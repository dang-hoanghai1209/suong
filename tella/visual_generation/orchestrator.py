"""Sequential orchestration for the isolated four-scene proof."""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Awaitable

from PIL import Image, ImageDraw

from .continuity import select_references
from .models import (
    CandidateMetadata,
    ProofPlan,
    ProviderCapabilities,
    QCDecision,
    SceneBrief,
    SceneResult,
    VisualQCResult,
)
from .prompt_builder import build_generation_request, instruction_hash, request_hash
from .providers.base import SceneImageProvider, validate_provider_capabilities
from .qc import (
    human_review_template,
    scores_meet_acceptance,
    unavailable_visual_qc,
    validate_candidate_structure,
)
from .references import resolve_reference_catalog
from .repair import build_repair_request
from .style_bible import load_style_bible, write_style_snapshot

QCEvaluator = Callable[
    [SceneBrief, Path], VisualQCResult | Awaitable[VisualQCResult]
]

DRY_RUN_CAPABILITIES = ProviderCapabilities(
    provider_id="provider-neutral-dry-run",
    model="unselected",
    supports_text_to_image=True,
    supports_reference_images=True,
    supports_multiple_references=True,
    supports_image_edit=True,
    supports_seed=True,
    supports_9_16=True,
    max_reference_images=3,
)


def load_proof_plan(path: Path | str) -> ProofPlan:
    return ProofPlan.model_validate_json(Path(path).read_text(encoding="utf-8"))


def live_gate_status(
    *,
    references_available: bool,
    capabilities: ProviderCapabilities,
    credentials_present: bool,
    live_opt_in: bool,
) -> str:
    if not references_available:
        return "LIVE_VISUAL_ACCEPTANCE_BLOCKED_REFERENCE_MISSING"
    try:
        validate_provider_capabilities(capabilities)
    except RuntimeError:
        return "LIVE_VISUAL_ACCEPTANCE_BLOCKED_PROVIDER_CAPABILITY"
    if not credentials_present:
        return "LIVE_VISUAL_ACCEPTANCE_BLOCKED_CREDENTIAL_MISSING"
    if not live_opt_in:
        return "LIVE_VISUAL_ACCEPTANCE_NOT_RUN_OPT_IN_REQUIRED"
    return "LIVE_VISUAL_ACCEPTANCE_READY"


async def render_proof(
    *,
    plan_path: Path,
    style_path: Path,
    reference_root: Path,
    out_root: Path,
    job_id: str,
    dry_run: bool,
    provider: SceneImageProvider | None = None,
    qc_evaluator: QCEvaluator | None = None,
    scene_id: str | None = None,
    seed_override: int | None = None,
    tier: str | None = None,
    intended_usage_class: str | None = None,
    chain_accepted_scenes: bool | None = None,
) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", job_id):
        raise ValueError("job-id must be a safe 1-80 character filename component")
    tier = tier or getattr(provider, "tier", None)
    intended_usage_class = intended_usage_class or getattr(
        provider, "intended_usage_class", None
    )
    effective_chaining = tier is None if chain_accepted_scenes is None else chain_accepted_scenes
    plan = load_proof_plan(plan_path)
    if scene_id is not None:
        selected = [scene for scene in plan.scenes if scene.scene_id == scene_id]
        if not selected:
            raise ValueError(f"unknown scene: {scene_id}")
        plan = plan.model_copy(
            update={"candidate_count": 1, "max_generation_attempts_per_scene": 1}
        )
    else:
        selected = plan.scenes
    style = load_style_bible(style_path)
    catalog = resolve_reference_catalog(reference_root)

    job_dir = (Path(out_root).resolve() / "visual_quality_v1" / job_id).resolve()
    expected_parent = (Path(out_root).resolve() / "visual_quality_v1").resolve()
    if job_dir.parent != expected_parent:
        raise ValueError("output job escaped isolated visual_quality_v1 root")
    job_dir.mkdir(parents=True, exist_ok=True)

    capabilities = (
        provider.capabilities()
        if provider is not None
        else DRY_RUN_CAPABILITIES
        if dry_run
        else _require_provider(provider).capabilities()
    )
    if not dry_run:
        gate = live_gate_status(
            references_available=True,
            capabilities=capabilities,
            credentials_present=_require_provider(provider).credentials_present(),
            live_opt_in=os.environ.get("TELLA_VISUAL_QUALITY_LIVE") == "1",
        )
        if gate != "LIVE_VISUAL_ACCEPTANCE_READY":
            raise RuntimeError(gate)

    _write_json(job_dir / "plan.json", plan.model_dump(mode="json"))
    write_style_snapshot(style, job_dir / "style_bible.snapshot.json")
    _write_json(
        job_dir / "references.json",
        {role: item.model_dump(mode="json") for role, item in catalog.items()},
    )

    accepted_scenes: dict[str, Path] = {}
    results: list[SceneResult] = []
    all_qc: dict[str, list[dict[str, object]]] = {}
    for scene in selected:
        scene_dir = job_dir / scene.scene_id
        scene_dir.mkdir(parents=True, exist_ok=True)
        reference_pack = select_references(
            scene,
            catalog,
            capabilities,
            accepted_scenes=accepted_scenes if effective_chaining else {},
        )
        first_request = build_generation_request(
            scene,
            style,
            reference_pack,
            candidate_index=1,
            attempt=1,
            seed=(
                seed_override
                if seed_override is not None and capabilities.supports_seed
                else 10_000 + int(scene.scene_id[-2:]) * 101
                if capabilities.supports_seed
                else None
            ),
        )
        _write_json(scene_dir / "request.json", first_request.model_dump(mode="json"))
        review_path = scene_dir / "human_review.json"
        if not review_path.exists():
            _write_json(review_path, human_review_template())

        if dry_run:
            results.append(
                SceneResult(
                    scene_id=scene.scene_id,
                    status="dry_run",
                    references_used=reference_pack.references,
                    generation_attempts=0,
                    repair_attempts=0,
                    provider=capabilities.provider_id,
                    model=capabilities.model,
                    metadata={
                        "tier": tier,
                        "intended_usage_class": intended_usage_class,
                        "provider": capabilities.provider_id,
                        "model": capabilities.model,
                        "effective_steps": getattr(provider, "steps", None),
                        "timeout_seconds": getattr(provider, "timeout_seconds", None),
                        "logical_request_hash": request_hash(first_request),
                        "reference_hashes": [
                            item.sha256 for item in first_request.references
                        ],
                        "seed": first_request.seed,
                    },
                )
            )
            continue

        result, qc_records = await _render_scene(
            scene=scene,
            style_width=style.canvas.width,
            style_height=style.canvas.height,
            request=first_request,
            scene_dir=scene_dir,
            plan=plan,
            provider=_require_provider(provider),
            qc_evaluator=qc_evaluator,
            tier=tier,
            intended_usage_class=intended_usage_class,
        )
        results.append(result)
        all_qc[scene.scene_id] = qc_records
        _write_json(scene_dir / "qc.json", qc_records)
        if result.status == "accepted" and result.accepted_path:
            accepted_scenes[scene.scene_id] = result.accepted_path
        else:
            break

    complete = len(results) == len(selected) and all(
        item.status == "accepted" for item in results
    )
    summary: dict[str, object] = {
        "job_id": job_id,
        "dry_run": dry_run,
        "tier": tier,
        "intended_usage_class": intended_usage_class,
        "accepted_scene_chaining": effective_chaining,
        "provider": capabilities.provider_id,
        "model": capabilities.model,
        "selected_scenes": [scene.scene_id for scene in selected],
        "planned_initial_candidates": len(selected) * plan.candidate_count,
        "maximum_generation_calls": len(selected) * plan.max_generation_attempts_per_scene,
        "maximum_edit_calls": (
            len(selected)
            * plan.max_generation_attempts_per_scene
            * plan.max_repairs_per_candidate
            if capabilities.supports_image_edit
            else 0
        ),
        "results": [item.model_dump(mode="json") for item in results],
        "complete": complete,
        "human_review_required": True,
        "external_calls_made": 0 if dry_run else sum(item.generation_attempts + item.repair_attempts for item in results),
    }
    if complete and len(results) > 1:
        summary["contact_sheet"] = str(_build_contact_sheet(results, job_dir))
    _write_json(job_dir / "summary.json", summary)
    return summary


async def _render_scene(
    *,
    scene: SceneBrief,
    style_width: int,
    style_height: int,
    request,
    scene_dir: Path,
    plan: ProofPlan,
    provider: SceneImageProvider,
    qc_evaluator: QCEvaluator | None,
    tier: str | None,
    intended_usage_class: str | None,
) -> tuple[SceneResult, list[dict[str, object]]]:
    capabilities = provider.capabilities()
    generation_attempts = 0
    repair_attempts = 0
    qc_records: list[dict[str, object]] = []
    evaluator = qc_evaluator or (lambda _scene, _path: unavailable_visual_qc())

    for attempt in range(1, plan.max_generation_attempts_per_scene + 1):
        candidate_index = min(attempt, plan.candidate_count)
        current = request.model_copy(update={"attempt": attempt, "candidate_index": candidate_index})
        output = scene_dir / f"candidate_{attempt:02d}{_provider_extension(provider)}"
        started = time.monotonic()
        metadata = await provider.generate_scene(current, output)
        generation_attempts += 1
        output = metadata.output_path
        metadata = _normalized_metadata(
            metadata,
            current,
            output,
            capabilities,
            started,
            tier=tier,
            intended_usage_class=intended_usage_class,
        )
        _write_json(output.with_suffix(".metadata.json"), metadata.model_dump(mode="json"))
        structural = validate_candidate_structure(output, width=style_width, height=style_height)
        qc = await _evaluate(evaluator, scene, output)
        if structural:
            qc = qc.model_copy(
                update={
                    "decision": QCDecision.REGENERATE,
                    "notes": "; ".join(structural + ([qc.notes] if qc.notes else [])),
                }
            )
        qc_records.append(qc.model_dump(mode="json"))
        if scores_meet_acceptance(qc, scene):
            accepted = scene_dir / f"accepted{output.suffix}"
            shutil.copy2(output, accepted)
            return (
                SceneResult(
                    scene_id=scene.scene_id,
                    status="accepted",
                    references_used=current.references,
                    generation_attempts=generation_attempts,
                    repair_attempts=repair_attempts,
                    accepted_candidate=attempt,
                    accepted_path=accepted.resolve(),
                    provider=capabilities.provider_id,
                    model=capabilities.model,
                ),
                qc_records,
            )

        if (
            qc.decision is QCDecision.MINOR_REPAIR
            and capabilities.supports_image_edit
            and plan.max_repairs_per_candidate > 0
        ):
            repair_source = output
            repair_qc = qc
            for repair_index in range(1, plan.max_repairs_per_candidate + 1):
                repair = build_repair_request(current, repair_qc, attempt=attempt)
                repaired = scene_dir / (
                    f"candidate_{attempt:02d}_repair_{repair_index:02d}"
                    f"{_provider_extension(provider)}"
                )
                repair_started = time.monotonic()
                repair_metadata = await provider.edit_scene(repair_source, repair, repaired)
                repair_attempts += 1
                repair_metadata = _normalized_metadata(
                    repair_metadata,
                    repair,
                    repaired,
                    capabilities,
                    repair_started,
                    tier=tier,
                    intended_usage_class=intended_usage_class,
                )
                _write_json(
                    repaired.with_suffix(".metadata.json"),
                    repair_metadata.model_dump(mode="json"),
                )
                repair_qc = await _evaluate(evaluator, scene, repaired)
                repair_structural = validate_candidate_structure(
                    repaired, width=style_width, height=style_height
                )
                if repair_structural:
                    repair_qc = repair_qc.model_copy(
                        update={
                            "decision": QCDecision.REGENERATE,
                            "notes": "; ".join(repair_structural),
                        }
                    )
                qc_records.append(repair_qc.model_dump(mode="json"))
                if scores_meet_acceptance(repair_qc, scene):
                    accepted = scene_dir / "accepted.png"
                    shutil.copy2(repaired, accepted)
                    return (
                        SceneResult(
                            scene_id=scene.scene_id,
                            status="accepted",
                            references_used=current.references,
                            generation_attempts=generation_attempts,
                            repair_attempts=repair_attempts,
                            accepted_candidate=attempt,
                            accepted_path=accepted.resolve(),
                            provider=capabilities.provider_id,
                            model=capabilities.model,
                        ),
                        qc_records,
                    )
                if repair_qc.decision is not QCDecision.MINOR_REPAIR:
                    break
                repair_source = repaired

    return (
        SceneResult(
            scene_id=scene.scene_id,
            status="failed",
            references_used=request.references,
            generation_attempts=generation_attempts,
            repair_attempts=repair_attempts,
            provider=capabilities.provider_id,
            model=capabilities.model,
            failure_reason="bounded attempts exhausted without accepted visual QC",
        ),
        qc_records,
    )


async def _evaluate(evaluator: QCEvaluator, scene: SceneBrief, path: Path) -> VisualQCResult:
    result = evaluator(scene, path)
    if inspect.isawaitable(result):
        result = await result
    return result


def _normalized_metadata(
    metadata: CandidateMetadata,
    request,
    output: Path,
    capabilities: ProviderCapabilities,
    started: float,
    *,
    tier: str | None,
    intended_usage_class: str | None,
) -> CandidateMetadata:
    return metadata.model_copy(
        update={
            "tier": tier,
            "intended_usage_class": intended_usage_class,
            "provider": capabilities.provider_id,
            "model": capabilities.model,
            "request_hash": request_hash(request),
            "logical_request_hash": request_hash(request),
            "reference_hashes": [item.sha256 for item in request.references],
            "instruction_hash": instruction_hash(request),
            "seed": request.seed,
            "generation_attempt": request.attempt,
            "output_path": output.resolve(),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    )


def _build_contact_sheet(results: list[SceneResult], job_dir: Path) -> Path:
    images = [Image.open(item.accepted_path).convert("RGB") for item in results if item.accepted_path]
    thumb_width = 270
    thumb_height = 480
    sheet = Image.new("RGB", (thumb_width * 4, thumb_height + 48), "#34231f")
    draw = ImageDraw.Draw(sheet)
    try:
        for index, image in enumerate(images):
            image.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
            x = index * thumb_width + (thumb_width - image.width) // 2
            sheet.paste(image, (x, 0))
            draw.text((index * thumb_width + 8, thumb_height + 12), f"Scene {index + 1}", fill="#f2e4cf")
    finally:
        for image in images:
            image.close()
    path = job_dir / "contact_sheet.png"
    sheet.save(path, "PNG")
    return path.resolve()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _require_provider(provider: SceneImageProvider | None) -> SceneImageProvider:
    if provider is None:
        raise RuntimeError("live render requires an explicit scene image provider")
    return provider


def _provider_extension(provider: SceneImageProvider) -> str:
    """Select the provider's truthful output suffix without changing legacy paths."""
    return ".jpg" if provider.capabilities().provider_id == "gemini" else ".png"
