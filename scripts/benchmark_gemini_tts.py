"""Bounded, sequential Gemini Vietnamese TTS voice benchmark."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from tella.tts import gemini
from tella.tts.gemini_registry import VOICE_NAMES, resolve_style, resolve_voice

BENCHMARK_TEXT = (
    "Bạn không cần thay đổi mọi thứ trong một ngày.\n\n"
    "Chỉ cần chọn một việc nhỏ, và làm cho bước đầu tiên dễ bắt đầu hơn.\n\n"
    "Khi đã quen với bước đầu tiên, những bước tiếp theo sẽ bớt nặng nề.\n\n"
    "Hôm nay, mình thử bắt đầu từ một điều thôi nhé."
)


async def _run(cmd: list[str], label: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"{label} failed: {stderr.decode(errors='replace')[-800:]}")


async def normalize_audio(raw: Path, normalized: Path) -> None:
    await _run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
        "-af", "loudnorm=I=-16:TP=-1:LRA=7,alimiter=limit=0.891251",
        "-ar", "24000", "-ac", "1", str(normalized),
    ], "benchmark loudness normalization")


async def probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"duration probe failed: {stderr.decode(errors='replace')[-400:]}")
    return float(stdout.decode().strip())


def parse_voices(raw: str) -> tuple[str, ...]:
    voices = VOICE_NAMES if raw.strip().lower() == "all" else tuple(
        item.strip() for item in raw.split(",") if item.strip()
    )
    if not voices:
        raise ValueError("at least one Gemini voice is required")
    if len(voices) > 6 or len(set(voices)) != len(voices):
        raise ValueError("benchmark voices must be unique and limited to six")
    return voices


async def run_benchmark(
    *,
    voices: tuple[str, ...],
    model: str,
    style: str,
    output_dir: Path,
    dry_run: bool,
    max_requests: int,
    no_retry: bool,
    synthesize_fn: Callable[..., Awaitable[dict[str, Any]]] = gemini.synthesize,
    normalize_fn: Callable[[Path, Path], Awaitable[None]] = normalize_audio,
    duration_fn: Callable[[Path], Awaitable[float]] = probe_duration,
) -> dict[str, Any]:
    if len(voices) > max_requests or max_requests > 6 or max_requests < 1:
        raise ValueError("maximum request count must cover voices and cannot exceed six")
    if not dry_run and not no_retry:
        raise ValueError("live Gemini benchmark requires --no-retry")
    instruction = resolve_style(style)
    for voice in voices:
        resolve_voice(voice, model)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for voice in voices:
        stem = voice.lower()
        entries.append({
            "voice": voice,
            "raw_output_path": str(output_dir / f"{stem}_raw.wav"),
            "normalized_output_path": str(output_dir / f"{stem}_normalized.wav"),
            "status": "planned" if dry_run else "pending",
        })
    manifest = {
        "provider": "gemini", "model": model, "language": "vi-VN",
        "voices": list(voices), "requested_style": style,
        "resolved_style_instruction": instruction,
        "narration_text": BENCHMARK_TEXT, "output_format": "WAV, mono, 24000 Hz",
        "maximum_requests": max_requests, "retry_policy": "no retries",
        "fallback_policy": "no fallback", "sequential": True,
        "post_tts_atempo_applied": False, "music_processing": False,
        "video_rendering": False,
        "credential_environment_variable": gemini.credential_environment_name(),
        "dry_run": dry_run, "request_count": 0, "entries": entries,
    }
    manifest_path = output_dir / "benchmark_manifest.json"
    summary_path = output_dir / "benchmark_summary.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if dry_run:
        summary = {"status": "dry_run", "request_count": 0, "voices": list(voices)}
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    try:
        for entry in entries:  # Deliberately sequential: never gather these calls.
            metadata = await synthesize_fn(
                BENCHMARK_TEXT, Path(entry["raw_output_path"]), model=model,
                voice=entry["voice"], style=style,
            )
            manifest["request_count"] += 1
            await normalize_fn(Path(entry["raw_output_path"]), Path(entry["normalized_output_path"]))
            raw_duration = await duration_fn(Path(entry["raw_output_path"]))
            normalized_duration = await duration_fn(Path(entry["normalized_output_path"]))
            entry.update(metadata)
            entry.update({
                "raw_duration": raw_duration,
                "normalized_duration": normalized_duration,
                "normalized_output_path": entry["normalized_output_path"],
                "post_tts_duration_fit_status": "skipped_benchmark_natural_duration",
                "status": "completed",
            })
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        entry["status"] = "failed"
        entry["failure_reason"] = str(exc)[:500]
        manifest["status"] = "failed_stopped_on_first_provider_failure"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    manifest["status"] = "completed"
    summary = {"status": "completed", "request_count": manifest["request_count"], "voices": entries}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voices", required=True, help="One voice, comma-separated voices, or all")
    parser.add_argument("--model", required=True)
    parser.add_argument("--style", required=True, choices=("natural", "vocal_smile", "natural_vocal_smile"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--no-retry", action="store_true")
    args = parser.parse_args()
    voices = parse_voices(args.voices)
    asyncio.run(run_benchmark(
        voices=voices, model=args.model, style=args.style,
        output_dir=args.output_dir, dry_run=args.dry_run,
        max_requests=args.max_requests, no_retry=args.no_retry,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
