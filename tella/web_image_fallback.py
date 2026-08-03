"""Backend-owned privacy authority for the web Pollinations fallback."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING

from PIL import Image

from tella.atomic_write import atomic_write_bytes
from tella.topic_production.strategy import (
    ProviderFailureCategory,
    SceneDataSensitivity,
    pollinations_failover_decision,
)
from tella.visual_generation.providers.kinds import ProviderKind
from tella.visual_generation.providers.pollinations import (
    DEFAULT_MODEL,
    PollinationsConfig,
    PollinationsExecutionRequest,
    PollinationsPrivacyMetadata,
    PollinationsPromptSource,
    PollinationsSceneImageProvider,
)

if TYPE_CHECKING:
    from tella.planner.models import Scene, TellaScenePlan


WEB_SENSITIVITY_POLICY_ID = "generic_text_only_v1"
WEB_POLLINATIONS_FALLBACK_ENV = "TELLA_WEB_POLLINATIONS_FALLBACK"
WEB_SENSITIVITY_POLICY_ENV = "TELLA_WEB_SENSITIVITY_POLICY"
MAX_WEB_POLLINATIONS_IMAGE_BYTES = 20 * 1024 * 1024

_PRIVATE_TEXT = re.compile(
    r"(?i)("
    r"https?://|www\.|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|"
    r"(?:[a-z]:[\\/]|/(?:users|home|var|private)/)|"
    r"\b(?:api[_ -]?key|authorization|bearer|password|secret|token)\b|"
    r"\b(?:my name|my address|my phone|my family|social security|passport)\b|"
    r"\b(?:tên tôi|địa chỉ của tôi|số điện thoại|gia đình tôi|căn cước|hộ chiếu)\b|"
    r"(?:\d[\s().+-]*){7,}"
    r")"
)
_CONTROL_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_GENERIC_CHARACTER_PREFIXES = (
    "a ",
    "an ",
    "the ",
    "adult ",
    "anonymous ",
    "young ",
    "older ",
    "người ",
    "một ",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_is_public_safe(value: str) -> bool:
    normalized = " ".join((value or "").split())
    return (
        bool(normalized)
        and not _PRIVATE_TEXT.search(normalized)
        and not _CONTROL_TEXT.search(normalized)
    )


def _scene_is_public_safe(scene: Scene) -> bool:
    if any(
        (
            scene.reference_paths,
            scene.original_reference_paths,
            scene.previous_scene_reference_path,
            scene.sprite_path,
            scene.head_base_path,
            scene.face_path,
            scene.source_narration_path if hasattr(scene, "source_narration_path") else "",
        )
    ):
        return False
    for name in (*scene.character_names, *scene.requested_characters, *scene.required_characters):
        rendered = " ".join(name.split())
        if rendered and not rendered.casefold().startswith(_GENERIC_CHARACTER_PREFIXES):
            return False
    authority_text = "\n".join(
        (
            scene.voice_script,
            scene.image_prompt,
            scene.scene_meaning,
            scene.visual_action,
            scene.visual_object,
            scene.visual_environment,
        )
    )
    return _text_is_public_safe(authority_text)


def _scene_authority_material(
    scene: Scene,
    *,
    source_sha256: str,
    sensitivity: SceneDataSensitivity,
) -> dict[str, object]:
    return {
        "policy_id": WEB_SENSITIVITY_POLICY_ID,
        "source_sha256": source_sha256,
        "scene_index": scene.scene_index,
        "kind": scene.kind,
        "voice_script": scene.voice_script,
        "image_prompt": scene.image_prompt,
        "scene_meaning": scene.scene_meaning,
        "visual_action": scene.visual_action,
        "visual_object": scene.visual_object,
        "visual_environment": scene.visual_environment,
        "character_names": list(scene.character_names),
        "requested_characters": list(scene.requested_characters),
        "required_characters": list(scene.required_characters),
        "reference_paths": list(scene.reference_paths),
        "original_reference_paths": list(scene.original_reference_paths),
        "previous_scene_reference_path": scene.previous_scene_reference_path,
        "sensitivity": sensitivity.value,
    }


def _plan_authority_sha256(plan: TellaScenePlan) -> str:
    return _canonical_sha256(
        {
            "policy_id": plan.web_sensitivity_policy_id,
            "source_sha256": plan.web_sensitivity_source_sha256,
            "scenes": [
                {
                    "scene_index": scene.scene_index,
                    "authority_sha256": scene.data_sensitivity_authority_sha256,
                }
                for scene in plan.scenes
                if scene.kind == "scene"
            ],
        }
    )


def apply_web_scene_sensitivity_authority(
    plan: TellaScenePlan,
    *,
    source_text: str,
    policy_id: str,
) -> None:
    """Mint the web-only authority; callers cannot supply per-scene decisions."""
    if policy_id != WEB_SENSITIVITY_POLICY_ID:
        raise ValueError("unsupported web scene sensitivity policy")
    source = source_text.strip()
    if not source:
        raise ValueError("web scene sensitivity authority requires source text")
    source_sha256 = _sha256_text(source)
    source_public_safe = _text_is_public_safe(source)
    for scene in plan.scenes:
        if scene.kind != "scene":
            continue
        sensitivity = (
            SceneDataSensitivity.PUBLIC_SAFE
            if source_public_safe and _scene_is_public_safe(scene)
            else SceneDataSensitivity.PRIVATE
        )
        scene.data_sensitivity = sensitivity.value
        scene.data_sensitivity_source_sha256 = source_sha256
        scene.data_sensitivity_authority_sha256 = _canonical_sha256(
            _scene_authority_material(
                scene,
                source_sha256=source_sha256,
                sensitivity=sensitivity,
            )
        )
    plan.web_sensitivity_policy_id = policy_id
    plan.web_sensitivity_source_sha256 = source_sha256
    plan.web_sensitivity_authority_sha256 = _plan_authority_sha256(plan)
    plan.primary_image_provider = "cloudflare"
    plan.fallback_image_provider = "pollinations"


def validate_web_scene_sensitivity_authorities(
    plan: TellaScenePlan,
    *,
    required_policy_id: str | None,
) -> dict[int, SceneDataSensitivity]:
    """Return detached decisions only after every authority hash revalidates."""
    if not required_policy_id and not plan.web_sensitivity_policy_id:
        return {}
    if required_policy_id != WEB_SENSITIVITY_POLICY_ID:
        raise ValueError("web scene sensitivity policy is missing or unsupported")
    if plan.web_sensitivity_policy_id != required_policy_id:
        raise ValueError("web scene sensitivity policy does not match the execution policy")
    if not re.fullmatch(r"[0-9a-f]{64}", plan.web_sensitivity_source_sha256):
        raise ValueError("web scene sensitivity source identity is malformed")
    decisions: dict[int, SceneDataSensitivity] = {}
    for scene in plan.scenes:
        if scene.kind != "scene":
            continue
        try:
            sensitivity = SceneDataSensitivity(scene.data_sensitivity)
        except ValueError as exc:
            raise ValueError("web scene sensitivity is missing or malformed") from exc
        if scene.data_sensitivity_source_sha256 != plan.web_sensitivity_source_sha256:
            raise ValueError("web scene sensitivity source identity is stale")
        expected = _canonical_sha256(
            _scene_authority_material(
                scene,
                source_sha256=plan.web_sensitivity_source_sha256,
                sensitivity=sensitivity,
            )
        )
        if scene.data_sensitivity_authority_sha256 != expected:
            raise ValueError("web scene sensitivity authority is stale")
        if scene.scene_index in decisions:
            raise ValueError("web scene sensitivity authority has duplicate scene indices")
        decisions[scene.scene_index] = sensitivity
    if plan.web_sensitivity_authority_sha256 != _plan_authority_sha256(plan):
        raise ValueError("web plan sensitivity authority is stale")
    return decisions


def pollinations_web_readiness(
    *,
    width: int,
    height: int,
    policy_id: str | None = None,
) -> dict[str, object]:
    setting = (os.environ.get(WEB_POLLINATIONS_FALLBACK_ENV) or "").strip().casefold()
    enabled = setting not in {"0", "false", "no", "off"}
    credential_present = bool((os.environ.get("POLLINATIONS_API_KEY") or "").strip())
    configured_policy = policy_id or (os.environ.get(WEB_SENSITIVITY_POLICY_ENV) or "").strip()
    policy_ready = configured_policy == WEB_SENSITIVITY_POLICY_ID
    geometry_ready = (width, height) in {(768, 1344), (1344, 768)}
    reason = (
        "ready"
        if enabled and credential_present and policy_ready and geometry_ready
        else "disabled"
        if not enabled
        else "missing_credential"
        if not credential_present
        else "missing_sensitivity_authority"
        if not policy_ready
        else "unsupported_geometry"
    )
    return {
        "configured": enabled,
        "credential_present": credential_present,
        "policy_ready": policy_ready,
        "geometry_ready": geometry_ready,
        "eligible": reason == "ready",
        "reason": reason,
        "model": DEFAULT_MODEL,
    }


def classify_cloudflare_failover(exc: Exception) -> ProviderFailureCategory | None:
    error_type = str(getattr(exc, "error_type", "") or "")
    mapping = {
        "rate_limited": ProviderFailureCategory.RATE_LIMITED,
        "quota_exhausted": ProviderFailureCategory.QUOTA_OR_CREDIT_EXHAUSTED,
        "provider_unavailable": ProviderFailureCategory.PROVIDER_UNAVAILABLE,
        "timeout": ProviderFailureCategory.TIMEOUT,
    }
    return mapping.get(error_type)


def pollinations_failover_eligible(
    *,
    sensitivity: SceneDataSensitivity,
    cloudflare_error: Exception,
    readiness: dict[str, object],
) -> tuple[bool, str]:
    failure = classify_cloudflare_failover(cloudflare_error)
    if failure is None:
        return False, str(getattr(cloudflare_error, "error_type", "unknown") or "unknown")
    decision = pollinations_failover_decision(
        sensitivity=sensitivity,
        failed_provider=ProviderKind.CLOUDFLARE_KLEIN_4B,
        failure=failure,
    )
    return bool(decision.eligible and readiness.get("eligible") is True), failure.value


def _pollinations_prompt_source(scene: Scene, plan: TellaScenePlan) -> PollinationsPromptSource:
    action = scene.visual_action or scene.scene_action or "quiet purposeful movement"
    setting = scene.visual_environment or scene.scene_setting or "simple uncluttered setting"
    meaning = scene.scene_meaning or scene.voice_script
    values = (meaning, action, setting, plan.theme)
    if not all(_text_is_public_safe(value) for value in values):
        raise ValueError("Pollinations fallback prompt source is not PUBLIC_SAFE")
    return PollinationsPromptSource(
        scene_meaning=meaning,
        action=[action],
        mood=[scene.emotional_state or "calm"],
        setting=[setting],
        generic_character_description="anonymous adult woman without identifying features",
        style_description=f"{plan.theme.replace('_', ' ')} editorial illustration",
        composition=[scene.composition_pattern or scene.framing or "clear centered composition"],
        negative_constraints=["text", "logos", "watermarks", "identifying information"],
    )


async def generate_web_pollinations_fallback(
    *,
    plan: TellaScenePlan,
    scene: Scene,
    sensitivity: SceneDataSensitivity,
    output_path: Path,
    width: int,
    height: int,
    seed: int,
) -> dict[str, object]:
    readiness = pollinations_web_readiness(width=width, height=height)
    if not readiness["eligible"]:
        raise RuntimeError(f"Pollinations fallback is not ready: {readiness['reason']}")
    if sensitivity is not SceneDataSensitivity.PUBLIC_SAFE:
        raise ValueError("Pollinations fallback requires PUBLIC_SAFE authority")
    request = PollinationsExecutionRequest(
        scene_id=f"scene_{scene.scene_index:02d}",
        sensitivity=sensitivity.value,
        prompt_source=_pollinations_prompt_source(scene, plan),
        privacy=PollinationsPrivacyMetadata(),
        seed=max(0, min(int(seed), 2_147_483_647)),
        width=width,
        height=height,
    )
    provider = PollinationsSceneImageProvider(config=PollinationsConfig(enabled=True))
    candidate_base = output_path.with_name(f".{output_path.stem}-pollinations")
    metadata = await provider.generate_public_scene(request, candidate_base)
    candidate_path = metadata.output_path
    try:
        source_bytes = candidate_path.read_bytes()
        if not source_bytes or len(source_bytes) > MAX_WEB_POLLINATIONS_IMAGE_BYTES:
            raise ValueError("Pollinations image bytes are empty or exceed the web limit")
        with Image.open(io.BytesIO(source_bytes)) as image:
            image.load()
            if image.size != (width, height):
                raise ValueError("Pollinations image dimensions changed after validation")
            if output_path.suffix.lower() in {".jpg", ".jpeg"} and image.format != "JPEG":
                converted = io.BytesIO()
                image.convert("RGB").save(converted, format="JPEG", quality=95)
                final_bytes = converted.getvalue()
            elif output_path.suffix.lower() == ".png" and image.format != "PNG":
                converted = io.BytesIO()
                image.save(converted, format="PNG")
                final_bytes = converted.getvalue()
            else:
                final_bytes = source_bytes
        if not final_bytes or len(final_bytes) > MAX_WEB_POLLINATIONS_IMAGE_BYTES:
            raise ValueError("Pollinations final image bytes are empty or exceed the web limit")
        atomic_write_bytes(output_path, final_bytes)
    finally:
        candidate_path.unlink(missing_ok=True)
    return {
        "provider": "pollinations",
        "request_sha256": metadata.provider_request_hash or metadata.request_hash,
        "width": width,
        "height": height,
        "bytes": output_path.stat().st_size,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "mime_type": "image/jpeg"
        if output_path.suffix.lower() in {".jpg", ".jpeg"}
        else "image/png",
    }


__all__ = [
    "WEB_POLLINATIONS_FALLBACK_ENV",
    "WEB_SENSITIVITY_POLICY_ENV",
    "WEB_SENSITIVITY_POLICY_ID",
    "apply_web_scene_sensitivity_authority",
    "classify_cloudflare_failover",
    "generate_web_pollinations_fallback",
    "pollinations_failover_eligible",
    "pollinations_web_readiness",
    "validate_web_scene_sensitivity_authorities",
]
