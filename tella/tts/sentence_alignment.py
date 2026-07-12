"""Deterministic, zero-network sentence-boundary alignment from local PCM WAV."""
from __future__ import annotations

import hashlib
import math
import re
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ALGORITHM_VERSION = "sentence_energy_alignment_v1"


@dataclass(frozen=True)
class AlignmentConfig:
    search_window_seconds: float = 1.25
    minimum_scene_duration: float = 2.0
    frame_seconds: float = 0.02
    hop_seconds: float = 0.01
    silence_threshold_dbfs: float = -38.0
    low_energy_threshold_dbfs: float = -28.0
    minimum_silence_seconds: float = 0.10


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _word_weights(sentences: Sequence[str]) -> list[int]:
    return [max(1, len(re.findall(r"\w+", sentence, flags=re.UNICODE))) for sentence in sentences]


def expected_boundaries(sentences: Sequence[str], duration: float) -> list[float]:
    if len(sentences) != 7:
        raise ValueError("sentence alignment requires exactly seven sentences")
    weights = _word_weights(sentences)
    total = sum(weights)
    cumulative = 0
    boundaries = []
    for weight in weights[:-1]:
        cumulative += weight
        boundaries.append(duration * cumulative / total)
    return boundaries


def _read_pcm(path: Path) -> tuple[list[float], int, int, float]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.getnframes()
        raw = source.readframes(frames)
    if width != 2:
        raise ValueError("sentence alignment currently requires 16-bit PCM WAV")
    values = array("h")
    values.frombytes(raw)
    if channels > 1:
        mono = [sum(values[index:index + channels]) / channels for index in range(0, len(values), channels)]
    else:
        mono = [float(value) for value in values]
    return mono, rate, channels, frames / rate


def _energy_frames(samples: list[float], rate: int, config: AlignmentConfig) -> list[dict]:
    frame_size = max(1, round(rate * config.frame_seconds))
    hop_size = max(1, round(rate * config.hop_seconds))
    result = []
    for start in range(0, max(1, len(samples) - frame_size + 1), hop_size):
        frame = samples[start:start + frame_size]
        rms = math.sqrt(sum(sample * sample for sample in frame) / max(1, len(frame)))
        dbfs = 20 * math.log10(max(rms / 32768.0, 1e-9))
        result.append({"time": (start + len(frame) / 2) / rate, "dbfs": dbfs})
    return result


def _regions(frames: list[dict], threshold: float, min_duration: float, hop: float, kind: str) -> list[dict]:
    regions = []
    start = None
    bucket = []
    for index, frame in enumerate(frames + [{"time": math.inf, "dbfs": math.inf}]):
        if frame["dbfs"] <= threshold:
            if start is None:
                start = index
            bucket.append(frame)
            continue
        if start is not None:
            duration = len(bucket) * hop
            if duration >= min_duration:
                regions.append({
                    "time": sum(item["time"] for item in bucket) / len(bucket),
                    "duration": duration,
                    "dbfs": min(item["dbfs"] for item in bucket),
                    "type": kind,
                })
            start = None
            bucket = []
    return regions


def _candidates(frames: list[dict], config: AlignmentConfig) -> list[dict]:
    silence = _regions(
        frames, config.silence_threshold_dbfs, config.minimum_silence_seconds,
        config.hop_seconds, "silence",
    )
    low = _regions(
        frames, config.low_energy_threshold_dbfs,
        max(config.hop_seconds * 2, config.minimum_silence_seconds / 2),
        config.hop_seconds, "low_energy",
    )
    # Silence candidates supersede overlapping low-energy regions.
    return silence + [
        item for item in low
        if not any(abs(item["time"] - s["time"]) <= max(item["duration"], s["duration"]) / 2 for s in silence)
    ]


def _validate_manual(values: Sequence[float] | None, duration: float, minimum: float) -> list[float] | None:
    if values is None:
        return None
    result = [float(value) for value in values]
    if len(result) != 6:
        raise ValueError("manual boundaries must contain exactly six timestamps")
    points = [0.0, *result, duration]
    if any(not 0 < value < duration for value in result) or any(b <= a for a, b in zip(points, points[1:])):
        raise ValueError("manual boundaries must be strictly monotonic and inside the audio")
    if any(b - a < minimum for a, b in zip(points, points[1:])):
        raise ValueError("manual boundaries violate minimum scene duration")
    return result


def align_sentences(
    wav_path: Path,
    sentences: Sequence[str],
    *,
    total_duration: float | None = None,
    current_expected_boundaries: Sequence[float] | None = None,
    config: AlignmentConfig = AlignmentConfig(),
    manual_boundaries: Sequence[float] | None = None,
) -> dict:
    if len(sentences) != 7:
        raise ValueError("sentence alignment requires exactly seven sentences")
    samples, rate, channels, audio_duration = _read_pcm(Path(wav_path))
    duration = float(total_duration or audio_duration)
    if abs(duration - audio_duration) > 0.02:
        raise ValueError("declared duration does not match local WAV duration")
    if duration < 7 * config.minimum_scene_duration:
        raise ValueError("audio duration cannot satisfy minimum scene duration")
    expected = expected_boundaries(sentences, duration)
    manual = _validate_manual(manual_boundaries, duration, config.minimum_scene_duration)
    frames = _energy_frames(samples, rate, config)
    candidates = _candidates(frames, config)
    boundaries = []
    diagnostics = []
    for index, target in enumerate(expected):
        lower = (boundaries[-1] if boundaries else 0.0) + config.minimum_scene_duration
        remaining = 6 - index
        upper = duration - remaining * config.minimum_scene_duration
        constraints = []
        if manual is not None:
            chosen = manual[index]
            candidate = {"type": "manual_override", "duration": 0.0, "dbfs": None}
            confidence, fallback = "high", False
        else:
            nearby = [
                item for item in candidates
                if abs(item["time"] - target) <= config.search_window_seconds
                and lower <= item["time"] <= upper
            ]
            if nearby:
                def score(item):
                    distance = abs(item["time"] - target) / config.search_window_seconds
                    depth = max(0.0, min(1.0, (-item["dbfs"] - 20) / 50))
                    pause = min(1.0, item["duration"] / 0.35)
                    kind_bonus = 0.08 if item["type"] == "silence" else 0.0
                    return 0.62 * distance - 0.23 * depth - 0.15 * pause - kind_bonus
                candidate = min(nearby, key=lambda item: (score(item), item["time"]))
                chosen = candidate["time"]
                confidence = (
                    "high" if candidate["type"] == "silence" and abs(chosen - target) <= 0.65
                    else "medium"
                )
                fallback = False
            else:
                chosen = min(max(target, lower), upper)
                if chosen != target:
                    constraints.append("minimum_scene_duration_clamp")
                candidate = {"type": "weighted_fallback", "duration": 0.0, "dbfs": None}
                confidence, fallback = "low", True
        if not lower <= chosen <= upper:
            raise ValueError("selected boundary violates monotonic duration constraints")
        boundaries.append(chosen)
        diagnostics.append({
            "boundary_index": index + 1,
            "previous_sentence_index": index + 1,
            "next_sentence_index": index + 2,
            "expected_timestamp": round(target, 6),
            "chosen_timestamp": round(chosen, 6),
            "adjustment_ms": round((chosen - target) * 1000, 1),
            "candidate_type": candidate["type"],
            "candidate_pause_duration": round(candidate["duration"], 3),
            "local_energy_dbfs": None if candidate["dbfs"] is None else round(candidate["dbfs"], 2),
            "confidence": confidence,
            "fallback_used": fallback,
            "constraints_applied": constraints,
        })
    points = [0.0, *boundaries, duration]
    intervals = [
        {"sentence_index": index + 1, "start": round(points[index], 6),
         "end": round(points[index + 1], 6), "duration": round(points[index + 1] - points[index], 6)}
        for index in range(7)
    ]
    if any(item["duration"] < config.minimum_scene_duration - 1e-6 for item in intervals):
        raise ValueError("aligned interval violates minimum scene duration")
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "alignment_method": "deterministic_text_weight_plus_local_energy_valleys",
        "weighting_method": "unicode_word_count_cumulative_fraction",
        "audio_duration": round(audio_duration, 6),
        "sample_rate": rate,
        "channels": channels,
        "analysis_frame_size": round(rate * config.frame_seconds),
        "analysis_hop_size": round(rate * config.hop_seconds),
        "silence_threshold_dbfs": config.silence_threshold_dbfs,
        "low_energy_threshold_dbfs": config.low_energy_threshold_dbfs,
        "minimum_silence_duration": config.minimum_silence_seconds,
        "search_window_seconds": config.search_window_seconds,
        "minimum_scene_duration": config.minimum_scene_duration,
        "expected_boundaries": [round(value, 6) for value in expected],
        "current_expected_boundaries": list(current_expected_boundaries or []),
        "boundaries": [round(value, 6) for value in boundaries],
        "boundary_diagnostics": diagnostics,
        "scene_intervals": intervals,
        "subtitle_intervals": [dict(item) for item in intervals],
        "fallback_count": sum(item["fallback_used"] for item in diagnostics),
        "manual_override_count": sum(item["candidate_type"] == "manual_override" for item in diagnostics),
        "candidate_count": len(candidates),
        "wav_sha256": sha256_file(Path(wav_path)),
        "atempo_applied": False,
        "external_request_count": 0,
    }


__all__ = ["ALGORITHM_VERSION", "AlignmentConfig", "align_sentences", "expected_boundaries", "sha256_file"]
