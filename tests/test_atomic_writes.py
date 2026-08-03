import builtins
import json
from pathlib import Path

import pytest

import tella.atomic_write as atomic
import tella.production as production


def _temp_files(path: Path):
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_atomic_json_new_replace_and_unicode(tmp_path):
    destination = tmp_path / "Windows compatible folder" / "state.json"
    atomic.atomic_write_json(destination, {"value": "Tiếng Việt", "version": 1})
    assert json.loads(destination.read_text(encoding="utf-8"))["value"] == "Tiếng Việt"
    atomic.atomic_write_json(destination, {"value": "replaced", "version": 2})
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "value": "replaced", "version": 2,
    }
    assert _temp_files(destination) == []


def test_serialization_failure_preserves_existing_document(monkeypatch, tmp_path):
    destination = tmp_path / "state.json"
    destination.write_text('{"valid":true}', encoding="utf-8")
    before = destination.read_bytes()
    monkeypatch.setattr(atomic.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(TypeError("bad payload")))
    with pytest.raises(TypeError, match="bad payload"):
        atomic.atomic_write_json(destination, {"bad": object()})
    assert destination.read_bytes() == before
    assert _temp_files(destination) == []


def test_write_and_fsync_failures_preserve_existing_and_clean_temp(monkeypatch, tmp_path):
    destination = tmp_path / "state.json"
    destination.write_text('{"valid":true}', encoding="utf-8")
    before = destination.read_bytes()
    real_open = builtins.open

    class BrokenWriter:
        def __enter__(self):
            raise OSError("write failed")
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(builtins, "open", lambda path, *a, **k: BrokenWriter() if str(path).endswith(".tmp") else real_open(path, *a, **k))
    with pytest.raises(OSError, match="write failed"):
        atomic.atomic_write_text(destination, "replacement")
    assert destination.read_bytes() == before
    assert _temp_files(destination) == []

    monkeypatch.setattr(builtins, "open", real_open)
    monkeypatch.setattr(atomic.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync failed")))
    with pytest.raises(OSError, match="fsync failed"):
        atomic.atomic_write_text(destination, "replacement")
    assert destination.read_bytes() == before
    assert _temp_files(destination) == []


def test_replace_failure_preserves_existing_and_cleans_temp(monkeypatch, tmp_path):
    destination = tmp_path / "state.json"
    destination.write_text('{"valid":true}', encoding="utf-8")
    before = destination.read_bytes()
    monkeypatch.setattr(atomic.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        atomic.atomic_write_json(destination, {"valid": False})
    assert destination.read_bytes() == before
    assert json.loads(destination.read_text()) == {"valid": True}
    assert _temp_files(destination) == []


def test_production_manifest_and_summary_use_atomic_writer(monkeypatch, tmp_path):
    calls = []
    real = production.atomic_write_json

    def observed(path, payload, **kwargs):
        calls.append(Path(path).name)
        return real(path, payload, **kwargs)

    monkeypatch.setattr(production, "atomic_write_json", observed)
    run = production.ProductionRun(tmp_path, production.CALLIRRHOE_PRODUCTION_CONFIG)
    run.advance(production.ProductionStage.recipe_resolved)
    assert "production_manifest.json" in calls
    assert calls.count("production_summary.json") >= 2
    assert json.loads(run.manifest_path.read_text())["production_schema_version"] == 1
    assert json.loads(run.summary_path.read_text())["status"] == "partial_failure"


def test_failed_manifest_artifact_hash_update_preserves_exact_previous_bytes(
    monkeypatch, tmp_path
):
    run = production.ProductionRun(tmp_path, production.CALLIRRHOE_PRODUCTION_CONFIG)
    artifact = tmp_path / "plan.json"
    artifact.write_text("{}", encoding="utf-8")
    before = run.manifest_path.read_bytes()

    def fail_atomic_update(path, payload, **kwargs):
        raise OSError("simulated atomic manifest replacement failure")

    monkeypatch.setattr(production, "atomic_write_json", fail_atomic_update)
    with pytest.raises(OSError, match="replacement failure"):
        run.record_artifact_hashes({"plan": artifact})
    assert run.manifest_path.read_bytes() == before
