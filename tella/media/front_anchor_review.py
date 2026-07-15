"""Local QC and human-selection boundary for front-anchor candidates."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.atomic_write import atomic_write_bytes, atomic_write_json
from tella.media.character_reference_bootstrap import HumanFrontSelectionRecord
from tella.media.front_anchor_harness import FrontHarnessPlan


class FrontVisualSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    one_character: bool = False
    chest_up_front_view: bool = False
    complete_head_and_hair: bool = False
    no_crop_through_head: bool = False
    no_extra_person: bool = False
    no_text_watermark_or_logo: bool = False
    teal_top: bool = False
    dark_side_swept_hair: bool = False
    expected_background: bool = False
    no_prop: bool = False
    no_obvious_duplicate_or_malformed_anatomy: bool = False
    no_coral_clothing: bool = False


class FrontCandidateQC(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    candidate_number: Literal[1, 2, 3]
    output_path: Path
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    request_id: str | None = Field(default=None, max_length=200)
    seed: int | None = None
    image_sha256: str
    mime_type: str = Field(min_length=1, max_length=40)
    width: int = Field(ge=0, le=8192)
    height: int = Field(ge=0, le=8192)
    byte_size: int = Field(ge=0, le=20_000_000)
    maximum_bytes: int = Field(default=20_000_000, ge=1, le=20_000_000)
    decoded_png: bool
    signals: FrontVisualSignals
    duplicate_response: bool = False
    hard_failures: tuple[str, ...] = ()
    passed: bool
    eligible_for_selection: bool

    @field_validator("image_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized

    @model_validator(mode="after")
    def eligibility_is_truthful(self) -> "FrontCandidateQC":
        expected_failures: set[str] = set()
        if self.duplicate_response:
            expected_failures.add("duplicate_response_bytes")
        for name, passed in self.signals.model_dump().items():
            if not passed:
                expected_failures.add(name)
        if not self.decoded_png:
            expected_failures.add("png_decode")
        if self.width != 768 or self.height != 1024:
            expected_failures.add("dimensions")
        if self.mime_type != "image/png":
            expected_failures.add("mime_type")
        if self.byte_size == 0:
            expected_failures.add("missing_or_empty")
        if self.byte_size > self.maximum_bytes:
            expected_failures.add("byte_size")
        expected_passed = not expected_failures
        if self.passed != expected_passed or self.eligible_for_selection != expected_passed:
            raise ValueError("candidate QC eligibility does not match hard gates")
        if set(self.hard_failures) != expected_failures or len(self.hard_failures) != len(expected_failures):
            raise ValueError("candidate QC failure reasons are incomplete")
        return self


class FrontCandidateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    candidate_number: Literal[1, 2, 3]
    output_path: Path
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    request_id: str | None = Field(default=None, max_length=200)
    seed: int | None = None
    image_sha256: str
    mime_type: str = Field(min_length=1, max_length=40)
    width: int = Field(ge=0, le=8192)
    height: int = Field(ge=0, le=8192)
    byte_size: int = Field(ge=0, le=20_000_000)

    @field_validator("image_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized

    @model_validator(mode="after")
    def no_remote_material(self) -> "FrontCandidateArtifact":
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in serialized for marker in ("://", "authorization", "bearer ", "api_key", "secret_access_key")):
            raise ValueError("candidate artifact must not contain URLs or credentials")
        return self


class FrontCandidateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    character_fingerprint: str
    provider: Literal["cloudflare", "bfl_flux_1_1_pro_front_anchor"]
    model: str = Field(min_length=1, max_length=160)
    candidates: tuple[FrontCandidateArtifact, ...]
    qc_results: tuple[FrontCandidateQC, ...]
    submission_count: Literal[3]
    transport_attempt_count: Literal[3]
    automatic_retries: Literal[0]
    fallbacks: Literal[0]
    selected_candidate_id: None = None
    contact_sheet_path: Path
    review_template_path: Path

    @field_validator("character_fingerprint")
    @classmethod
    def digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized

    @model_validator(mode="after")
    def exact_three_and_no_selection(self) -> "FrontCandidateManifest":
        if len(self.candidates) != 3 or len(self.qc_results) != 3:
            raise ValueError("front manifest requires exactly three candidates")
        if len({item.candidate_id for item in self.candidates}) != 3:
            raise ValueError("front candidate IDs must be unique")
        if any(item.candidate_id != qc.candidate_id for item, qc in zip(self.candidates, self.qc_results, strict=True)):
            raise ValueError("candidate and QC records must remain aligned")
        if self.selected_candidate_id is not None:
            raise ValueError("front candidates cannot be auto-selected")
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in serialized for marker in ("://", "authorization", "bearer ", "api_key", "secret_access_key")):
            raise ValueError("front manifest must not contain URLs or credentials")
        return self


class FrontReviewCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    image_sha256: str
    eligible_for_selection: bool
    qc_passed: bool


class FrontReviewTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    session_id: str
    candidates: tuple[FrontReviewCandidate, ...]
    checklist: dict[str, bool]
    selected_candidate_id: str | None = None
    selected_candidate_sha256: str | None = None
    human_approved: bool = False
    approver_role: str | None = None
    selection_timestamp: datetime | None = None
    review_notes: str = ""
    immutable_selection_sha256: str | None = None

    @model_validator(mode="after")
    def selection_lifecycle(self) -> "FrontReviewTemplate":
        if self.selected_candidate_id is None:
            if self.human_approved or self.immutable_selection_sha256 is not None:
                raise ValueError("unselected review template cannot be approved")
            return self
        matches = [item for item in self.candidates if item.candidate_id == self.selected_candidate_id]
        if len(matches) != 1 or not matches[0].eligible_for_selection or not matches[0].qc_passed:
            raise ValueError("selected candidate must be exactly one eligible QC-passing candidate")
        if self.selected_candidate_sha256 != matches[0].image_sha256:
            raise ValueError("selection SHA256 does not match candidate")
        if not self.human_approved or not self.approver_role or self.selection_timestamp is None:
            raise ValueError("human selection requires approval role and timestamp")
        if self.immutable_selection_sha256 is None:
            raise ValueError("human selection record SHA256 is required")
        return self


def run_candidate_qc(
    *,
    candidate_id: str,
    candidate_number: int,
    path: Path,
    provider: str,
    model: str,
    request_id: str | None,
    seed: int | None,
    signals: FrontVisualSignals,
    previous_sha256: set[str] | None = None,
    maximum_bytes: int = 20_000_000,
) -> FrontCandidateQC:
    content = path.read_bytes() if path.is_file() else b""
    digest = hashlib.sha256(content).hexdigest()
    byte_size = len(content)
    decoded = False
    width, height = 0, 0
    mime = "image/png"
    try:
        with Image.open(path) as image:
            image.load()
            decoded = image.format == "PNG"
            mime = "image/png" if image.format == "PNG" else "image/unknown"
            width, height = image.size
    except (OSError, ValueError):
        pass
    failures: list[str] = []
    if byte_size == 0:
        failures.append("missing_or_empty")
    if byte_size > maximum_bytes:
        failures.append("byte_size")
    duplicate = digest in (previous_sha256 or set())
    if duplicate:
        failures.append("duplicate_response_bytes")
    if not decoded:
        failures.append("png_decode")
    if (width, height) != (768, 1024):
        failures.append("dimensions")
    if mime != "image/png":
        failures.append("mime_type")
    signals_failures = [name for name, passed in signals.model_dump().items() if not passed]
    failures.extend(signals_failures)
    failures_tuple = tuple(dict.fromkeys(failures))
    passed = not failures_tuple
    return FrontCandidateQC(
        candidate_id=candidate_id,
        candidate_number=candidate_number,
        output_path=path,
        provider=provider,
        model=model,
        request_id=request_id,
        seed=seed,
        image_sha256=digest,
        mime_type=mime,
        width=width,
        height=height,
        byte_size=byte_size,
        maximum_bytes=maximum_bytes,
        decoded_png=decoded,
        signals=signals,
        duplicate_response=duplicate,
        hard_failures=failures_tuple,
        passed=passed,
        eligible_for_selection=passed,
    )


def build_candidate_manifest(
    *, plan: FrontHarnessPlan, qcs: tuple[FrontCandidateQC, ...], contact_sheet_path: Path, review_template_path: Path
) -> FrontCandidateManifest:
    if len(qcs) != 3:
        raise ValueError("exactly three candidate QC records are required")
    candidates = tuple(
        FrontCandidateArtifact(
            candidate_id=qc.candidate_id,
            candidate_number=qc.candidate_number,
            output_path=qc.output_path,
            provider=qc.provider,
            model=qc.model,
            request_id=qc.request_id,
            seed=qc.seed,
            image_sha256=qc.image_sha256,
            mime_type=qc.mime_type,
            width=qc.width,
            height=qc.height,
            byte_size=qc.byte_size,
        )
        for qc in qcs
    )
    return FrontCandidateManifest(
        schema_version=1,
        session_id=plan.session_id,
        character_fingerprint=plan.character_fingerprint,
        provider=plan.provider_id,
        model=plan.model,
        candidates=candidates,
        qc_results=qcs,
        submission_count=3,
        transport_attempt_count=3,
        automatic_retries=0,
        fallbacks=0,
        contact_sheet_path=contact_sheet_path,
        review_template_path=review_template_path,
    )


def make_review_template(manifest: FrontCandidateManifest) -> FrontReviewTemplate:
    checklist = {
        "face_suitable_for_recurring_scenes": False,
        "age_appearance_24_to_28": False,
        "stable_hair_silhouette": False,
        "neutral_expression": False,
        "recognizable_simple_face": False,
        "outfit_correct": False,
        "anatomy_correct": False,
        "suitable_for_side_and_full_body_generation": False,
    }
    return FrontReviewTemplate(
        schema_version=1,
        session_id=manifest.session_id,
        candidates=tuple(
            FrontReviewCandidate(
                candidate_id=item.candidate_id,
                image_sha256=item.image_sha256,
                eligible_for_selection=qc.eligible_for_selection,
                qc_passed=qc.passed,
            )
            for item, qc in zip(manifest.candidates, manifest.qc_results, strict=True)
        ),
        checklist=checklist,
    )


def record_human_selection(
    template: FrontReviewTemplate,
    *,
    candidate_id: str,
    approver_role: str,
    selection_timestamp: datetime,
    review_notes: str,
) -> FrontReviewTemplate:
    if template.selected_candidate_id is not None:
        raise ValueError("front selection is already immutable")
    if not all(template.checklist.values()):
        raise ValueError("all human front-anchor checklist items are required")
    candidate = next((item for item in template.candidates if item.candidate_id == candidate_id), None)
    if candidate is None or not candidate.eligible_for_selection or not candidate.qc_passed:
        raise ValueError("failed or unknown candidate cannot be selected")
    payload = {
        "schema_version": template.schema_version,
        "session_id": template.session_id,
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": candidate.image_sha256,
        "approver_role": approver_role,
        "selection_timestamp": selection_timestamp.isoformat(),
        "review_notes": review_notes,
    }
    immutable = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return template.model_copy(
        update={
            "selected_candidate_id": candidate.candidate_id,
            "selected_candidate_sha256": candidate.image_sha256,
            "human_approved": True,
            "approver_role": approver_role,
            "selection_timestamp": selection_timestamp,
            "review_notes": review_notes,
            "immutable_selection_sha256": immutable,
        }
    )


def to_bootstrap_selection(template: FrontReviewTemplate) -> HumanFrontSelectionRecord:
    if not template.human_approved or template.selected_candidate_id is None:
        raise RuntimeError("Stage B remains blocked until front selection is approved")
    return HumanFrontSelectionRecord(
        selected_candidate_id=template.selected_candidate_id,
        selected_image_sha256=template.selected_candidate_sha256,
        selector_role=template.approver_role,
        selected_at=template.selection_timestamp,
        decision="bootstrap_identity_anchor",
    )


def build_contact_sheet(
    *,
    manifest: FrontCandidateManifest,
    output_path: Path,
) -> str:
    if len(manifest.candidates) != 3:
        raise ValueError("contact sheet requires exactly three candidates")
    canvas = Image.new("RGBA", (1536, 2048), (238, 240, 231, 255))
    positions = ((0, 0), (768, 0), (0, 1024))
    for candidate, position in zip(manifest.candidates, positions, strict=True):
        with Image.open(candidate.output_path) as image:
            if image.format != "PNG" or image.size != (768, 1024):
                raise ValueError("contact sheet candidate does not meet exact PNG dimensions")
            canvas.paste(image.convert("RGBA"), position)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((768, 1024, 1535, 2047), fill=(238, 240, 231, 255))
    draw.text((800, 1080), "FRONT ANCHOR REVIEW ONLY", fill=(38, 51, 47, 255))
    for index, qc in enumerate(manifest.qc_results, 1):
        status = "PASS" if qc.passed else "FAIL"
        draw.text((800, 1120 + index * 36), f"candidate_{index:02d}: {status}", fill=(38, 51, 47, 255))
    output = Path(output_path)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False, compress_level=9)
    content = buffer.getvalue()
    atomic_write_bytes(output, content)
    return hashlib.sha256(content).hexdigest()


def write_review_template(template: FrontReviewTemplate, output_path: Path) -> Path:
    return atomic_write_json(output_path, template.model_dump(mode="json"), ensure_ascii=False)


def write_candidate_manifest(manifest: FrontCandidateManifest, output_path: Path) -> Path:
    return atomic_write_json(output_path, manifest.model_dump(mode="json"), ensure_ascii=False)


__all__ = [
    "FrontCandidateArtifact",
    "FrontCandidateManifest",
    "FrontCandidateQC",
    "FrontReviewCandidate",
    "FrontReviewTemplate",
    "FrontVisualSignals",
    "build_candidate_manifest",
    "build_contact_sheet",
    "make_review_template",
    "record_human_selection",
    "run_candidate_qc",
    "to_bootstrap_selection",
    "write_candidate_manifest",
    "write_review_template",
]
