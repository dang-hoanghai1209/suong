"""Synthesize ONE continuous narration MP3 for the whole plan.

The continuous-narration model (CEO 2026-06-29):
  * Join every body scene's ``voice_script`` into a single TTS input and
    issue ONE synthesis call. Edge TTS / Google TTS handle the inter-
    sentence breath pauses naturally inside the utterance — far smoother
    than concatenating N independently-synthesized MP3s, which each carry
    ~0.3-0.6 s of baked-in leading/trailing silence that compounded into
    1+ second gaps on scene boundaries.
  * Measure the resulting audio's total duration. Distribute it across
    scenes in proportion to each scene's ``voice_script`` character count
    — TTS speaks at a roughly constant chars/sec — so visual cuts still
    land near the right phrase. (Exact word-level alignment would need a
    forced aligner; char-proportion gets us within ~0.3 s, usually
    imperceptible against a Ken Burns image.)
  * The single audio file is recorded on the plan as
    ``narration_audio_filename``. Per-scene ``audio_filename`` is left
    blank — the render layer mixes the single track at final-mux time.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path

from tella._voice_pace import normalize_voice_rate
from tella.atomic_write import atomic_write_json
from tella.composer.timing import build_render_timing_plan
from tella.planner.models import TellaScenePlan
from tella.tts import gemini, google
from tella.tts.policy import (
    ProductionTTSPolicy,
    is_production_emotional,
    narration_planning_diagnostic,
    resolve_production_tts_policy,
    sanitize_tts_error,
)
from tella.tts.providers import EdgeTTSProvider, TTSResult, get_tts_provider
from tella.tts.text import normalize_narration_for_tts

logger = logging.getLogger("tella.tts.synth_all")

_CONTINUOUS_NARRATION_THEMES = {"minimalist_emotional", "minimalist_symbolic_reel"}


async def _ffprobe_duration(path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path.name}: "
            f"{stderr.decode('utf-8', errors='replace')[-200:]}"
        )
    try:
        return float(stdout.decode("ascii").strip() or "0")
    except ValueError:
        return 0.0


async def _detect_longest_silence(path: Path, *, min_duration: float = 0.2) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", f"silencedetect=noise=-40dB:d={min_duration:.3f}",
        "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return 0.0
    text = stderr.decode("utf-8", errors="replace")
    durations = [
        float(match.group(1))
        for match in re.finditer(r"silence_duration:\s*([0-9.]+)", text)
    ]
    return round(max(durations, default=0.0), 3)


async def _postprocess_narration_audio(raw_path: Path, out_path: Path, *, max_pause_ms: int) -> dict:
    max_pause_s = max(0.08, int(max_pause_ms) / 1000.0)
    original_duration = await _ffprobe_duration(raw_path)
    longest_before = await _detect_longest_silence(raw_path)
    audio_filter = (
        "silenceremove="
        "start_periods=1:start_duration=0.05:start_threshold=-45dB:"
        f"stop_periods=-1:stop_duration={max_pause_s:.3f}:"
        "stop_threshold=-45dB:"
        f"stop_silence={max_pause_s:.3f},"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(raw_path),
        "-af", audio_filter,
        "-c:a", "libmp3lame", "-q:a", "3",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size < 256:
        raise RuntimeError(
            "ffmpeg TTS post-process failed: "
            f"{stderr.decode('utf-8', errors='replace')[-300:]}"
        )
    processed_duration = await _ffprobe_duration(out_path)
    longest_after = await _detect_longest_silence(out_path)
    return {
        "silence_postprocess_applied": True,
        "max_pause_ms": int(max_pause_ms),
        "original_duration": round(original_duration, 6),
        "processed_duration": round(processed_duration, 6),
        "duration_delta": round(processed_duration - original_duration, 6),
        "processing_steps": [
            "remove_leading_silence_over_50ms",
            f"cap_detected_silence_to_{int(max_pause_ms)}ms",
            "loudness_normalize_-16lufs",
            "encode_mp3",
        ],
        "duration_change_expected": True,
        "longest_silence_before": longest_before,
        "longest_silence_after": longest_after,
    }


async def _normalize_gemini_narration(raw_path: Path, out_path: Path) -> dict:
    """Normalize loudness without tempo, silence, or pitch processing."""
    original_duration = await _ffprobe_duration(raw_path)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_path),
        "-af", "loudnorm=I=-16:TP=-1:LRA=7,alimiter=limit=0.891251:level=false",
        "-ar", "24000", "-ac", "1", str(out_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode or not out_path.is_file():
        raise RuntimeError(
            "Gemini loudness normalization failed: "
            + stderr.decode(errors="replace")[-300:]
        )
    processed_duration = await _ffprobe_duration(out_path)
    if abs(processed_duration - original_duration) > 0.01:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Gemini normalization changed narration duration beyond 0.01 seconds"
        )
    return {
        "silence_postprocess_applied": False,
        "max_pause_ms": 0,
        "original_duration": original_duration,
        "processed_duration": processed_duration,
        "duration_delta": round(processed_duration - original_duration, 6),
        "processing_steps": [
            "loudness_normalize_-16lufs",
            "true_peak_limit_-1dbtp",
            "mono_24000hz",
        ],
        "duration_change_expected": False,
        "longest_silence_before": 0.0,
        "longest_silence_after": 0.0,
        "atempo_applied": False,
        "duration_fit_applied": False,
    }


def _join_voice_scripts(scenes, *, add_terminal_punctuation: bool = True) -> str:
    """Concatenate per-scene voice_script into one TTS input.

    Joins with a space — Edge/Google TTS treat sentence-end punctuation as
    a natural breath cue, so we don't need to force extra padding. If a
    scene's script doesn't end in punctuation, we add a period so the TTS
    engine inflects it as a sentence end.
    """
    parts: list[str] = []
    for s in scenes:
        text = (s.voice_script or "").strip()
        if not text:
            continue
        # Ensure each scene ends with terminal punctuation so the TTS
        # engine plays a natural beat between them.
        if add_terminal_punctuation and text[-1] not in ".!?…":
            text = text + "."
        parts.append(text)
    return " ".join(parts)


def _scene_text_for_tts(scenes) -> str:
    return " ".join(
        re.sub(r"\s+", " ", (s.voice_script or "").strip())
        for s in scenes
        if (s.voice_script or "").strip()
    ).strip()


def _build_global_narration_text(scenes, *, theme: str) -> str:
    parts = [
        re.sub(r"\s+", " ", (s.voice_script or "").strip())
        for s in scenes
        if (s.voice_script or "").strip()
    ]
    if not parts:
        return ""
    if theme not in _CONTINUOUS_NARRATION_THEMES:
        return normalize_narration_for_tts(
            _join_voice_scripts(scenes),
            "edge",
            theme,
        )

    smoothed: list[str] = []
    for idx, part in enumerate(parts):
        part = re.sub(r"\s*(?:\.{3,}|\u2026+)\s*", ", ", part)
        part = re.sub(r"([!?]){2,}", r"\1", part)
        part = re.sub(r"\s+([,.;:!?])", r"\1", part)
        if idx < len(parts) - 1:
            part = part.rstrip(" .!?;:\u2026")
        else:
            part = part.rstrip()
        if part:
            smoothed.append(part)
    text = ", ".join(smoothed)
    text = re.sub(r",\s*,+", ", ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    if text and text[-1] not in ".!?\u2026":
        text = text + "."
    return text


def _tts_continuous_enabled(plan: TellaScenePlan) -> bool:
    raw = (os.environ.get("TELLA_TTS_CONTINUOUS") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return plan.theme in _CONTINUOUS_NARRATION_THEMES


def _distribute_durations(scenes, total_duration: float) -> None:
    """Set ``scene.audio_duration`` for each scene by char-proportion.

    Rounding errors are absorbed into the final scene so
    ``sum(scene.audio_duration) == total_duration`` exactly (to 6 d.p.).
    """
    chars = [max(1, len((s.voice_script or "").strip())) for s in scenes]
    total_chars = sum(chars)
    if total_chars <= 0 or total_duration <= 0:
        # Defensive fallback — distribute evenly.
        even = total_duration / max(1, len(scenes))
        for s in scenes:
            s.audio_duration = round(even, 6)
        return

    running = 0.0
    for i, scene in enumerate(scenes):
        if i == len(scenes) - 1:
            scene.audio_duration = round(total_duration - running, 6)
        else:
            d = round(total_duration * chars[i] / total_chars, 6)
            scene.audio_duration = d
            running = round(running + d, 6)


def _env_bool(name: str) -> bool:
    return (os.environ.get(name) or "").strip() == "1"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> tuple[float, bool]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default, False
    try:
        value = float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %.2f", name, raw, default)
        return default, False
    return max(0.25, min(4.0, value)), True


def _edge_rate_to_speed(edge_rate: str) -> float:
    raw = normalize_voice_rate(edge_rate).rstrip("%")
    return round(1.0 + int(raw) / 100.0, 3)


def _resolve_tts_settings(
    plan: TellaScenePlan,
    policy: ProductionTTSPolicy | str,
) -> dict:
    if isinstance(policy, str):
        policy = resolve_production_tts_policy(
            plan,
            explicit_provider=policy,
        )
    provider = policy.actual_provider
    env_voice = (os.environ.get("TELLA_TTS_VOICE") or "").strip()
    env_language = (os.environ.get("TELLA_TTS_LANGUAGE") or "").strip().lower()
    language = policy.language if env_language in {"", "auto"} else env_language
    codec = (os.environ.get("TELLA_TTS_CODEC") or ("wav" if provider == "gemini" else "mp3")).strip().lower()
    sample_rate = _env_int("TELLA_TTS_SAMPLE_RATE", 24000)

    default_speed = _edge_rate_to_speed(plan.voice_edge_rate)
    if provider in {"cloudflare_grok", "xai"} and plan.theme == "minimalist_emotional":
        default_speed = 0.92
    speed, speed_from_env = _env_float("TELLA_TTS_SPEED", default_speed)

    if provider == "edge":
        voice = env_voice or policy.actual_voice or plan.voice_name
    elif provider == "google":
        voice = env_voice or (os.environ.get("GOOGLE_TTS_VOICE") or "").strip() or "vi-VN-Chirp3-HD-Achernar"
    elif provider == "gemini":
        voice = env_voice or policy.actual_voice or plan.resolved_voice
        model = (
            (os.environ.get("TELLA_TTS_MODEL") or "").strip()
            or policy.model
            or plan.resolved_tts_model
        )
        style = (
            (os.environ.get("TELLA_TTS_STYLE") or "").strip()
            or policy.style
            or plan.resolved_tts_style
        )
        if not model or not voice or not style:
            raise RuntimeError("Gemini TTS requires explicit model, voice, and style")
        from tella.tts.gemini_registry import resolve_style, resolve_voice
        resolve_voice(voice, model)
        style_instruction = resolve_style(style)
    else:
        voice = env_voice or "ara"

    return {
        "provider": provider,
        "voice": voice,
        "language": language,
        "speed": speed,
        "speed_from_env": speed_from_env,
        "codec": codec,
        "sample_rate": sample_rate,
        "model": model if provider == "gemini" else "",
        "style": style if provider == "gemini" else "",
        "style_instruction": style_instruction if provider == "gemini" else "",
    }


async def _synthesize_edge_fallback(
    text: str,
    out: Path,
    plan: TellaScenePlan,
    *,
    language: str,
    sample_rate: int,
    fallback_from: str = "",
) -> TTSResult:
    return await EdgeTTSProvider().synthesize(
        text,
        out,
        voice=plan.voice_name,
        language=language,
        speed=_edge_rate_to_speed(plan.voice_edge_rate),
        codec="mp3",
        sample_rate=sample_rate,
        metadata={
            "edge_rate": plan.voice_edge_rate,
            "fallback_from": fallback_from,
        },
    )


def _save_tts_metadata(job_dir: Path, metadata: dict) -> Path:
    out = Path(job_dir) / "tts_metadata.json"
    atomic_write_json(out, metadata)
    return out


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generic_cache_key(
    *,
    text: str,
    settings: dict,
    policy: ProductionTTSPolicy,
) -> str:
    identity = {
        "schema_version": 1,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "provider": settings["provider"],
        "voice": settings["voice"],
        "language": settings["language"],
        "model": settings["model"],
        "style": settings["style"],
        "codec": settings["codec"],
        "sample_rate": settings["sample_rate"],
        "speed": settings["speed"],
        "policy_id": policy.policy_id,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_job_resume_metadata(
    job_dir: Path,
    *,
    raw_path: Path,
    processed_path: Path,
    cache_key: str,
) -> dict | None:
    metadata_path = Path(job_dir) / "tts_metadata.json"
    if not metadata_path.is_file() or not raw_path.is_file() or not processed_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("tts_cache_key") != cache_key:
        return None
    if metadata.get("raw_audio_sha256") != _file_sha256(raw_path):
        return None
    if metadata.get("processed_audio_sha256") != _file_sha256(processed_path):
        return None
    if float(metadata.get("processed_duration") or 0.0) <= 0:
        return None
    return metadata


def _result_from_resume(metadata: dict, raw_path: Path) -> TTSResult:
    return TTSResult(
        audio_path=raw_path,
        duration=float(metadata.get("processed_duration") or 0.0),
        provider=str(metadata.get("tts_provider") or ""),
        voice=str(metadata.get("tts_voice") or ""),
        language=str(metadata.get("tts_language") or ""),
        metadata={
            **dict(metadata.get("provider_metadata") or {}),
            "request_attempt_count": 0,
            "resume_reuse": True,
        },
    )


async def synthesize_all(
    plan: TellaScenePlan,
    job_dir: Path,
    *,
    google_tts_api_key: str = "",
    google_tts_voice: str = "",
) -> None:
    """Synthesize the narration as ONE continuous MP3.

    Mutates the plan in place:
      * ``plan.narration_audio_filename`` → ``"assets/narration.mp3"``
      * ``scene.audio_duration`` set by char-proportional split
      * ``scene.audio_filename`` left blank (render mixes the single track)

    Provider selection is resolved by the typed TTS policy. Production
    emotional narration prefers Gemini/Callirrhoe when configured and uses
    an explicit pre-request Edge fallback when it is unavailable. Legacy and
    explicitly configured providers retain their existing behavior.

    Raises:
        RuntimeError: when both providers fail.
    """
    job_dir = Path(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    if not body_scenes:
        return

    raw_scene_text = _scene_text_for_tts(body_scenes)
    tts_continuous = _tts_continuous_enabled(plan)
    manual_global_text = (
        plan.global_narration_text.strip()
        if plan.tts_text_source == "manual_global_narration_text"
        else ""
    )
    if manual_global_text:
        raw_text = manual_global_text
        text_source = "manual_global_narration_text"
    elif is_production_emotional(plan):
        raw_text = raw_scene_text
        text_source = "production_full_narration_text"
    elif tts_continuous:
        raw_text = _build_global_narration_text(body_scenes, theme=plan.theme)
        text_source = "global_narration_text"
    else:
        raw_text = _join_voice_scripts(
            body_scenes,
            add_terminal_punctuation=plan.theme not in _CONTINUOUS_NARRATION_THEMES,
        )
        text_source = "scene_voice_script_join"
    plan.global_narration_text = raw_text if tts_continuous else ""
    plan.tts_continuous = tts_continuous
    plan.tts_text_source = text_source
    default_pause_ms = 700 if plan.theme == "minimalist_symbolic_reel" else 350
    plan.tts_max_pause_ms = _env_int("TELLA_TTS_MAX_PAUSE_MS", default_pause_ms)
    requested_provider = (os.environ.get("TELLA_TTS_PROVIDER") or "").strip().lower()
    policy = resolve_production_tts_policy(
        plan,
        explicit_provider=requested_provider,
    )
    settings = _resolve_tts_settings(plan, policy)
    plan.tts_style = (
        settings["style"]
        or (os.environ.get("TELLA_TTS_STYLE") or "emotional_storytelling").strip()
        or "emotional_storytelling"
    )
    full_text = normalize_narration_for_tts(
        raw_text,
        settings["provider"],
        plan.theme,
    )
    if not full_text.strip():
        return

    extension = "wav" if settings["provider"] == "gemini" else "mp3"
    raw_out = assets_dir / f"narration_raw.{extension}"
    out = assets_dir / f"narration.{extension}"
    result: TTSResult | None = None
    cache_hit = False
    cache_key = _generic_cache_key(
        text=full_text,
        settings=settings,
        policy=policy,
    )
    fallback_used = policy.fallback_used
    fallback_reason = policy.fallback_reason
    job_resume_metadata: dict | None = None
    google_tts_api_key = google_tts_api_key or (os.environ.get("GOOGLE_TTS_API_KEY") or "").strip()
    google_tts_voice = google_tts_voice or (os.environ.get("GOOGLE_TTS_VOICE") or "").strip()
    google_enabled = bool(google_tts_api_key) and bool(google_tts_voice)

    if settings["provider"] == "gemini":
        max_requests = _env_int("TELLA_MAX_TTS_REQUESTS", 1)
        instruction = settings["style_instruction"]
        provider_input = gemini.serialize_provider_input(full_text, instruction)
        from tella.production import LocalTTSCache, tts_cache_key
        from tella.tts.gemini_registry import REGISTRY_VERSION
        cache_key = tts_cache_key(
            provider="gemini", model=settings["model"], voice=settings["voice"],
            style=plan.tts_style, language=settings["language"],
            canonical_narration_sha256=gemini.sha256_text(full_text),
            serialized_provider_input_sha256=gemini.sha256_text(provider_input),
            request_format_version=gemini.REQUEST_FORMAT_VERSION,
            voice_registry_version=REGISTRY_VERSION,
        )
        cache = LocalTTSCache(job_dir.parent / ".tts_cache")
        job_resume_metadata = _load_job_resume_metadata(
            job_dir,
            raw_path=raw_out,
            processed_path=out,
            cache_key=cache_key,
        )
        resume_raw = Path(os.environ.get("TELLA_TTS_RESUME_RAW") or "")
        if job_resume_metadata is not None:
            cache_hit = True
            result = _result_from_resume(job_resume_metadata, raw_out)
        elif resume_raw.is_file():
            if resume_raw.resolve() != raw_out.resolve():
                shutil.copyfile(resume_raw, raw_out)
            cache_hit = True
            result = TTSResult(
                audio_path=raw_out, provider="gemini", voice=settings["voice"],
                language=settings["language"], metadata={
                    "model": settings["model"], "requested_style": plan.tts_style,
                    "resolved_style_instruction": instruction,
                    "source_narration_text_hash": gemini.sha256_text(full_text),
                    "serialized_provider_input_hash": gemini.sha256_text(provider_input),
                    "request_format_version": gemini.REQUEST_FORMAT_VERSION,
                    "voice_registry_version": REGISTRY_VERSION,
                    "request_attempt_count": 0, "fallback_used": False,
                    "resume_reuse": True,
                },
            )
        elif _env_bool("TELLA_TTS_CACHE_ENABLED") and cache.lookup(cache_key, raw_out):
            cache_hit = True
            result = TTSResult(
                audio_path=raw_out, provider="gemini", voice=settings["voice"],
                language=settings["language"], metadata={
                    "model": settings["model"], "requested_style": plan.tts_style,
                    "resolved_style_instruction": instruction,
                    "source_narration_text_hash": gemini.sha256_text(full_text),
                    "serialized_provider_input_hash": gemini.sha256_text(provider_input),
                    "request_format_version": gemini.REQUEST_FORMAT_VERSION,
                    "voice_registry_version": REGISTRY_VERSION,
                    "request_attempt_count": 0, "fallback_used": False,
                },
            )
        elif max_requests < 1:
            raise RuntimeError("TTS cache miss with zero permitted TTS requests")

    if settings["provider"] != "gemini":
        job_resume_metadata = _load_job_resume_metadata(
            job_dir,
            raw_path=raw_out,
            processed_path=out,
            cache_key=cache_key,
        )
        if job_resume_metadata is not None:
            cache_hit = True
            result = _result_from_resume(job_resume_metadata, raw_out)

    if result is None and requested_provider == "google":
        ok = await google.synth_google(
            text=full_text,
            voice_name=settings["voice"],
            rate=plan.voice_edge_rate,
            api_key=google_tts_api_key,
            out_path=raw_out,
        )
        if not ok:
            if _env_bool("TELLA_STRICT_TTS_PROVIDER"):
                raise RuntimeError("TTS provider google failed; check GOOGLE_TTS_API_KEY and voice.")
            fallback_used = True
            fallback_reason = "google TTS failed or was not configured"
            logger.warning("TTS provider=google failed; falling back to Edge TTS")
            result = await _synthesize_edge_fallback(
                full_text,
                raw_out,
                plan,
                language=settings["language"],
                sample_rate=settings["sample_rate"],
                fallback_from="google",
            )
        else:
            result = TTSResult(
                audio_path=raw_out,
                provider="google",
                voice=settings["voice"],
                language=settings["language"],
                metadata={
                    "requested_provider": "google",
                    "edge_rate": plan.voice_edge_rate,
                    "normalized_text_chars": len(full_text),
                    "codec": "mp3",
                },
            )

    if (
        result is None
        and settings["provider"] != "google"
        and (
            policy.mode == "production_emotional"
            or bool(requested_provider)
            or not google_enabled
        )
    ):
        try:
            edge_metadata = {}
            if settings["provider"] == "edge" and not settings["speed_from_env"]:
                edge_metadata["edge_rate"] = plan.voice_edge_rate
            provider = get_tts_provider(settings["provider"])
            result = await provider.synthesize(
                full_text,
                raw_out,
                voice=settings["voice"],
                language=settings["language"],
                speed=settings["speed"],
                codec=settings["codec"],
                sample_rate=settings["sample_rate"],
                metadata={
                    **edge_metadata,
                    "requested_provider": requested_provider,
                    "normalized_text_chars": len(full_text),
                    "model": settings["model"],
                    "style": plan.tts_style,
                    "resolved_style_instruction": settings["style_instruction"],
                },
            )
        except Exception as exc:
            fallback_reason = sanitize_tts_error(exc)
            if settings["provider"] in {"edge", "gemini"} or _env_bool("TELLA_STRICT_TTS_PROVIDER"):
                raise RuntimeError(
                    f"TTS provider {settings['provider']} failed: {fallback_reason}"
                ) from None
            fallback_used = True
            logger.warning(
                "TTS provider=%s failed; falling back to Edge TTS: %s",
                settings["provider"],
                fallback_reason,
            )
            result = await _synthesize_edge_fallback(
                full_text,
                raw_out,
                plan,
                language=settings["language"],
                sample_rate=settings["sample_rate"],
                fallback_from=settings["provider"],
            )

    if result is None and google_enabled and not google.is_dead():
        ok = await google.synth_google(
            text=full_text,
            voice_name=google_tts_voice,
            rate=plan.voice_edge_rate,
            api_key=google_tts_api_key,
            out_path=raw_out,
        )
        if ok:
            result = TTSResult(
                audio_path=raw_out,
                provider="google",
                voice=google_tts_voice,
                language=settings["language"],
                metadata={
                    "requested_provider": "legacy_google",
                    "edge_rate": plan.voice_edge_rate,
                    "normalized_text_chars": len(full_text),
                },
            )

    if result is None:
        result = await EdgeTTSProvider().synthesize(
            full_text,
            raw_out,
            voice=settings["voice"] if requested_provider == "edge" else plan.voice_name,
            language=settings["language"],
            speed=settings["speed"],
            codec="mp3",
            sample_rate=settings["sample_rate"],
            metadata={
                "edge_rate": plan.voice_edge_rate,
                "requested_provider": requested_provider or "edge",
                "normalized_text_chars": len(full_text),
            },
        )

    original_duration = await _ffprobe_duration(raw_out)
    if result.provider == "gemini" and not cache_hit and _env_bool("TELLA_TTS_CACHE_ENABLED"):
        LocalTTSCache(job_dir.parent / ".tts_cache").store(
            cache_key, raw_out, {"provider": "gemini", "model": settings["model"],
                                 "voice": settings["voice"], "style": plan.tts_style},
        )
    if job_resume_metadata is not None:
        postprocess = {
            "silence_postprocess_applied": bool(
                job_resume_metadata.get("silence_postprocess_applied")
            ),
            "max_pause_ms": int(job_resume_metadata.get("max_pause_ms") or 0),
            "original_duration": float(job_resume_metadata["raw_duration"]),
            "processed_duration": float(job_resume_metadata["processed_duration"]),
            "duration_delta": float(job_resume_metadata.get("duration_delta") or 0.0),
            "processing_steps": list(job_resume_metadata.get("processing_steps") or []),
            "duration_change_expected": bool(
                job_resume_metadata.get("duration_change_expected")
            ),
            "longest_silence_before": float(
                job_resume_metadata.get("longest_silence_before") or 0.0
            ),
            "longest_silence_after": float(
                job_resume_metadata.get("longest_silence_after") or 0.0
            ),
        }
    else:
        postprocess = {
            "silence_postprocess_applied": False,
            "max_pause_ms": int(plan.tts_max_pause_ms),
            "original_duration": round(original_duration, 6),
            "processed_duration": round(original_duration, 6),
            "duration_delta": 0.0,
            "processing_steps": [],
            "duration_change_expected": False,
            "longest_silence_before": 0.0,
            "longest_silence_after": 0.0,
        }
        if result.provider == "gemini":
            postprocess = await _normalize_gemini_narration(raw_out, out)
        else:
            try:
                postprocess = await _postprocess_narration_audio(
                    raw_out,
                    out,
                    max_pause_ms=plan.tts_max_pause_ms,
                )
            except Exception as exc:
                shutil.copyfile(raw_out, out)
                logger.warning("TTS silence post-process skipped: %s", str(exc)[:180])
                postprocess["longest_silence_before"] = (
                    await _detect_longest_silence(raw_out)
                )
                postprocess["longest_silence_after"] = postprocess[
                    "longest_silence_before"
                ]

    total_duration = float(postprocess["processed_duration"]) or await _ffprobe_duration(out)
    result.duration = total_duration
    _distribute_durations(body_scenes, total_duration)
    timing_preview = build_render_timing_plan(
        [scene.audio_duration for scene in body_scenes],
        requested_duration=plan.requested_production_duration_seconds,
        narration_duration=total_duration,
    )
    effective_speed = (
        _edge_rate_to_speed(str(result.metadata.get("edge_rate", plan.voice_edge_rate)))
        if result.provider == "edge"
        else float(settings["speed"])
    )
    effective_codec = str(result.metadata.get("codec") or settings["codec"])

    plan.narration_audio_filename = f"assets/{out.name}"
    plan.narration_audio_path = str(out)
    plan.narration_duration = round(total_duration, 6)
    plan.original_narration_duration = float(postprocess["original_duration"])
    plan.processed_narration_duration = float(postprocess["processed_duration"])
    plan.silence_postprocess_applied = bool(postprocess["silence_postprocess_applied"])
    plan.longest_silence_before = float(postprocess["longest_silence_before"])
    plan.longest_silence_after = float(postprocess["longest_silence_after"])
    plan.tts_provider = result.provider
    plan.tts_voice = result.voice
    plan.tts_language = result.language
    plan.tts_speed = effective_speed
    plan.tts_codec = effective_codec
    plan.tts_sample_rate = int(settings["sample_rate"])
    plan.tts_fallback_used = fallback_used
    plan.tts_fallback_reason = fallback_reason

    planning_diagnostic = narration_planning_diagnostic(
        full_text,
        plan.requested_production_duration_seconds,
    )
    policy_metadata = policy.metadata()
    policy_metadata.update(
        {
            "actual_provider": result.provider,
            "actual_voice": result.voice,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
        }
    )
    tts_metadata = {
        "requested_provider": requested_provider or policy.preferred_provider,
        "requested_tts_speed": float(settings["speed"]),
        "tts_provider": result.provider,
        "tts_voice": result.voice,
        "tts_language": result.language,
        "tts_speed": effective_speed,
        "tts_codec": effective_codec,
        "tts_sample_rate": int(settings["sample_rate"]),
        "narration_audio_path": str(out),
        "narration_duration": round(total_duration, 6),
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "tts_continuous": tts_continuous,
        "tts_text_source": plan.tts_text_source,
        "tts_style": plan.tts_style,
        "raw_scene_text_chars": len(raw_scene_text),
        "global_narration_text_chars": len(plan.global_narration_text),
        "silence_postprocess_applied": bool(postprocess["silence_postprocess_applied"]),
        "max_pause_ms": int(plan.tts_max_pause_ms),
        "original_duration": float(postprocess["original_duration"]),
        "processed_duration": float(postprocess["processed_duration"]),
        "duration_delta": float(
            postprocess.get(
                "duration_delta",
                float(postprocess["processed_duration"])
                - float(postprocess["original_duration"]),
            )
        ),
        "processing_steps": list(postprocess.get("processing_steps") or []),
        "duration_change_expected": bool(
            postprocess.get("duration_change_expected", False)
        ),
        "longest_silence_before": float(postprocess["longest_silence_before"]),
        "longest_silence_after": float(postprocess["longest_silence_after"]),
        "edge_rate": str(result.metadata.get("edge_rate", plan.voice_edge_rate)),
        "normalized_text_chars": len(full_text),
        "raw_text_chars": len(raw_text),
        "canonical_narration_text": full_text,
        "canonical_narration_text_sha256": hashlib.sha256(
            full_text.encode("utf-8")
        ).hexdigest(),
        "provider_metadata": result.metadata,
        "tts_model": str(result.metadata.get("model") or ""),
        "voice_registry_version": result.metadata.get("voice_registry_version"),
        "resolved_style_instruction": str(result.metadata.get("resolved_style_instruction") or ""),
        "source_narration_text_hash": str(result.metadata.get("source_narration_text_hash") or ""),
        "raw_output_path": str(raw_out),
        "normalized_output_path": str(out),
        "raw_duration": float(postprocess["original_duration"]),
        "normalized_duration": float(postprocess["processed_duration"]),
        "request_attempt_count": int(result.metadata.get("request_attempt_count", 1)),
        "narration_synthesis_count": 0 if cache_hit else 1,
        "continuous_artifact_count": 1,
        "cache_hit": cache_hit,
        "tts_cache_key": cache_key,
        "raw_audio_sha256": _file_sha256(raw_out),
        "processed_audio_sha256": _file_sha256(out),
        "production_tts_policy": policy_metadata,
        "narration_planning_diagnostic": planning_diagnostic,
        "requested_production_duration_seconds": (
            plan.requested_production_duration_seconds
        ),
        "requested_vs_narration_delta_seconds": round(
            total_duration - plan.requested_production_duration_seconds,
            6,
        ) if plan.requested_production_duration_seconds > 0 else None,
        "authoritative_timeline_duration_seconds": (
            timing_preview.authoritative_duration
        ),
        "timing_authority": timing_preview.authority,
        "post_tts_scene_timeline_durations_seconds": list(
            timing_preview.scene_timeline_durations
        ),
        "post_tts_duration_fit_status": "pending_production_duration_fit",
    }
    plan.tts_metadata = tts_metadata
    _save_tts_metadata(job_dir, tts_metadata)

    # Clear any stale per-scene audio_filename from older runs of this plan.
    for s in body_scenes:
        s.audio_filename = ""

    logger.info(
        "synthesize_all: 1 combined narration (%.2fs -> %.2fs, provider=%s, voice=%s, language=%s, edge_rate=%s, speed=%.2f, continuous=%s, source=%s, fallback=%s) "
        "longest_silence %.2fs -> %.2fs, distributed across %d scenes",
        float(postprocess["original_duration"]),
        total_duration,
        result.provider,
        result.voice,
        result.language,
        str(result.metadata.get("edge_rate", plan.voice_edge_rate)),
        float(settings["speed"]),
        tts_continuous,
        plan.tts_text_source,
        fallback_used,
        float(postprocess["longest_silence_before"]),
        float(postprocess["longest_silence_after"]),
        len(body_scenes),
    )


__all__ = ["synthesize_all"]
