import asyncio
import hashlib
import inspect
import json
import os
import socket
from pathlib import Path

import pytest
from PIL import Image

from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.production import file_sha256
from tella.production_lock import JobLockConflict, ProductionJobLock
from tella.scene_regeneration import (
    SceneCorrection, build_corrected_provider_prompt, build_regeneration_envelope,
    normalize_scene_indices, regenerate_scenes,
)
import tella.scene_regeneration as scene_regeneration


ROOT = Path(__file__).resolve().parents[1]


def _source_job(tmp_path):
    job = tmp_path / "source"
    assets = job / "assets"
    assets.mkdir(parents=True)
    plan = plan_practical_life_steps_from_script(
        user_script=(ROOT / "script_practical_life_steps_test.txt").read_text(encoding="utf-8"),
        target_lang="vi",
    )
    plan.recipe_id = "practical_life_steps_callirrhoe_v1"
    plan.recipe_version = 1
    plan.narration_audio_filename = "assets/narration.wav"
    plan.narration_audio_path = str(assets / "narration.wav")
    for index, scene in enumerate((s for s in plan.scenes if s.kind == "scene"), 1):
        relative = f"assets/scene_{index:02d}_{scene.scene_role}.jpg"
        image = job / relative
        Image.new("RGB", (20, 30), (20 * index, 80, 120)).save(image)
        scene.image_filenames = [relative]
        scene.asset_path = relative
        scene.asset_hash = file_sha256(image)[:24]
        scene.asset_status = "done"
        scene.image_source = "ai_image_provider"
        scene.image_provider = "cloudflare"
        scene.start = float(index - 1) * 4
        scene.duration = 4
    plan.total_duration = 28
    (assets / "narration.wav").write_bytes(b"normalized narration")
    (assets / "narration_raw.wav").write_bytes(b"raw narration")
    (assets / "final_mixed_audio.m4a").write_bytes(b"accepted mixed audio")
    (job / "alignment_metadata.json").write_text('{"aligned":true}')
    (job / "subtitles.json").write_text('{"unchanged":true}')
    (job / "music_metadata.json").write_text('{"track":"practical_calm_01"}')
    (job / "recipe.json").write_text('{"recipe_id":"practical_life_steps_callirrhoe_v1"}')
    (job / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    images = []
    for scene in plan.scenes:
        if scene.kind == "scene":
            image = job / scene.image_filenames[0]
            images.append({"path": scene.image_filenames[0], "sha256": file_sha256(image)})
    (job / "production_manifest.json").write_text(json.dumps({
        "production_schema_version": 1, "recipe_fingerprint": "source-fingerprint",
        "image_artifacts": images, "artifact_hashes": {"plan": file_sha256(job / "plan.json")},
    }), encoding="utf-8")
    return job


def _snapshot(job):
    return {p.relative_to(job).as_posix(): p.read_bytes() for p in job.rglob("*")
            if p.is_file() and p.name != ".tella-job.lock"}


async def _fake_provider(prompt, output, scene_index):
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 30), (220, scene_index, 30)).save(output)
    return output


def test_dry_run_validates_exact_envelope_and_writes_nothing(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    before = _snapshot(source)
    result = asyncio.run(regenerate_scenes(
        source, target, scene_indices=[4], reason="action_mismatch",
        max_ai_images=4, dry_run=True,
    ))
    assert result["expected_image_request_count"] == 1
    assert result["image_request_budget"] == 1
    assert result["reused_scene_indices"] == [1, 2, 3, 5, 6, 7]
    assert result["external_calls_performed"] == 0
    assert not target.exists() and _snapshot(source) == before


def test_dry_run_output_is_deterministic_and_atomic(tmp_path):
    source = _source_job(tmp_path)
    output = tmp_path / "reports" / "dry-run.json"
    kwargs = dict(scene_indices=[4, 3, 4], reason="Unicode ghi chú", max_ai_images=2,
                  dry_run=True, output=output)
    first = asyncio.run(regenerate_scenes(source, tmp_path / "target", **kwargs))
    first_bytes = output.read_bytes()
    second = asyncio.run(regenerate_scenes(source, tmp_path / "target", **kwargs))
    assert first == second and output.read_bytes() == first_bytes
    assert not list(output.parent.glob("*.tmp"))


@pytest.mark.parametrize("indices,budget,message", [
    ([], 1, "at least one"), ([0], 1, "range"), ([8], 1, "range"),
    ([3, 4], 1, "lower"),
])
def test_invalid_selection_fails_before_provider_or_target(tmp_path, indices, budget, message):
    source = _source_job(tmp_path)
    calls = 0
    async def provider(*args):
        nonlocal calls
        calls += 1
    with pytest.raises(ValueError, match=message):
        asyncio.run(regenerate_scenes(source, tmp_path / "target",
            scene_indices=indices, reason="invalid", max_ai_images=budget,
            image_provider=provider, no_render=True))
    assert calls == 0 and not (tmp_path / "target").exists()


def test_duplicate_indices_normalize_deterministically():
    assert normalize_scene_indices([4, 3, 4, 3]) == [3, 4]


def test_selected_only_regenerated_and_all_upstream_hashes_reused(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "derived"
    before = _snapshot(source)
    calls = []
    async def provider(prompt, output, index):
        calls.append((index, prompt))
        return await _fake_provider(prompt, output, index)
    result = asyncio.run(regenerate_scenes(
        source, target, scene_indices=[3, 4], reason="action_mismatch",
        max_ai_images=7, image_provider=provider, no_render=True,
    ))
    assert [item[0] for item in calls] == [3, 4]
    assert result["actual_image_request_count"] == 2
    assert result["retry_count"] == result["fallback_count"] == 0
    assert _snapshot(source) == before
    for index in [1, 2, 5, 6, 7]:
        source_image = next((source / "assets").glob(f"scene_{index:02d}_*.jpg"))
        assert file_sha256(source_image) == file_sha256(target / source_image.relative_to(source))
    for name in ["narration.wav", "narration_raw.wav", "final_mixed_audio.m4a"]:
        assert file_sha256(source / "assets" / name) == file_sha256(target / "assets" / name)
    for name in ["alignment_metadata.json", "subtitles.json", "music_metadata.json"]:
        assert file_sha256(source / name) == file_sha256(target / name)
    metadata = json.loads((target / "scene_regeneration.json").read_text())
    assert metadata["status"] == "render_required" and metadata["render_state"] == "required"


def test_scene_timing_narration_and_subtitles_are_unchanged(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    asyncio.run(regenerate_scenes(source, target, scene_indices=[4], reason="text",
                max_ai_images=1, image_provider=_fake_provider, no_render=True))
    old = json.loads((source / "plan.json").read_text(encoding="utf-8"))
    new = json.loads((target / "plan.json").read_text(encoding="utf-8"))
    old_scenes = {s["scene_index"]: s for s in old["scenes"] if s["kind"] == "scene"}
    new_scenes = {s["scene_index"]: s for s in new["scenes"] if s["kind"] == "scene"}
    for index in range(1, 8):
        assert (new_scenes[index]["start"], new_scenes[index]["duration"], new_scenes[index]["voice_script"]) == (
            old_scenes[index]["start"], old_scenes[index]["duration"], old_scenes[index]["voice_script"])
    assert new["subtitle_segments"] == old["subtitle_segments"]


def test_corrective_constraints_augment_selected_prompt_only(tmp_path):
    source = _source_job(tmp_path)
    plan = json.loads((source / "plan.json").read_text(encoding="utf-8"))
    correction = SceneCorrection(
        scene_index=4, reason="action_mismatch",
        must_show=["phone resting outside reach"],
        must_not_show=["character holding the phone"], forbidden_text=True,
        object_state="phone resting away from work", requested_action="place the phone down",
        character_lock_notes="same adult and clothing", composition_notes="phone visible at frame edge",
    )
    target = tmp_path / "target"
    prompts = {}
    async def provider(prompt, output, index):
        prompts[index] = prompt
        return await _fake_provider(prompt, output, index)
    asyncio.run(regenerate_scenes(source, target, scene_indices=[4], reason="action_mismatch",
                max_ai_images=1, corrections={4: correction}, image_provider=provider, no_render=True))
    prompt = prompts[4]
    assert "Minimalist hand-drawn" in prompt
    assert "phone resting outside reach" in prompt
    assert "Must not show: character holding the phone" in prompt
    assert "no readable words, labels, logos, UI text" in prompt
    target_plan = json.loads((target / "plan.json").read_text(encoding="utf-8"))
    assert target_plan["scenes"][0]["image_prompt"] == plan["scenes"][0]["image_prompt"]
    metadata = json.loads((target / "scene_regeneration.json").read_text(encoding="utf-8"))
    request = metadata["provider_requests"][0]
    assert request["correction"]["must_not_show"] == ["character holding the phone"]
    assert request["maximum_transport_attempts"] == 1


def test_corrections_reject_secrets_unselected_and_stale_hash(tmp_path):
    with pytest.raises(ValueError):
        SceneCorrection(scene_index=1, reason="API_KEY=secret")
    source = _source_job(tmp_path)
    correction = SceneCorrection(scene_index=2, reason="wrong scene")
    with pytest.raises(ValueError, match="unselected"):
        build_regeneration_envelope(source, tmp_path / "target", scene_indices=[1],
            reason="x", max_ai_images=1, corrections={2: correction})
    stale = SceneCorrection(scene_index=1, reason="stale", source_image_sha256="0" * 64)
    with pytest.raises(ValueError, match="hash"):
        build_regeneration_envelope(source, tmp_path / "target", scene_indices=[1],
            reason="x", max_ai_images=1, corrections={1: stale})


def test_stale_source_manifest_image_fails_before_provider(tmp_path):
    source = _source_job(tmp_path)
    next((source / "assets").glob("scene_04_*.jpg")).write_bytes(b"changed")
    calls = 0
    async def provider(*args):
        nonlocal calls
        calls += 1
    with pytest.raises(ValueError, match="production manifest"):
        asyncio.run(regenerate_scenes(source, tmp_path / "target", scene_indices=[1],
            reason="x", max_ai_images=1, image_provider=provider, no_render=True))
    assert calls == 0 and not (tmp_path / "target").exists()


def test_provider_failure_preserves_source_and_records_safe_failure(tmp_path):
    source = _source_job(tmp_path)
    before = _snapshot(source)
    async def fail(prompt, output, index):
        raise RuntimeError("Authorization Bearer secret")
    with pytest.raises(RuntimeError):
        asyncio.run(regenerate_scenes(source, tmp_path / "target", scene_indices=[1],
            reason="x", max_ai_images=1, image_provider=fail, no_render=True))
    assert _snapshot(source) == before
    text = (tmp_path / "target" / "scene_regeneration.json").read_text()
    assert "Authorization Bearer" not in text
    assert "credential-bearing details redacted" in text
    assert json.loads(text)["actual_image_request_count"] == 1
    summary = json.loads((tmp_path / "target" / "production_summary.json").read_text())
    assert summary["status"] == "provider_failure" and summary["resumable"] is False
    assert summary["regeneration_resume_supported"] is False
    assert "new empty target" in summary["recommended_resume_action"]
    assert not (source / ".tella-job.lock").exists()
    assert not (tmp_path / "target" / ".tella-job.lock").exists()


@pytest.mark.parametrize("locked", ["source", "target"])
def test_lock_conflict_modifies_no_artifacts_and_calls_nothing(tmp_path, locked):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    if locked == "target":
        target.mkdir()
    job = source if locked == "source" else target
    before_source = _snapshot(source)
    with ProductionJobLock(job) as lock:
        lock_bytes = lock.path.read_bytes()
        calls = 0
        async def provider(*args):
            nonlocal calls
            calls += 1
        with pytest.raises(JobLockConflict):
            asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
                max_ai_images=1, image_provider=provider, no_render=True))
        assert lock.path.read_bytes() == lock_bytes
        assert calls == 0 and _snapshot(source) == before_source
        if locked == "target":
            assert _snapshot(target) == {}


def test_render_receives_existing_audio_and_preserved_timing(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    rendered = []
    async def renderer(plan, job, audio):
        rendered.append(([(s.start, s.duration) for s in plan.scenes if s.kind == "scene"], audio.read_bytes()))
        video = job / "video.mp4"
        video.write_bytes(b"synthetic video")
        return video
    result = asyncio.run(regenerate_scenes(source, target, scene_indices=[7], reason="x",
        max_ai_images=1, image_provider=_fake_provider, renderer=renderer))
    assert rendered[0][1] == b"accepted mixed audio"
    assert rendered[0][0] == [(float(i - 1) * 4, 4.0) for i in range(1, 8)]
    assert result["render_operations_performed"] == 1
    assert result["qc_state"] == "technical_qc_required"


def test_completed_claim_requires_both_technical_qc_results(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    async def renderer(plan, job, audio):
        (job / "video.mp4").write_bytes(b"synthetic video")
        (job / "audio_qc.json").write_text('{"status":"passed"}')
        (job / "video_qc.json").write_text('{"status":"passed"}')
        return job / "video.mp4"
    result = asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
        max_ai_images=1, image_provider=_fake_provider, renderer=renderer))
    assert result["status"] == "completed" and result["qc_state"] == "passed"
    summary = json.loads((target / "production_summary.json").read_text())
    manifest = json.loads((target / "production_manifest.json").read_text())
    assert summary["status"] == "completed" and summary["resumable"] is False
    assert manifest["qc_results"] == {"audio": "passed", "video": "passed"}


def test_workflow_has_no_tts_music_alignment_or_socket_path(tmp_path, monkeypatch):
    source = _source_job(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("network/socket path called")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("tella.tts.gemini.synthesize", forbidden)
    monkeypatch.setattr("tella.tts.edge.synthesize", forbidden)
    monkeypatch.setattr("tella.tts.synth_all.synthesize_all", forbidden)
    monkeypatch.setattr("tella.music.audio.prepare_music", forbidden)
    monkeypatch.setattr("tella.tts.sentence_alignment.align_sentences", forbidden)
    result = asyncio.run(regenerate_scenes(source, tmp_path / "target", scene_indices=[2],
        reason="local", max_ai_images=1, image_provider=_fake_provider, no_render=True))
    assert result["external_calls_performed"] == 0
    assert result["reused_artifacts"] == [
        "narration", "mixed_audio", "alignment", "boundary_metadata",
        "subtitles", "scene_timing", "music_metadata", "recipe", "voice_metadata",
    ]


def test_reused_files_are_independent_regular_copies(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    asyncio.run(regenerate_scenes(source, target, scene_indices=[4], reason="copy audit",
                max_ai_images=1, image_provider=_fake_provider, no_render=True))
    relatives = [
        "assets/scene_01_hook.jpg", "assets/narration.wav",
        "assets/final_mixed_audio.m4a", "subtitles.json",
        "alignment_metadata.json", "plan.json", "recipe.json", "music_metadata.json",
    ]
    for relative in relatives:
        src, dst = source / relative, target / relative
        assert src.is_file() and dst.is_file()
        assert file_sha256(src) == file_sha256(dst)
        assert not src.is_symlink() and not dst.is_symlink()
        assert not os.path.samefile(src, dst)
    source_audio = (source / "assets/narration.wav").read_bytes()
    (target / "assets/narration.wav").write_bytes(b"target changed")
    assert (source / "assets/narration.wav").read_bytes() == source_audio
    (target / "subtitles.json").unlink()
    assert (source / "subtitles.json").is_file()


@pytest.mark.parametrize("existing", ["unrelated.txt", "production_summary.json", "scene_regeneration.json"])
def test_nonempty_or_completed_or_incomplete_target_is_rejected(tmp_path, existing):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / existing).write_text("existing", encoding="utf-8")
    before = _snapshot(target)
    calls = 0
    async def provider(*args):
        nonlocal calls
        calls += 1
    with pytest.raises(ValueError, match="resume is unsupported"):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=provider, no_render=True))
    assert calls == 0 and _snapshot(target) == before


@pytest.mark.parametrize("precreate", [False, True])
def test_new_and_existing_empty_targets_are_allowed(tmp_path, precreate):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    if precreate:
        target.mkdir()
    result = asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
        max_ai_images=1, image_provider=_fake_provider, no_render=True))
    assert result["target_job_id"] == "target"


@pytest.mark.parametrize("relation", ["same", "nested", "ancestor"])
def test_same_nested_or_ancestor_target_is_rejected(tmp_path, relation):
    source = _source_job(tmp_path / "parent")
    target = {"same": source, "nested": source / "derived", "ancestor": source.parent}[relation]
    with pytest.raises(ValueError, match="separate non-nested|same filesystem"):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=_fake_provider, no_render=True))


def test_symlink_and_junction_aliases_are_rejected_conservatively(tmp_path, monkeypatch):
    source = _source_job(tmp_path)
    alias = tmp_path / "alias"
    alias.mkdir()
    original_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == alias or original_symlink(self))
    with pytest.raises(ValueError, match="symbolic links or junctions"):
        asyncio.run(regenerate_scenes(alias, tmp_path / "target", scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=_fake_provider, no_render=True))
    monkeypatch.setattr(Path, "is_symlink", original_symlink)
    junction = tmp_path / "junction"
    junction.mkdir()
    original = getattr(Path, "is_junction", None)
    monkeypatch.setattr(Path, "is_junction", lambda self: self == junction, raising=False)
    with pytest.raises(ValueError, match="symbolic links or junctions"):
        asyncio.run(regenerate_scenes(source, junction, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=_fake_provider, no_render=True))
    if original is None:
        monkeypatch.delattr(Path, "is_junction", raising=False)


def test_second_lock_failure_releases_first_and_creates_no_artifacts(tmp_path, monkeypatch):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    original = ProductionJobLock.acquire
    calls = 0
    first_path = None
    def acquire(lock):
        nonlocal calls, first_path
        calls += 1
        if calls == 2:
            raise JobLockConflict("synthetic second-lock conflict")
        result = original(lock)
        first_path = lock.path
        return result
    monkeypatch.setattr(ProductionJobLock, "acquire", acquire)
    with pytest.raises(JobLockConflict, match="second-lock"):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=_fake_provider, no_render=True))
    assert calls == 2 and first_path is not None and not first_path.exists()
    assert not target.exists() or _snapshot(target) == {}


def test_source_validation_occurs_only_while_both_locks_are_held(tmp_path, monkeypatch):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    original = scene_regeneration._source_record
    original_acquire = ProductionJobLock.acquire
    observed = []
    lock_order = []
    def acquire(lock):
        lock_order.append(lock.job_dir.resolve())
        return original_acquire(lock)
    def guarded_source_record(*args, **kwargs):
        observed.append(((source / ".tella-job.lock").is_file(),
                         (target / ".tella-job.lock").is_file()))
        return original(*args, **kwargs)
    monkeypatch.setattr(scene_regeneration, "_source_record", guarded_source_record)
    monkeypatch.setattr(ProductionJobLock, "acquire", acquire)
    asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
                max_ai_images=1, image_provider=_fake_provider, no_render=True))
    assert observed == [(True, True)]
    assert lock_order == sorted((source.resolve(), target.resolve()),
                                key=lambda path: str(path).casefold())


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(9)])
def test_interrupt_and_system_exit_release_both_locks(tmp_path, exc):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    async def fail(*args):
        raise exc
    with pytest.raises(type(exc)):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=fail, no_render=True))
    assert not (source / ".tella-job.lock").exists()
    assert not (target / ".tella-job.lock").exists()


@pytest.mark.parametrize("failure", ["render", "metadata"])
def test_render_and_metadata_failure_release_both_locks(tmp_path, monkeypatch, failure):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    async def renderer(*args):
        raise RuntimeError("render exploded")
    if failure == "metadata":
        async def renderer(plan, job, audio):
            (job / "video.mp4").write_bytes(b"video")
            return job / "video.mp4"
        monkeypatch.setattr(scene_regeneration, "_write_derived_state",
                            lambda *args: (_ for _ in ()).throw(OSError("metadata failed")))
    with pytest.raises((RuntimeError, OSError)):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=_fake_provider, renderer=renderer))
    assert not (source / ".tella-job.lock").exists()
    assert not (target / ".tella-job.lock").exists()


def test_lock_token_mismatch_preserves_another_owners_lock(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    async def provider(prompt, output, index):
        result = await _fake_provider(prompt, output, index)
        lock_path = target / ".tella-job.lock"
        metadata = json.loads(lock_path.read_text())
        metadata["lock_token"] = "another-owner"
        lock_path.write_text(json.dumps(metadata), encoding="utf-8")
        return result
    asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
                max_ai_images=1, image_provider=provider, no_render=True))
    assert json.loads((target / ".tella-job.lock").read_text())["lock_token"] == "another-owner"


def test_source_race_after_validation_is_detected_before_provider(tmp_path, monkeypatch):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    original = scene_regeneration._copy_reusable_tree
    def racing_copy(*args):
        copied = original(*args)
        (source / "assets/narration.wav").write_bytes(b"raced")
        return copied
    monkeypatch.setattr(scene_regeneration, "_copy_reusable_tree", racing_copy)
    calls = 0
    async def provider(*args):
        nonlocal calls
        calls += 1
    with pytest.raises(RuntimeError, match="changed while locked"):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[1], reason="x",
            max_ai_images=1, image_provider=provider, no_render=True))
    assert calls == 0


def test_two_scene_partial_provider_failure_is_nonresumable_and_preserved(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    before = _snapshot(source)
    calls = []
    async def provider(prompt, output, index):
        calls.append(index)
        if index == 4:
            raise RuntimeError("safe provider failure")
        return await _fake_provider(prompt, output, index)
    with pytest.raises(RuntimeError, match="safe provider"):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[3, 4], reason="x",
            max_ai_images=2, image_provider=provider, no_render=True))
    metadata = json.loads((target / "scene_regeneration.json").read_text())
    summary = json.loads((target / "production_summary.json").read_text())
    assert calls == [3, 4] and metadata["actual_image_request_count"] == 2
    assert (target / "assets/scene_03_practical_step.jpg").is_file()
    assert metadata["regenerated_image_hashes"].keys() == {"3"}
    assert metadata["render_operations_performed"] == 0
    assert summary["status"] == "provider_failure" and summary["resumable"] is False
    assert summary["partial_artifacts_reusable"] is False
    assert metadata["retry_count"] == metadata["fallback_count"] == 0
    assert _snapshot(source) == before
    with pytest.raises(ValueError, match="resume is unsupported"):
        asyncio.run(regenerate_scenes(source, target, scene_indices=[3, 4], reason="x",
            max_ai_images=2, image_provider=provider, no_render=True))


def test_derived_lineage_and_reused_hash_maps_are_complete(tmp_path):
    source = _source_job(tmp_path)
    target = tmp_path / "target"
    result = asyncio.run(regenerate_scenes(source, target, scene_indices=[4], reason="lineage",
        max_ai_images=1, image_provider=_fake_provider, no_render=True))
    assert result["source_job_id"] == "source"
    assert result["source_manifest_sha256"] == file_sha256(source / "production_manifest.json")
    assert result["source_plan_sha256"] == file_sha256(source / "plan.json")
    assert result["source_production_fingerprint"] == "source-fingerprint"
    assert result["source_recipe_id"] == "practical_life_steps_callirrhoe_v1"
    assert result["regenerated_scene_indices"] == [4]
    assert result["reused_scene_indices"] == [1, 2, 3, 5, 6, 7]
    assert set(result["reused_image_hashes"]) == {"1", "2", "3", "5", "6", "7"}
    assert set(result["regenerated_image_hashes"]) == {"4"}
    manifest = json.loads((target / "production_manifest.json").read_text())
    assert manifest["derived_job_type"] == "scene_regeneration"
    assert len(manifest["image_artifacts"]) == 7


def test_real_provider_adapter_is_one_account_one_attempt_per_selected_scene(tmp_path, monkeypatch):
    source = _source_job(tmp_path)
    calls = []
    async def fake_generate(prompt, output, **kwargs):
        calls.append(kwargs)
        Image.new("RGB", (20, 30), "#334455").save(output)
        return output
    monkeypatch.setattr("tella.media.ai_image.generate_image", fake_generate)
    result = asyncio.run(regenerate_scenes(source, tmp_path / "target",
        scene_indices=[2, 5], reason="x", max_ai_images=7, no_render=True))
    assert result["actual_image_request_count"] == 2 and len(calls) == 2
    assert all(call["max_accounts"] == 1 and call["max_attempts_per_account"] == 1 for call in calls)
    assert all(call["model"] == "@cf/black-forest-labs/flux-1-schnell" for call in calls)
    assert result["retry_count"] == result["fallback_count"] == 0


def test_render_api_defaults_preserve_existing_callers_and_copy_accepted_audio(monkeypatch, tmp_path):
    from tella.render import pipeline
    signature = inspect.signature(pipeline.render)
    assert signature.parameters["preserve_timing"].default is False
    assert signature.parameters["existing_mixed_audio"].default is None
    commands = []
    class Process:
        returncode = 0
        async def communicate(self): return b"", b""
    async def subprocess(*args, **kwargs):
        commands.append(args)
        return Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", subprocess)
    awaitable = pipeline._mux_audio(tmp_path / "silent.mp4", tmp_path / "audio.m4a",
                                    tmp_path / "out.mp4", copy_audio=True)
    asyncio.run(awaitable)
    assert "copy" in commands[0] and "aac" not in commands[0]


def test_unicode_notes_and_windows_compatible_space_paths(tmp_path):
    source = _source_job(tmp_path / "nguồn có khoảng trắng")
    target = tmp_path / "đích có khoảng trắng"
    result = asyncio.run(regenerate_scenes(source, target, scene_indices=[6],
        reason="Cần sửa hành động rõ ràng", max_ai_images=1,
        image_provider=_fake_provider, no_render=True))
    assert result["reason"] == "Cần sửa hành động rõ ràng"
    assert target.is_dir()
