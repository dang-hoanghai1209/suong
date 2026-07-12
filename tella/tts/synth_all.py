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
import re
import shutil
from pathlib import Path

from tella.planner.models import TellaScenePlan
from tella.tts import edge, google
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
        "original_duration": round(original_duration, 2),
        "processed_duration": round(processed_duration, 2),
        "longest_silence_before": longest_before,
        "longest_silence_after": longest_after,
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
    codec = (os.environ.get("TELLA_TTS_CODEC") or ("wav" if provider == "gemini" else "mp3")).strip().lower()
    sample_rate = _env_int("TELLA_TTS_SAMPLE_RATE", 24000)

    default_speed = _edge_rate_to_speed(plan.voice_edge_rate)
    if provider in {"cloudflare_grok", "xai"} and plan.theme == "minimalist_emotional":
        default_speed = 0.92
    speed, speed_from_env = _env_float("TELLA_TTS_SPEED", default_speed)

    if provider == "edge":
        voice = env_voice or plan.voice_name
    elif provider == "google":
        voice = env_voice or (os.environ.get("GOOGLE_TTS_VOICE") or "").strip() or "vi-VN-Chirp3-HD-Achernar"
    elif provider == "gemini":
        voice = env_voice
        model = (os.environ.get("TELLA_TTS_MODEL") or "").strip()
        style = (os.environ.get("TELLA_TTS_STYLE") or "").strip()
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

    raw_scene_text = _scene_text_for_tts(body_scenes)
    tts_continuous = _tts_continuous_enabled(plan)
    raw_text = (
        _build_global_narration_text(body_scenes, theme=plan.theme)
        if tts_continuous else
        _join_voice_scripts(
            body_scenes,
            add_terminal_punctuation=plan.theme not in _CONTINUOUS_NARRATION_THEMES,
        )
    )
    plan.global_narration_text = raw_text if tts_continuous else ""
    plan.tts_continuous = tts_continuous
    plan.tts_text_source = "global_narration_text" if tts_continuous else "scene_voice_script_join"
    default_pause_ms = 700 if plan.theme == "minimalist_symbolic_reel" else 350
    plan.tts_max_pause_ms = _env_int("TELLA_TTS_MAX_PAUSE_MS", default_pause_ms)
    plan.tts_style = (os.environ.get("TELLA_TTS_STYLE") or "emotional_storytelling").strip() or "emotional_storytelling"
    requested_provider = (os.environ.get("TELLA_TTS_PROVIDER") or "").strip().lower()
    settings = _resolve_tts_settings(plan, requested_provider)
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
    fallback_used = False
    fallback_reason = ""
    google_tts_api_key = google_tts_api_key or (os.environ.get("GOOGLE_TTS_API_KEY") or "").strip()
    google_tts_voice = google_tts_voice or (os.environ.get("GOOGLE_TTS_VOICE") or "").strip()
    google_enabled = bool(google_tts_api_key) and bool(google_tts_voice)

    if requested_provider == "google":
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

    if result is None and requested_provider and requested_provider != "google":
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
            fallback_reason = str(exc)[:500]
            if settings["provider"] in {"edge", "gemini"} or _env_bool("TELLA_STRICT_TTS_PROVIDER"):
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
    postprocess = {
        "silence_postprocess_applied": False,
        "max_pause_ms": int(plan.tts_max_pause_ms),
        "original_duration": round(original_duration, 2),
        "processed_duration": round(original_duration, 2),
        "longest_silence_before": 0.0,
        "longest_silence_after": 0.0,
    }
    if result.provider == "gemini":
        shutil.copyfile(raw_out, out)
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
            postprocess["longest_silence_before"] = await _detect_longest_silence(raw_out)
            postprocess["longest_silence_after"] = postprocess["longest_silence_before"]

    total_duration = float(postprocess["processed_duration"]) or await _ffprobe_duration(out)
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
        "tts_continuous": tts_continuous,
        "tts_text_source": plan.tts_text_source,
        "tts_style": plan.tts_style,
        "raw_scene_text_chars": len(raw_scene_text),
        "global_narration_text_chars": len(plan.global_narration_text),
        "silence_postprocess_applied": bool(postprocess["silence_postprocess_applied"]),
        "max_pause_ms": int(plan.tts_max_pause_ms),
        "original_duration": float(postprocess["original_duration"]),
        "processed_duration": float(postprocess["processed_duration"]),
        "longest_silence_before": float(postprocess["longest_silence_before"]),
        "longest_silence_after": float(postprocess["longest_silence_after"]),
        "edge_rate": str(result.metadata.get("edge_rate", plan.voice_edge_rate)),
        "normalized_text_chars": len(full_text),
        "raw_text_chars": len(raw_text),
        "provider_metadata": result.metadata,
        "tts_model": str(result.metadata.get("model") or ""),
        "voice_registry_version": result.metadata.get("voice_registry_version"),
        "resolved_style_instruction": str(result.metadata.get("resolved_style_instruction") or ""),
        "source_narration_text_hash": str(result.metadata.get("source_narration_text_hash") or ""),
        "raw_output_path": str(raw_out),
        "normalized_output_path": str(out),
        "raw_duration": float(postprocess["original_duration"]),
        "normalized_duration": float(postprocess["processed_duration"]),
        "request_attempt_count": int(result.metadata.get("request_attempt_count") or 1),
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
