import json
import os
from pathlib import Path

from pytest import MonkeyPatch

import conftest
from tella.music.library import default_library_root, load_library


def test_fixture_is_opt_in_and_function_scoped(monkeypatch):
    marker = conftest.synthetic_practical_music_library._fixture_function_marker
    assert marker.autouse is False
    assert marker.scope == "function"
    monkeypatch.delenv("TELLA_MUSIC_LIBRARY", raising=False)
    assert default_library_root() == Path(__file__).resolve().parents[1] / "music"


def test_two_synthetic_catalogues_are_independent(tmp_path):
    first = conftest.create_synthetic_practical_music_library(tmp_path / "first")
    second = conftest.create_synthetic_practical_music_library(tmp_path / "second")
    assert first != second
    assert first.is_relative_to(tmp_path) and second.is_relative_to(tmp_path)
    assert load_library(first)["synthetic_practical_tone"].file_path != load_library(second)["synthetic_practical_tone"].file_path
    (first / "tracks" / "synthetic_practical_tone.wav").unlink()
    assert (second / "tracks" / "synthetic_practical_tone.wav").is_file()


def test_temporary_music_environment_is_restored(tmp_path):
    original = os.environ.get("TELLA_MUSIC_LIBRARY")
    root = conftest.create_synthetic_practical_music_library(tmp_path)
    with MonkeyPatch.context() as scoped:
        scoped.setenv("TELLA_MUSIC_LIBRARY", str(root))
        assert default_library_root() == root.resolve()
    assert os.environ.get("TELLA_MUSIC_LIBRARY") == original


def test_production_catalogue_resolution_is_unchanged_outside_fixture(monkeypatch):
    monkeypatch.delenv("TELLA_MUSIC_LIBRARY", raising=False)
    root = default_library_root()
    catalogue = json.loads((root / "library.json").read_text(encoding="utf-8"))
    practical = next(item for item in catalogue["tracks"] if item["track_id"] == "practical_calm_01")
    assert root == Path(__file__).resolve().parents[1] / "music"
    assert practical["file_path"] == "tracks/practical_calm_01.mp3"
    assert practical["license_reference"] == "licenses/practical_calm_01.txt"
