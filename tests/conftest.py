"""Shared deterministic, license-free test fixtures."""
from __future__ import annotations

import json
import math
import struct
import wave

import pytest


def create_synthetic_practical_music_library(tmp_path):
    """Create a validated temporary practical catalogue without changing globals."""
    root = tmp_path / "synthetic_music_library"
    track = root / "tracks" / "synthetic_practical_tone.wav"
    license_path = root / "licenses" / "synthetic_practical_tone.txt"
    track.parent.mkdir(parents=True)
    license_path.parent.mkdir(parents=True)
    sample_rate = 24_000
    duration = 10.0
    with wave.open(str(track), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(round(sample_rate * duration)):
            envelope = min(1.0, index / 1200, (sample_rate * duration - index) / 1200)
            sample = int(32767 * 0.12 * envelope * math.sin(2 * math.pi * 220 * index / sample_rate))
            frames.extend(struct.pack("<h", sample))
        output.writeframes(frames)
    try:
        with wave.open(str(track), "rb") as source:
            valid = (
                source.getsampwidth() == 2
                and source.getnchannels() == 1
                and source.getframerate() == sample_rate
                and source.getnframes() == round(sample_rate * duration)
            )
    except (OSError, wave.Error) as exc:
        raise RuntimeError("synthetic music fixture is not a valid PCM WAV") from exc
    if not valid:
        raise RuntimeError("synthetic music fixture has invalid PCM parameters")
    license_path.write_text(
        "Generated deterministic tone for zero-network tests; no copyrighted input.",
        encoding="utf-8",
    )
    catalogue = {
        "version": 1,
        "tracks": [{
            "track_id": "synthetic_practical_tone",
            "title": "Synthetic practical tone",
            "creator": "Tella test suite",
            "source": "runtime generated",
            "file_path": "tracks/synthetic_practical_tone.wav",
            "source_duration": duration,
            "moods": ["calm", "encouraging", "light_rhythm"],
            "supported_recipes": [
                "practical_life_steps_v1",
                "practical_life_steps_callirrhoe_v1",
            ],
            "energy": "medium_low",
            "intro_safe_seconds": 0.5,
            "loop_safe": True,
            "loop_start": 0.5,
            "loop_end": 9.5,
            "default_start_offset": 0.0,
            "license_type": "generated_test_fixture",
            "license_reference": "licenses/synthetic_practical_tone.txt",
            "attribution_required": False,
            "attribution_text": "",
            "content_id_registered": False,
            "enabled": True,
        }],
    }
    (root / "library.json").write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


@pytest.fixture
def synthetic_practical_music_library(tmp_path, monkeypatch):
    """Opt-in function-scoped catalogue compatible with practical recipes."""
    root = create_synthetic_practical_music_library(tmp_path)
    monkeypatch.setenv("TELLA_MUSIC_LIBRARY", str(root))
    return root
