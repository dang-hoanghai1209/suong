import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import release_preflight as preflight


def _git(repo: Path, *args: str):
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result


def _repo(tmp_path: Path, files=None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI Test")
    for name, content in (files or {"source.py": "print('ok')\n"}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _results(checks):
    return {item.name: item.result for item in checks}


def test_clean_repo_passes_and_tracking_is_local_unknown(tmp_path):
    repo = _repo(tmp_path)
    checks, info = preflight.git_and_hygiene_checks(repo, allow_dirty=False)
    results = _results(checks)
    assert results["worktree_clean"] == "PASS"
    assert results["staged_changes"] == "PASS"
    assert results["tracked_output"] == "PASS"
    assert info["tracking_status"] == "unknown"


def test_dirty_staged_and_untracked_output_policies(tmp_path):
    repo = _repo(tmp_path)
    (repo / "source.py").write_text("changed")
    assert _results(preflight.git_and_hygiene_checks(repo, allow_dirty=False)[0])["worktree_clean"] == "FAIL"
    assert _results(preflight.git_and_hygiene_checks(repo, allow_dirty=True)[0])["worktree_clean"] == "WARN"
    _git(repo, "add", "source.py")
    assert _results(preflight.git_and_hygiene_checks(repo, allow_dirty=True)[0])["staged_changes"] == "FAIL"
    _git(repo, "reset", "--hard", "HEAD")
    output = repo / "out" / "video.mp4"
    output.parent.mkdir(); output.write_bytes(b"untracked")
    checks, _ = preflight.git_and_hygiene_checks(repo, allow_dirty=True)
    assert _results(checks)["tracked_output"] == "PASS"
    assert _results(checks)["worktree_clean"] == "WARN"


@pytest.mark.parametrize("path", [
    "out/video.mp4", "music/tracks/practical_calm_01.mp3", ".env",
    "config/service-account-prod.json", "config/credentials.json",
])
def test_tracked_output_media_and_credentials_fail(tmp_path, path):
    repo = _repo(tmp_path, {path: "placeholder"})
    results = _results(preflight.git_and_hygiene_checks(repo, allow_dirty=False)[0])
    if path.startswith("out/"):
        assert results["tracked_output"] == "FAIL"
    if path.endswith((".mp3", ".mp4")):
        assert results["tracked_media"] == "FAIL"
    if path.endswith(".mp3"):
        assert results["tracked_production_mp3"] == "FAIL"
    if ".env" in path or "service-account" in path or "credential" in path:
        assert results["tracked_credentials"] == "FAIL"


def test_unresolved_merge_conflict_fails(tmp_path):
    repo = _repo(tmp_path, {"conflict.txt": "base\n"})
    _git(repo, "checkout", "-b", "other")
    (repo / "conflict.txt").write_text("other\n"); _git(repo, "commit", "-am", "other")
    _git(repo, "checkout", "main")
    (repo / "conflict.txt").write_text("main\n"); _git(repo, "commit", "-am", "main")
    subprocess.run(["git", "merge", "other"], cwd=repo, capture_output=True)
    assert _results(preflight.git_and_hygiene_checks(repo, allow_dirty=True)[0])["merge_conflicts"] == "FAIL"


def test_runtime_contract_and_request_limits(monkeypatch):
    assert all(item.result == "PASS" for item in preflight.runtime_contract_checks(Path.cwd()))
    import tella.production
    monkeypatch.setattr(tella.production, "CALLIRRHOE_PRODUCTION_CONFIG", SimpleNamespace(
        max_tts_requests=2, max_image_requests=7, tts_attempts=1, tts_retry=False,
        edge_fallback=False, model_fallback=False,
    ))
    results = _results(preflight.runtime_contract_checks(Path.cwd()))
    assert results["request_limits"] == "FAIL"


def test_missing_atomic_and_lock_sources_fail(tmp_path):
    results = _results(preflight.runtime_contract_checks(tmp_path))
    assert results["required_sources"] == "FAIL"


def test_missing_recipe_or_voice_profile_fails(monkeypatch):
    import tella.recipes
    import tella.voice_profiles
    monkeypatch.setattr(tella.recipes, "get_recipe", lambda value: (_ for _ in ()).throw(ValueError("missing")))
    assert _results(preflight.runtime_contract_checks(Path.cwd()))["production_contract"] == "FAIL"
    monkeypatch.undo()
    monkeypatch.setattr(tella.voice_profiles, "get_voice_profile", lambda value: SimpleNamespace(profile_id="wrong"))
    assert _results(preflight.runtime_contract_checks(Path.cwd()))["voice_profile"] == "FAIL"


def test_runtime_checks_need_no_credentials_provider_or_socket(monkeypatch):
    forbidden = lambda *a, **k: pytest.fail("provider or socket called")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    import tella.tts.gemini
    import tella.media.ai_image
    monkeypatch.setattr(tella.tts.gemini, "synthesize", forbidden)
    monkeypatch.setattr(tella.media.ai_image, "generate_image", forbidden)
    assert not any(item.result == "FAIL" for item in preflight.runtime_contract_checks(Path.cwd()))


def test_skip_tests_is_non_release_ready_and_schema_is_stable(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "git_and_hygiene_checks", lambda *a, **k: (
        [preflight.CheckResult("git", "PASS", "ok")],
        {"branch": "main", "head": "abc", "tracking_status": "synchronized"},
    ))
    monkeypatch.setattr(preflight, "runtime_contract_checks", lambda repo: [])
    monkeypatch.setattr(preflight, "command_check", lambda *a, **k: preflight.CheckResult(a[1], "PASS", "ok"))
    report = preflight.build_report(tmp_path, skip_tests=True)
    assert list(report) == [
        "schema_version", "timestamp_utc", "repository_root", "branch", "head",
        "tracking_status", "checks", "release_ready", "external_call_count",
    ]
    assert report["schema_version"] == 1
    assert report["release_ready"] is False
    assert report["external_call_count"] == 0


def test_main_exit_codes_and_atomic_json_output(monkeypatch, tmp_path):
    ready = {
        "schema_version": 1, "timestamp_utc": "x", "repository_root": "x",
        "branch": "main", "head": "abc", "tracking_status": "synchronized",
        "checks": [], "release_ready": True, "external_call_count": 0,
    }
    monkeypatch.setattr(preflight, "build_report", lambda *a, **k: ready)
    output = tmp_path / "preflight.json"
    assert preflight.main(["--json", "--output", str(output)]) == 0
    assert json.loads(output.read_text())["release_ready"] is True
    monkeypatch.setattr(preflight, "build_report", lambda *a, **k: {**ready, "release_ready": False})
    assert preflight.main(["--json"]) == 1


def test_ci_workflow_has_required_safe_structure():
    text = (Path.cwd() / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lower = text.lower()
    assert "windows-full:" in text and "linux-smoke:" in text
    assert "contents: read" in text
    assert "timeout-minutes:" in text
    assert "uv sync --locked --extra dev" in text
    assert "--junitxml=out/ci/windows-pytest.xml" in text
    assert "test_practical_callirrhoe_recipe.py" in text
    assert "test ! -e music/tracks/practical_calm_01.mp3" in text
    assert "download" not in lower or "production mp3" not in lower
    assert not any(word in lower for word in ("deploy", "publish", "create-release"))
    assert "secrets." not in lower
    assert "\t" not in text
    assert text.count("${{") == text.count("}}")
    assert all(
        (len(line) - len(line.lstrip(" "))) % 2 == 0
        for line in text.splitlines() if line.strip()
    )
    actions = [line.split("uses:", 1)[1].strip() for line in text.splitlines() if "uses:" in line]
    assert actions
    assert all(
        action.startswith(("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@", "astral-sh/setup-uv@"))
        for action in actions
    )
