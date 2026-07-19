from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from tella.visual_generation.continuity import select_references
from tella.visual_generation.models import (
    CandidateMetadata,
    ProviderCapabilities,
    QCDecision,
    VisualQCResult,
)
from tella.visual_generation.orchestrator import (
    DRY_RUN_CAPABILITIES,
    live_gate_status,
    load_proof_plan,
    render_proof,
)
from tella.visual_generation.prompt_builder import build_generation_request, build_instruction
from tella.visual_generation.providers.base import validate_provider_capabilities
from tella.visual_generation.providers.existing import ExistingTellaProviderAdapter
from tella.visual_generation.qc import scores_meet_acceptance
from tella.visual_generation.references import (
    REFERENCE_FILES,
    ReferenceMissingError,
    resolve_reference_catalog,
    sha256_file,
)
from tella.visual_generation.style_bible import load_style_bible

ROOT = Path(__file__).parents[1]
PLAN_PATH = ROOT / "configs" / "visual_quality" / "four_scene_proof_v1.json"
STYLE_PATH = ROOT / "configs" / "visual_quality" / "soft_emotional_reference_v1.json"


def _references(tmp_path: Path) -> Path:
    root = tmp_path / "references"
    root.mkdir()
    for index, (filename, _, _) in enumerate(sorted(set(REFERENCE_FILES.values()))):
        Image.new("RGB", (90, 160), (40 + index * 20, 30, 30)).save(root / filename)
    return root


def _caps(*, edit: bool = True, multi: bool = True) -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_id="fake-reference",
        model="fake-v1",
        supports_text_to_image=True,
        supports_reference_images=True,
        supports_multiple_references=multi,
        supports_image_edit=edit,
        supports_seed=True,
        supports_9_16=True,
        max_reference_images=3 if multi else 1,
    )


def _qc(decision: QCDecision = QCDecision.PASS, *, repair: bool = False) -> VisualQCResult:
    score = 9.0 if decision is QCDecision.PASS else 8.0
    return VisualQCResult(
        style_coherence=score,
        character_identity=score,
        scene_meaning=score,
        composition=score,
        natural_interaction=score,
        anatomy=score,
        visual_appeal=score,
        score_source="human_review",
        decision=decision,
        notes="reduce the cup" if repair else "reviewed",
        repair_instructions=["reduce the oversized cup"] if repair else [],
        reviewer="test-reviewer",
    )


class FakeProvider:
    def __init__(self, *, edit: bool = True, multi: bool = True):
        self._caps = _caps(edit=edit, multi=multi)
        self.generate_calls = []
        self.edit_calls = []

    def capabilities(self):
        return self._caps

    def credentials_present(self):
        return True

    async def generate_scene(self, request, output_path):
        self.generate_calls.append(request)
        Image.new("RGB", (request.width, request.height), "#49332b").save(output_path)
        return _blank_metadata(output_path)

    async def edit_scene(self, source_path, request, output_path):
        self.edit_calls.append((source_path, request))
        Image.new("RGB", (request.width, request.height), "#584038").save(output_path)
        return _blank_metadata(output_path)


def _blank_metadata(path: Path) -> CandidateMetadata:
    return CandidateMetadata(
        provider="fake-reference",
        model="fake-v1",
        request_hash="0" * 64,
        reference_hashes=[],
        instruction_hash="0" * 64,
        seed=None,
        generation_attempt=1,
        output_path=path,
    )


def test_style_bible_and_four_scene_plan_parse():
    style = load_style_bible(STYLE_PATH)
    plan = load_proof_plan(PLAN_PATH)
    assert style.style_id == "soft_emotional_reference_v1"
    assert (style.canvas.width, style.canvas.height) == (1080, 1920)
    assert [scene.scene_id for scene in plan.scenes] == [f"scene_{i:02d}" for i in range(1, 5)]


def test_reference_root_missing_lists_exact_files(tmp_path):
    with pytest.raises(ReferenceMissingError) as error:
        resolve_reference_catalog(tmp_path / "missing")
    for filename, _, _ in REFERENCE_FILES.values():
        assert filename in str(error.value)


def test_reference_hash_is_deterministic(tmp_path):
    path = tmp_path / "ref.png"
    path.write_bytes(b"stable reference bytes")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha256_file(path) == expected == sha256_file(path)


def test_prompt_builder_is_deterministic_and_provider_neutral(tmp_path):
    style = load_style_bible(STYLE_PATH)
    scene = load_proof_plan(PLAN_PATH).scenes[2]
    catalog = resolve_reference_catalog(_references(tmp_path))
    pack = select_references(scene, catalog, DRY_RUN_CAPABILITIES)
    first = build_generation_request(
        scene, style, pack, candidate_index=1, attempt=1, seed=13
    )
    second = build_generation_request(
        scene, style, pack, candidate_index=1, attempt=1, seed=13
    )
    assert first == second
    assert "CURRENT SCENE MEANING" in first.instruction
    assert "cloudflare" not in first.instruction.lower()
    assert build_instruction(scene, style) == build_instruction(scene, style)


def test_reference_selection_for_all_scenes(tmp_path):
    plan = load_proof_plan(PLAN_PATH)
    catalog = resolve_reference_catalog(_references(tmp_path))
    accepted = tmp_path / "scene_01_accepted.png"
    Image.new("RGB", (90, 160)).save(accepted)
    scene_1 = select_references(plan.scenes[0], catalog, _caps())
    scene_2 = select_references(plan.scenes[1], catalog, _caps())
    scene_3 = select_references(
        plan.scenes[2], catalog, _caps(), accepted_scenes={"scene_01": accepted}
    )
    scene_4 = select_references(
        plan.scenes[3], catalog, _caps(), accepted_scenes={"scene_01": accepted}
    )
    assert scene_1.references[0].role == "female_identity_anchor"
    assert scene_2.references[0].role == "couple_identity_anchor"
    assert any(item.role == "daily_vignette_reference" for item in scene_3.references)
    assert any(item.role == "scene_01_accepted_continuity" for item in scene_3.references)
    assert not any("scene_02" in item.role for item in scene_3.references)
    assert any(item.role == "emotional_metaphor_reference" for item in scene_4.references)
    assert any(item.role == "scene_01_accepted_continuity" for item in scene_4.references)


def test_single_reference_provider_uses_highest_priority_anchor(tmp_path):
    plan = load_proof_plan(PLAN_PATH)
    catalog = resolve_reference_catalog(_references(tmp_path))
    selected = select_references(plan.scenes[1], catalog, _caps(multi=False))
    assert [item.role for item in selected.references] == ["couple_identity_anchor"]


def test_provider_capability_mismatch_and_live_gate_are_explicit():
    broken = ProviderCapabilities(
        provider_id="text-only",
        model="model",
        supports_text_to_image=True,
        supports_reference_images=False,
        supports_multiple_references=False,
        supports_image_edit=False,
        supports_seed=True,
        supports_9_16=True,
        max_reference_images=0,
    )
    with pytest.raises(RuntimeError, match="reference images"):
        validate_provider_capabilities(broken)
    assert live_gate_status(
        references_available=True,
        capabilities=broken,
        credentials_present=True,
        live_opt_in=True,
    ) == "LIVE_VISUAL_ACCEPTANCE_BLOCKED_PROVIDER_CAPABILITY"
    assert live_gate_status(
        references_available=False,
        capabilities=_caps(),
        credentials_present=True,
        live_opt_in=True,
    ) == "LIVE_VISUAL_ACCEPTANCE_BLOCKED_REFERENCE_MISSING"
    assert live_gate_status(
        references_available=True,
        capabilities=_caps(),
        credentials_present=False,
        live_opt_in=True,
    ) == "LIVE_VISUAL_ACCEPTANCE_BLOCKED_CREDENTIAL_MISSING"
    assert live_gate_status(
        references_available=True,
        capabilities=_caps(),
        credentials_present=True,
        live_opt_in=False,
    ) == "LIVE_VISUAL_ACCEPTANCE_NOT_RUN_OPT_IN_REQUIRED"


def test_existing_cloudflare_adapter_reports_actual_proof_capabilities():
    capabilities = ExistingTellaProviderAdapter().capabilities()
    assert capabilities.provider_id == "cloudflare"
    assert capabilities.supports_text_to_image is True
    assert capabilities.supports_reference_images is False
    assert capabilities.supports_multiple_references is False
    assert capabilities.supports_image_edit is False
    assert capabilities.supports_seed is True
    assert capabilities.supports_9_16 is False


def test_qc_thresholds_fail_closed():
    scene = load_proof_plan(PLAN_PATH).scenes[2]
    assert scores_meet_acceptance(_qc(), scene)
    low = _qc().model_copy(update={"natural_interaction": 7.9})
    assert not scores_meet_acceptance(low, scene)
    heuristic = _qc().model_copy(update={"score_source": "heuristic"})
    assert scores_meet_acceptance(heuristic, scene)


@pytest.mark.asyncio
async def test_dry_run_writes_isolated_requests_and_makes_zero_provider_calls(tmp_path):
    provider = FakeProvider()
    summary = await render_proof(
        plan_path=PLAN_PATH,
        style_path=STYLE_PATH,
        reference_root=_references(tmp_path),
        out_root=tmp_path / "out",
        job_id="dry-run-test",
        dry_run=True,
        provider=provider,
    )
    assert provider.generate_calls == []
    assert provider.edit_calls == []
    assert summary["external_calls_made"] == 0
    job = tmp_path / "out" / "visual_quality_v1" / "dry-run-test"
    assert (job / "summary.json").is_file()
    assert all((job / f"scene_{i:02d}" / "request.json").is_file() for i in range(1, 5))
    assert not list(ROOT.glob("candidate_*.png"))


@pytest.mark.asyncio
async def test_live_generation_is_disabled_without_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("TELLA_VISUAL_QUALITY_LIVE", raising=False)
    provider = FakeProvider()
    with pytest.raises(RuntimeError, match="OPT_IN_REQUIRED"):
        await render_proof(
            plan_path=PLAN_PATH,
            style_path=STYLE_PATH,
            reference_root=_references(tmp_path),
            out_root=tmp_path / "out",
            job_id="blocked",
            dry_run=False,
            provider=provider,
        )
    assert provider.generate_calls == []


@pytest.mark.asyncio
async def test_regeneration_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    provider = FakeProvider(edit=False)
    summary = await render_proof(
        plan_path=PLAN_PATH,
        style_path=STYLE_PATH,
        reference_root=_references(tmp_path),
        out_root=tmp_path / "out",
        job_id="bounded",
        dry_run=False,
        provider=provider,
        qc_evaluator=lambda _scene, _path: _qc(QCDecision.REGENERATE),
    )
    assert len(provider.generate_calls) == 3
    assert provider.edit_calls == []
    assert summary["results"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_minor_repair_requires_edit_capability(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    provider = FakeProvider(edit=False)
    await render_proof(
        plan_path=PLAN_PATH,
        style_path=STYLE_PATH,
        reference_root=_references(tmp_path),
        out_root=tmp_path / "out",
        job_id="no-edit",
        dry_run=False,
        provider=provider,
        qc_evaluator=lambda _scene, _path: _qc(QCDecision.MINOR_REPAIR, repair=True),
    )
    assert len(provider.generate_calls) == 3
    assert provider.edit_calls == []


@pytest.mark.asyncio
async def test_minor_repair_is_bounded_and_preserves_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    provider = FakeProvider(edit=True)
    decisions = [_qc(QCDecision.MINOR_REPAIR, repair=True), _qc()]

    def evaluate(_scene, _path):
        return decisions.pop(0) if decisions else _qc()

    summary = await render_proof(
        plan_path=PLAN_PATH,
        style_path=STYLE_PATH,
        reference_root=_references(tmp_path),
        out_root=tmp_path / "out",
        job_id="repair",
        dry_run=False,
        provider=provider,
        qc_evaluator=evaluate,
    )
    assert len(provider.generate_calls) == 4
    assert len(provider.edit_calls) == 1
    assert provider.edit_calls[0][1].preserve_existing is True
    assert "Preserve all other composition" in provider.edit_calls[0][1].instruction
    assert summary["results"][0]["repair_attempts"] == 1


@pytest.mark.asyncio
async def test_accepted_scene_one_is_supplementary_for_later_scenes(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    provider = FakeProvider()
    summary = await render_proof(
        plan_path=PLAN_PATH,
        style_path=STYLE_PATH,
        reference_root=_references(tmp_path),
        out_root=tmp_path / "out",
        job_id="accepted-sequence",
        dry_run=False,
        provider=provider,
        qc_evaluator=lambda _scene, _path: _qc(),
    )
    assert summary["complete"] is True
    assert len(provider.generate_calls) == 4
    assert any(
        ref.role == "scene_01_accepted_continuity"
        for ref in provider.generate_calls[2].references
    )
    assert any(
        ref.role == "scene_01_accepted_continuity"
        for ref in provider.generate_calls[3].references
    )
    assert (tmp_path / "out" / "visual_quality_v1" / "accepted-sequence" / "contact_sheet.png").is_file()


def test_job_id_cannot_escape_isolated_output(tmp_path):
    with pytest.raises(ValueError, match="job-id"):
        asyncio.run(
            render_proof(
                plan_path=PLAN_PATH,
                style_path=STYLE_PATH,
                reference_root=_references(tmp_path),
                out_root=tmp_path / "out",
                job_id="../escape",
                dry_run=True,
            )
        )
