"""Provider-free import of three user-supplied front-anchor candidates."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.atomic_write import atomic_write_bytes, atomic_write_json
from tella.media.bfl_front_anchor_orchestration import (
    CHARACTER_FINGERPRINT,
    CHARACTER_ID,
    OUTPUT_PREFIX,
    REQUIRED_BRANCH,
    RepositoryState,
    repository_state,
)


MAX_BYTES = 20_000_000
EXPECTED_SIZE = (768, 1024)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ManualImportBlocked(RuntimeError):
    pass


class MechanicalValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    png_signature: Literal[True]
    decoded: Literal[True]
    format_png: Literal[True]
    mime_image_png: Literal[True]
    exact_dimensions: Literal[True]
    non_animated: Literal[True]
    within_byte_limit: Literal[True]
    non_truncated: Literal[True]


class ManualCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: Literal["candidate_01", "candidate_02", "candidate_03"]
    order: Literal[1, 2, 3]
    image_filename: str
    source_sha256: str
    copied_sha256: str
    mime: Literal["image/png"]
    dimensions: tuple[Literal[768], Literal[1024]]
    byte_size: int = Field(gt=0, le=MAX_BYTES)
    duplicate_group: str | None = None
    mechanical_validation: MechanicalValidation
    semantic_review_status: Literal["pending_human_review"]
    provenance: Literal["user_supplied_local"]

    @field_validator("source_sha256", "copied_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("expected SHA256")
        return value

    @model_validator(mode="after")
    def unchanged(self) -> "ManualCandidate":
        if self.source_sha256 != self.copied_sha256:
            raise ValueError("manual candidate copy SHA256 mismatch")
        if self.image_filename != f"{self.candidate_id}.png" or self.order != int(self.candidate_id[-2:]):
            raise ValueError("manual candidate identity/order mismatch")
        return self


class ManualImportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    session_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", min_length=1, max_length=120)
    source_kind: Literal["manual_import"]
    character_id: Literal["practical_young_adult_male_teal_v1"]
    character_fingerprint: str
    canonical_spec_version: Literal[1]
    generation_spec_version: Literal[1]
    prompt_sha256: str
    provider_id: None = None
    provider_calls: Literal[0]
    external_calls: Literal[0]
    candidates: tuple[ManualCandidate, ManualCandidate, ManualCandidate]
    imported_timestamp: str
    session_state: Literal["awaiting_front_selection"]
    automatic_selection: Literal[False]
    selected_candidate_id: None = None
    human_approved: Literal[False]
    stage_b_requested: Literal[False]
    contact_sheet_kind: Literal["local_review_derivative"]

    @model_validator(mode="after")
    def exact_package(self) -> "ManualImportManifest":
        if self.character_fingerprint != CHARACTER_FINGERPRINT:
            raise ValueError("manual import fingerprint mismatch")
        if [row.candidate_id for row in self.candidates] != ["candidate_01", "candidate_02", "candidate_03"]:
            raise ValueError("manual import candidate order mismatch")
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        forbidden = ("://", "authorization", "cookie", "api_key", "access_key", "secret")
        if any(marker in serialized for marker in forbidden):
            raise ValueError("manual import manifest contains forbidden material")
        return self


SEMANTIC_CHECKS = (
    "one_character_only", "front_facing_chest_up_portrait", "complete_head_and_hair",
    "no_head_crop", "age_appearance_24_to_28", "softly_angular_face",
    "small_dark_eyes", "short_dark_side_swept_hair", "warm_medium_light_skin_tone",
    "muted_teal_top", "no_coral_clothing", "no_extra_person", "no_prop",
    "no_logo", "no_text", "no_watermark", "plausible_anatomy",
    "suitable_for_later_three_quarter_profile_and_full_body_derivation",
)


class ManualReviewTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    session_id: str
    candidates: tuple[dict[str, object], dict[str, object], dict[str, object]]
    semantic_checklist: dict[str, Literal["pending_human_review"]]
    selected_candidate_id: None = None
    selected_candidate_sha256: None = None
    human_approved: Literal[False]
    automatic_selection: Literal[False]
    approval_notes: Literal[""]
    approver_role: None = None
    approval_timestamp: None = None
    immutable_selection_sha256: None = None
    stage_b_allowed: Literal[False]


def validate_source(path: Path) -> tuple[bytes, str]:
    if path.is_symlink() or not path.is_file():
        raise ManualImportBlocked("candidate source must be a regular non-symlink file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        raise ManualImportBlocked("candidate source exceeds byte policy")
    content = path.read_bytes()
    if len(content) != size or not content.startswith(PNG_SIGNATURE):
        raise ManualImportBlocked("candidate source is not a complete PNG")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            if image.format != "PNG" or image.size != EXPECTED_SIZE:
                raise ManualImportBlocked("candidate PNG format or dimensions mismatch")
            if getattr(image, "n_frames", 1) != 1:
                raise ManualImportBlocked("animated candidate PNG is forbidden")
    except ManualImportBlocked:
        raise
    except Exception:
        raise ManualImportBlocked("candidate PNG decoding failed") from None
    return content, hashlib.sha256(content).hexdigest()


def import_candidates(
    *, repository_root: Path, session_id: str, sources: tuple[Path, Path, Path],
    character_id: str, character_fingerprint: str, canonical_spec_version: int,
    generation_spec_version: int, prompt: str, prompt_sha256: str,
    state_reader=repository_state,
    now=lambda: datetime.now(timezone.utc),
) -> Path:
    root = repository_root.resolve(strict=True)
    if not (root / ".git").exists():
        raise ManualImportBlocked("repository root is invalid")
    state: RepositoryState = state_reader(root)
    if state.branch != REQUIRED_BRANCH or not state.tracked_clean or not state.staged_zero:
        raise ManualImportBlocked("source-state policy failed")
    if character_id != CHARACTER_ID or character_fingerprint != CHARACTER_FINGERPRINT:
        raise ManualImportBlocked("canonical character identity mismatch")
    if canonical_spec_version != 1 or generation_spec_version != 1:
        raise ManualImportBlocked("character specification version mismatch")
    if hashlib.sha256(prompt.encode()).hexdigest() != prompt_sha256:
        raise ManualImportBlocked("front prompt SHA256 mismatch")
    if not session_id or len(session_id) > 120 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in session_id):
        raise ManualImportBlocked("unsafe session ID")
    relative = OUTPUT_PREFIX / session_id
    final = (root / relative).resolve()
    approved = (root / OUTPUT_PREFIX).resolve()
    if not final.is_relative_to(approved):
        raise ManualImportBlocked("manual import output escapes approved root")
    for parent in (final, *final.parents):
        if parent == approved.parent:
            break
        if parent.exists() and parent.is_symlink():
            raise ManualImportBlocked("manual import output contains symlink")
    if final.exists():
        raise ManualImportBlocked("manual import session already exists")
    validated = tuple(validate_source(path) for path in sources)

    approved.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{session_id}.", suffix=".tmp", dir=approved))
    try:
        digests = [item[1] for item in validated]
        repeated = {digest for digest in digests if digests.count(digest) > 1}
        group_by_digest = {digest: f"duplicate_{index:02d}" for index, digest in enumerate(
            sorted(repeated, key=lambda value: digests.index(value)), 1
        )}
        candidates = []
        mechanical = MechanicalValidation(
            png_signature=True, decoded=True, format_png=True, mime_image_png=True,
            exact_dimensions=True, non_animated=True, within_byte_limit=True,
            non_truncated=True,
        )
        for index, (content, digest) in enumerate(validated, 1):
            filename = f"candidate_{index:02d}.png"
            destination = temporary / filename
            atomic_write_bytes(destination, content)
            copied = hashlib.sha256(destination.read_bytes()).hexdigest()
            candidates.append(ManualCandidate(
                candidate_id=f"candidate_{index:02d}", order=index,
                image_filename=filename, source_sha256=digest, copied_sha256=copied,
                mime="image/png", dimensions=EXPECTED_SIZE, byte_size=len(content),
                duplicate_group=group_by_digest.get(digest),
                mechanical_validation=mechanical,
                semantic_review_status="pending_human_review",
                provenance="user_supplied_local",
            ))
        manifest = ManualImportManifest(
            schema_version=1, session_id=session_id, source_kind="manual_import",
            character_id=character_id, character_fingerprint=character_fingerprint,
            canonical_spec_version=1, generation_spec_version=1,
            prompt_sha256=prompt_sha256, provider_calls=0, external_calls=0,
            candidates=tuple(candidates), imported_timestamp=now().isoformat(),
            session_state="awaiting_front_selection", automatic_selection=False,
            human_approved=False, stage_b_requested=False,
            contact_sheet_kind="local_review_derivative",
        )
        _contact_sheet(tuple(temporary / row.image_filename for row in candidates), temporary / "contact_sheet.png")
        review = ManualReviewTemplate(
            schema_version=1, session_id=session_id,
            candidates=tuple({
                "candidate_id": row.candidate_id,
                "image_sha256": row.copied_sha256,
                "mechanical_validation_passed": True,
                "duplicate_group": row.duplicate_group,
                "semantic_review_status": "pending_human_review",
            } for row in candidates),
            semantic_checklist={name: "pending_human_review" for name in SEMANTIC_CHECKS},
            human_approved=False, automatic_selection=False, approval_notes="",
            stage_b_allowed=False,
        )
        atomic_write_json(temporary / "candidates_manifest.json", manifest.model_dump(mode="json"))
        atomic_write_json(temporary / "review_template.json", review.model_dump(mode="json"))
        os.replace(temporary, final)
        return final
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _contact_sheet(paths: tuple[Path, Path, Path], output: Path) -> None:
    canvas = Image.new("RGBA", (1536, 2048), (238, 240, 231, 255))
    positions = ((0, 0), (768, 0), (0, 1024))
    for path, position in zip(paths, positions, strict=True):
        with Image.open(path) as image:
            image.load()
            canvas.paste(image.convert("RGBA"), position)
    draw = ImageDraw.Draw(canvas)
    draw.text((800, 1080), "MANUAL FRONT CANDIDATE REVIEW", fill=(38, 51, 47, 255))
    draw.text((800, 1120), "NO CANDIDATE SELECTED", fill=(38, 51, 47, 255))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False, compress_level=9)
    atomic_write_bytes(output, buffer.getvalue())


__all__ = [
    "ManualCandidate", "ManualImportBlocked", "ManualImportManifest",
    "ManualReviewTemplate", "import_candidates", "validate_source",
]
