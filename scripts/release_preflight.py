"""Zero-network local release-candidate preflight for Tella."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from tella.atomic_write import atomic_write_json

SCHEMA_VERSION = 1
PRODUCTION_RECIPE = "practical_life_steps_callirrhoe_v1"
PRODUCTION_VOICE = "gemini_callirrhoe_vi_natural_smile"
MEDIA_SUFFIXES = {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".avi", ".mkv"}


@dataclass(frozen=True)
class CheckResult:
    name: str
    result: str
    message: str


def _run(repo: Path, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=repo, text=True, capture_output=True, check=False,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(repo, ["git", *args])


def _tracked_paths(repo: Path) -> list[str]:
    result = _git(repo, "ls-files", "-z")
    if result.returncode:
        raise RuntimeError("unable to list tracked files")
    return sorted(item for item in result.stdout.split("\0") if item)


def tracking_status(repo: Path) -> tuple[str, str]:
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.returncode:
        return "unknown", "no local tracking reference is available"
    counts = _git(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    try:
        ahead, behind = (int(value) for value in counts.stdout.split())
    except (ValueError, TypeError):
        return "unknown", "local tracking relationship could not be determined"
    if ahead and behind:
        return "diverged", f"ahead {ahead}, behind {behind}"
    if ahead:
        return "ahead", f"ahead {ahead}"
    if behind:
        return "behind", f"behind {behind}"
    return "synchronized", "HEAD matches the locally available tracking reference"


def git_and_hygiene_checks(repo: Path, *, allow_dirty: bool) -> tuple[list[CheckResult], dict]:
    checks: list[CheckResult] = []
    branch_result = _git(repo, "branch", "--show-current")
    head_result = _git(repo, "rev-parse", "HEAD")
    if branch_result.returncode or head_result.returncode:
        return [CheckResult("git_repository", "FAIL", "not a readable Git repository")], {
            "branch": "", "head": "", "tracking_status": "unknown",
        }
    branch = branch_result.stdout.strip()
    head = head_result.stdout.strip()
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
    conflicts = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    staged = [line for line in status if len(line) >= 2 and line[0] not in {" ", "?"}]
    dirty = bool(status)
    checks.append(CheckResult(
        "worktree_clean",
        "WARN" if dirty and allow_dirty else ("FAIL" if dirty else "PASS"),
        "dirty worktree explicitly allowed for diagnostics" if dirty and allow_dirty else (
            "worktree contains changes" if dirty else "worktree is clean"
        ),
    ))
    checks.append(CheckResult("staged_changes", "FAIL" if staged else "PASS",
                              "staged changes exist" if staged else "no staged changes"))
    checks.append(CheckResult("merge_conflicts", "FAIL" if conflicts else "PASS",
                              "unresolved merge conflicts exist" if conflicts else "no unresolved conflicts"))
    tracked = _tracked_paths(repo)
    tracked_out = [path for path in tracked if path == "out" or path.startswith("out/")]
    tracked_media = [
        path for path in tracked
        if Path(path).suffix.lower() in MEDIA_SUFFIXES
        and not path.replace("\\", "/").startswith("tests/fixtures/")
    ]
    tracked_mp3 = [path for path in tracked if path.startswith("music/tracks/") and path.lower().endswith(".mp3")]
    secret_names = []
    for path in tracked:
        name = Path(path).name.lower()
        if name == ".env.example":
            continue
        if (
            name == ".env" or name.startswith(".env.") or name.endswith((".pem", ".key"))
            or "service-account" in name or "credential" in name
            or "secret" in name or "token" in name
            or "api_key" in name or "api-key" in name or "apikey" in name
        ):
            secret_names.append(path)
    checks.extend([
        CheckResult("tracked_output", "FAIL" if tracked_out else "PASS",
                    "tracked files exist under out/" if tracked_out else "no tracked output files"),
        CheckResult("tracked_media", "FAIL" if tracked_media else "PASS",
                    "tracked binary media found" if tracked_media else "no unapproved tracked binary media"),
        CheckResult("tracked_production_mp3", "FAIL" if tracked_mp3 else "PASS",
                    "production MP3 is tracked" if tracked_mp3 else "production MP3 is not tracked"),
        CheckResult("tracked_credentials", "FAIL" if secret_names else "PASS",
                    "credential-like filename is tracked" if secret_names else "no credential-like files tracked"),
    ])
    track_state, track_message = tracking_status(repo)
    checks.append(CheckResult("tracking_status", "WARN" if track_state == "unknown" else "PASS", track_message))
    return checks, {"branch": branch, "head": head, "tracking_status": track_state}


def runtime_contract_checks(repo: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        from tella.production import CALLIRRHOE_PRODUCTION_CONFIG
        from tella.recipes import get_recipe
        from tella.voice_profiles import get_voice_profile
        config = CALLIRRHOE_PRODUCTION_CONFIG
        recipe = get_recipe(PRODUCTION_RECIPE)
        voice = get_voice_profile(PRODUCTION_VOICE)
        checks.extend([
            CheckResult("production_recipe", "PASS" if recipe.recipe_id == PRODUCTION_RECIPE else "FAIL",
                        "production recipe resolved" if recipe.recipe_id == PRODUCTION_RECIPE else "production recipe mismatch"),
            CheckResult("voice_profile", "PASS" if voice.profile_id == PRODUCTION_VOICE else "FAIL",
                        "Callirrhoe voice profile resolved" if voice.profile_id == PRODUCTION_VOICE else "voice profile mismatch"),
            CheckResult(
                "request_limits",
                "PASS" if (
                    config.max_tts_requests == 1 and config.max_image_requests == 7
                    and config.tts_attempts == 1 and not config.tts_retry
                    and not config.edge_fallback and not config.model_fallback
                ) else "FAIL",
                "Gemini=1, images=7, retries=0, fallbacks=0",
            ),
        ])
    except Exception as exc:
        checks.append(CheckResult("production_contract", "FAIL", f"production contract unavailable: {type(exc).__name__}"))
    required = [
        repo / "tella" / "atomic_write.py", repo / "tella" / "production_lock.py",
        repo / "tella" / "production.py", repo / "tella" / "cli.py",
        repo / "music" / "library.json", repo / "music" / "licenses" / "practical_calm_01.txt",
    ]
    missing = [path.name for path in required if not path.is_file()]
    checks.append(CheckResult("required_sources", "FAIL" if missing else "PASS",
                              "required source files missing" if missing else "required source files exist"))
    return checks


def command_check(repo: Path, name: str, command: Sequence[str]) -> CheckResult:
    result = _run(repo, command)
    return CheckResult(name, "PASS" if result.returncode == 0 else "FAIL",
                       "command passed" if result.returncode == 0 else f"command failed with exit {result.returncode}")


def build_report(
    repo: Path,
    *,
    allow_dirty: bool = False,
    skip_tests: bool = False,
    expected_branch: str = "",
    expected_head: str = "",
) -> dict:
    repo = repo.resolve()
    checks, git_info = git_and_hygiene_checks(repo, allow_dirty=allow_dirty)
    if expected_branch:
        checks.append(CheckResult("expected_branch", "PASS" if git_info["branch"] == expected_branch else "FAIL",
                                  "branch matches expectation" if git_info["branch"] == expected_branch else "branch mismatch"))
    if expected_head:
        matches = git_info["head"].lower().startswith(expected_head.lower())
        checks.append(CheckResult("expected_head", "PASS" if matches else "FAIL",
                                  "HEAD matches expectation" if matches else "HEAD mismatch"))
    checks.extend(runtime_contract_checks(repo))
    checks.append(command_check(repo, "compile", [sys.executable, "-m", "compileall", "-q", "tella", "scripts"]))
    checks.append(command_check(repo, "cli_help", [sys.executable, "-m", "tella", "--help"]))
    checks.append(command_check(repo, "git_diff_check", ["git", "diff", "--check"]))
    if skip_tests:
        checks.append(CheckResult("tests", "WARN", "tests explicitly skipped; diagnostic result is not release-ready"))
    else:
        focused = [
            "tests/test_release_preflight.py", "tests/test_atomic_writes.py",
            "tests/test_production_lock.py", "tests/test_synthetic_music_fixture.py",
            "tests/test_production_resume.py", "tests/test_practical_callirrhoe_recipe.py",
            "tests/test_gemini_tts.py",
        ]
        checks.append(command_check(repo, "focused_tests", [sys.executable, "-m", "pytest", "-q", *focused]))
        checks.append(command_check(repo, "full_tests", [sys.executable, "-m", "pytest", "-q"]))
    serialized = [asdict(item) for item in checks]
    release_ready = not skip_tests and not any(item.result == "FAIL" for item in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(repo),
        **git_info,
        "checks": serialized,
        "release_ready": release_ready,
        "external_call_count": 0,
    }


def _print_human(report: dict) -> None:
    for check in report["checks"]:
        print(f"{check['result']:4} {check['name']}: {check['message']}")
    print(f"release_ready={str(report['release_ready']).lower()} external_calls=0")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--expected-branch", default="")
    parser.add_argument("--expected-head", default="")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    report = build_report(
        repo, allow_dirty=args.allow_dirty, skip_tests=args.skip_tests,
        expected_branch=args.expected_branch, expected_head=args.expected_head,
    )
    if args.output:
        atomic_write_json(args.output, report)
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
