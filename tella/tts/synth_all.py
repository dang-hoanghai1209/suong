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
import json
import logging
import os
from pathlib import Path

from tella.planner.models import TellaScenePlan
from tella.tts import edge, google
from tella.tts.providers import EdgeTTSProvider, TTSResult, get_tts_provider
from tella.tts.text import normalize_narration_for_tts

logger = logging.getLogger("tella.tts.synth_all")


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


def _distribute_durations(scenes, total_duration: float) -> None:
    """Set ``scene.audio_duration`` for each scene by char-proportion.

    Rounding errors are absorbed into the final scene so
    ``sum(scene.audio_duration) == total_duration`` exactly (to 2 d.p.).
    """
    chars = [max(1, len((s.voice_script or "").strip())) for s in scenes]
    total_chars = sum(chars)
    if total_chars <= 0 or total_duration <= 0:
        # Defensive fallback — distribute evenly.
        even = total_duration / max(1, len(scenes))
        for s in scenes:
            s.audio_duration = round(even, 2)
        return

    running = 0.0
    for i, scene in enumerate(scenes):
        if i == len(scenes) - 1:
            scene.audio_duration = round(total_duration - running, 2)
        else:
            d = round(total_duration * chars[i] / total_chars, 2)
            scene.audio_duration = d
            running = round(running + d, 2)


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
    raw = (edge_rate or "0%").strip().rstrip("%")
    try:
        return round(1.0 + int(raw) / 100.0, 3)
    except ValueError:
        return 1.0


def _resolve_tts_settings(plan: TellaScenePlan, requested_provider: str) -> dict:
    provider = requested_provider or "edge"
    env_voice = (os.environ.get("TELLA_TTS_VOICE") or "").strip()
    env_language = (os.environ.get("TELLA_TTS_LANGUAGE") or "").strip().lower()
    language = plan.language if env_language in {"", "auto"} else env_language
    codec = (os.environ.get("TELLA_TTS_CODEC") or "mp3").strip().lower() or "mp3"
    sample_rate = _env_int("TELLA_TTS_SAMPLE_RATE", 24000)

    default_speed = _edge_rate_to_speed(plan.voice_edge_rate)
    if provider in {"cloudflare_grok", "xai"} and plan.theme == "minimalist_emotional":
        default_speed = 0.92
    speed, speed_from_env = _env_float("TELLA_TTS_SPEED", default_speed)

    if provider == "edge":
        voice = env_voice or plan.voice_name
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
    out.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


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

    Voice provider priority:
      1. Google Cloud TTS Chirp 3 HD (when key + voice set, kill switch off)
      2. Edge TTS — always-on fallback

    Raises:
        RuntimeError: when both providers fail.
    """
    job_dir = Path(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    if not body_scenes:
        return

    raw_text = _join_voice_scripts(
        body_scenes,
        add_terminal_punctuation=plan.theme != "minimalist_emotional",
    )
    requested_provider = (os.environ.get("TELLA_TTS_PROVIDER") or "").strip().lower()
    settings = _resolve_tts_settings(plan, requested_provider)
    full_text = normalize_narration_for_tts(
        raw_text,
        settings["provider"],
        plan.theme,
    )
    if not full_text.strip():
        return

    out = assets_dir / "narration.mp3"
    result: TTSResult | None = None
    fallback_used = False
    fallback_reason = ""
    google_enabled = bool(google_tts_api_key) and bool(google_tts_voice)

    if requested_provider:
        try:
            edge_metadata = {}
            if settings["provider"] == "edge" and not settings["speed_from_env"]:
                edge_metadata["edge_rate"] = plan.voice_edge_rate
            provider = get_tts_provider(settings["provider"])
            result = await provider.synthesize(
                full_text,
                out,
                voice=settings["voice"],
                language=settings["language"],
                speed=settings["speed"],
                codec=settings["codec"],
                sample_rate=settings["sample_rate"],
                metadata={
                    **edge_metadata,
                    "requested_provider": requested_provider,
                    "normalized_text_chars": len(full_text),
                },
            )
        except Exception as exc:
            fallback_reason = str(exc)[:500]
            if settings["provider"] == "edge" or _env_bool("TELLA_STRICT_TTS_PROVIDER"):
                raise RuntimeError(
                    f"TTS provider {settings['provider']} failed: {exc}"
                ) from exc
            fallback_used = True
            logger.warning(
                "TTS provider=%s failed; falling back to Edge TTS: %s",
                settings["provider"],
                fallback_reason,
            )
            result = await _synthesize_edge_fallback(
                full_text,
                out,
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
            out_path=out,
        )
        if ok:
            result = TTSResult(
                audio_path=out,
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
            out,
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

    total_duration = await _ffprobe_duration(out)
    result.duration = total_duration
    _distribute_durations(body_scenes, total_duration)
    effective_speed = (
        _edge_rate_to_speed(str(result.metadata.get("edge_rate", plan.voice_edge_rate)))
        if result.provider == "edge"
        else float(settings["speed"])
    )
    effective_codec = str(result.metadata.get("codec") or settings["codec"])

    plan.narration_audio_filename = f"assets/{out.name}"
    plan.narration_audio_path = str(out)
    plan.narration_duration = round(total_duration, 2)
    plan.tts_provider = result.provider
    plan.tts_voice = result.voice
    plan.tts_language = result.language
    plan.tts_speed = effective_speed
    plan.tts_codec = effective_codec
    plan.tts_sample_rate = int(settings["sample_rate"])
    plan.tts_fallback_used = fallback_used
    plan.tts_fallback_reason = fallback_reason

    tts_metadata = {
        "requested_provider": requested_provider or "edge",
        "requested_tts_speed": float(settings["speed"]),
        "tts_provider": result.provider,
        "tts_voice": result.voice,
        "tts_language": result.language,
        "tts_speed": effective_speed,
        "tts_codec": effective_codec,
        "tts_sample_rate": int(settings["sample_rate"]),
        "narration_audio_path": str(out),
        "narration_duration": round(total_duration, 2),
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "normalized_text_chars": len(full_text),
        "raw_text_chars": len(raw_text),
        "provider_metadata": result.metadata,
    }
    plan.tts_metadata = tts_metadata
    _save_tts_metadata(job_dir, tts_metadata)

    # Clear any stale per-scene audio_filename from older runs of this plan.
    for s in body_scenes:
        s.audio_filename = ""

    logger.info(
        "synthesize_all: 1 combined narration (%.2fs, provider=%s, voice=%s, language=%s, speed=%.2f, fallback=%s) "
        "distributed across %d scenes",
        total_duration,
        result.provider,
        result.voice,
        result.language,
        float(settings["speed"]),
        fallback_used,
        len(body_scenes),
    )


__all__ = ["synthesize_all"]
