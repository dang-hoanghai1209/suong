import asyncio
import json
import re
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest
from pydantic import ValidationError

import tella.cli as cli_module
from tella._voice_pace import resolve_pace
from tella.composer.compose import compose_timing
from tella.planner.character_lock import apply_lock
from tella.planner.models import Scene, TellaScenePlan
from tella.planner.prompts import build_system_prompt
from tella.planner.story_planner import plan_story, plan_story_from_script
from tella.planner.symbolic_reel import (
    _highlight_words,
    enforce_symbolic_reel_plan,
)
from tella.render.text_overlay import (
    _highlight_token_indexes,
    _phrase_word_highlight_keys,
)
from tella.tts import synth_all
from tella.tts.providers import TTSResult


def _symbolic_plan() -> TellaScenePlan:
    stale_room_prompt = (
        "medium-wide bedroom scene, bed and window with curtain, bedside table, "
        "folded blanket"
    )
    return TellaScenePlan(
        title="Soft symbolic reel",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_symbolic_reel",
        voice_name="en-US-JennyNeural",
        voice_edge_rate="-7%",
        voice_gender="female",
        scenes=[
            Scene(
                scene_index=1,
                kind="scene",
                title="Tired heart",
                voice_script="Some days feel heavier than they should",
                scene_meaning="the weight of a tired day",
                symbolic_visual="small paper heart with one soft crack",
                emotional_metaphor="a tender feeling becoming visible",
                main_character_or_object="small paper heart",
                image_prompt=stale_room_prompt,
                stock_query="bedroom",
            ),
            Scene(
                scene_index=2,
                kind="scene",
                title="Small hope",
                voice_script="A quiet light still waits inside you",
                scene_meaning="hope staying alive quietly",
                symbolic_visual="tiny glowing dot beside a gray cloud",
                emotional_metaphor="small hope beside sadness",
                main_character_or_object="tiny glowing dot",
                image_prompt=stale_room_prompt,
                stock_query="bedroom",
            ),
            Scene(
                scene_index=3,
                kind="scene",
                title="Return",
                voice_script="You can come back to yourself slowly",
                scene_meaning="returning to self without hurry",
                symbolic_visual="small sprout growing from a thin pencil line",
                emotional_metaphor="calm growing from a small beginning",
                main_character_or_object="small sprout",
                image_prompt=stale_room_prompt,
                stock_query="bedroom",
            ),
        ],
    )


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModels:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake Gemini response queue exhausted")
        return _FakeResponse(self.responses.pop(0))


class _FakeClient:
    def __init__(self, responses: list[str]):
        self.models = _FakeModels(responses)


def _planner_payload(*, include_symbolic_fields: bool = True, voice_scripts: list[str] | None = None) -> str:
    scripts = voice_scripts or [
        "Some days feel heavier than they should",
        "A quiet light still waits inside you",
        "You can come back to yourself slowly",
    ]
    scenes = []
    for idx, script in enumerate(scripts, start=1):
        scene = {
            "scene_index": idx,
            "kind": "scene",
            "title": f"Scene {idx}",
            "voice_script": script,
            "image_prompt": "plain symbolic doodle",
            "stock_query": "symbolic doodle",
        }
        if include_symbolic_fields:
            scene.update(
                {
                    "scene_meaning": f"emotional idea {idx}",
                    "symbolic_visual": f"simple symbolic object {idx}",
                    "emotional_metaphor": f"quiet metaphor {idx}",
                    "main_character_or_object": f"symbolic object {idx}",
                    "subtitle_highlight_words": [],
                    "visual_mode": "symbolic_listicle",
                }
            )
        scenes.append(scene)
    return json.dumps(
        {
            "title": "Symbolic reel",
            "subtitle_style": "reel_minimal",
            "scenes": scenes,
        }
    )


def test_symbolic_model_validation_requires_scene_meaning_and_symbolic_visual():
    with pytest.raises(ValidationError) as exc:
        TellaScenePlan(
            title="Invalid symbolic reel",
            language="en",
            aspect_ratio="9:16",
            media_source="ai_image",
            duration_mode="short",
            theme="minimalist_symbolic_reel",
            scenes=[
                Scene(scene_index=1, kind="scene", title="One", voice_script="One"),
                Scene(scene_index=2, kind="scene", title="Two", voice_script="Two"),
                Scene(scene_index=3, kind="scene", title="Three", voice_script="Three"),
            ],
        )

    assert "scene_meaning" in str(exc.value)
    assert "symbolic_visual" in str(exc.value)


def test_symbolic_reel_enforces_scene_fields_and_prompt_style():
    plan = _symbolic_plan()

    enforce_symbolic_reel_plan(plan)

    assert plan.subtitle_style == "reel_minimal"
    for scene in plan.scenes:
        assert scene.visual_mode == "symbolic_listicle"
        assert scene.scene_meaning
        assert scene.symbolic_visual
        assert scene.emotional_metaphor
        assert scene.main_character_or_object
        assert scene.subtitle_highlight_words
        prompt = scene.image_prompt.lower()
        assert "minimalist hand-drawn emotional doodle illustration" in prompt
        assert "warm muted taupe background" in prompt
        assert "soft rough pencil lines" in prompt
        assert "flat muted earthy colors" in prompt
        assert "centered composition" in prompt
        assert "lots of negative space" in prompt
        assert "no text" in prompt
        assert "no watermark" in prompt
        assert "no realistic rendering" in prompt
        assert "no 3d" in prompt
        assert "no anime" in prompt
        assert "no complex background" in prompt
        assert scene.scene_meaning.lower() in prompt
        assert scene.symbolic_visual.lower() in prompt
        assert scene.emotional_metaphor.lower() in prompt
        assert scene.main_character_or_object.lower() in prompt


def test_symbolic_prompts_do_not_keep_room_defaults_without_explicit_setting():
    plan = _symbolic_plan()

    enforce_symbolic_reel_plan(plan)

    for scene in plan.scenes:
        prompt = scene.image_prompt.lower()
        assert "bedroom" not in prompt
        assert "bedside table" not in prompt
        assert "folded blanket" not in prompt
        assert "window" not in prompt
        assert "curtain" not in prompt
        assert re.search(r"\bbed\b", prompt) is None


def test_symbolic_prompt_contract_includes_required_metadata_fields():
    prompt = build_system_prompt(
        theme="minimalist_symbolic_reel",
        duration_mode="short",
        media_source="ai_image",
    )

    assert "minimalist_symbolic_reel short mode" in prompt
    assert "scene_meaning" in prompt
    assert "symbolic_visual" in prompt
    assert "emotional_metaphor" in prompt
    assert "main_character_or_object" in prompt
    assert "subtitle_highlight_words" in prompt
    assert 'visual_mode: "symbolic_listicle"' in prompt
    assert 'subtitle_style: "reel_minimal"' in prompt


def test_symbolic_character_lock_does_not_inject_emotional_character_defaults():
    plan = _symbolic_plan()

    apply_lock(plan, style_suffix=", symbolic suffix")

    prompt = plan.scenes[0].image_prompt.lower()
    assert "young vietnamese woman" not in prompt
    assert "character within central safe area" not in prompt
    assert "reference" not in prompt
    assert plan.scenes[0].used_reference_conditioning is False


def test_symbolic_subtitle_segments_include_highlight_words():
    plan = _symbolic_plan()
    enforce_symbolic_reel_plan(plan)
    for scene in plan.scenes:
        scene.audio_duration = 4.0

    compose_timing(plan)

    assert plan.subtitle_segments
    for segment, scene in zip(plan.subtitle_segments, plan.scenes):
        assert segment["text"] == scene.voice_script
        assert segment["highlight_words"] == scene.subtitle_highlight_words


def test_reel_minimal_supports_vietnamese_multi_word_phrase_highlights():
    caption = (
        "Co ay chon "
        "im l\u1eb7ng"
        " de "
        "bu\u00f4ng xu\u1ed1ng"
        " nhung dieu "
        "kh\u00f4ng n\u00f3i ra"
    )
    tokens = re.findall(r"\S+|\s+", caption)
    highlighted = _highlight_token_indexes(
        tokens,
        ["im l\u1eb7ng", "bu\u00f4ng xu\u1ed1ng", "kh\u00f4ng n\u00f3i ra"],
    )
    highlighted_words = {tokens[idx].strip() for idx in highlighted}

    assert {"im", "l\u1eb7ng"} <= highlighted_words
    assert {"bu\u00f4ng", "xu\u1ed1ng"} <= highlighted_words
    assert {"kh\u00f4ng", "n\u00f3i", "ra"} <= highlighted_words
    assert _phrase_word_highlight_keys(caption, ["kh\u00f4ng n\u00f3i ra"]) == {
        "khong",
        "noi",
        "ra",
    }
    assert _highlight_words("Co ay \u1edf l\u1ea1i m\u1ed9t m\u00ecnh")[0] == "m\u1ed9t m\u00ecnh"


def test_symbolic_voice_pace_default_is_minus_seven():
    pace = resolve_pace(theme="minimalist_symbolic_reel")

    assert pace.edge_rate == "-7%"


def test_symbolic_tts_uses_global_narration_and_700ms_pause(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    async def fake_synthesize(
        self,
        text,
        out_path,
        *,
        voice,
        language,
        speed,
        codec,
        sample_rate,
        metadata=None,
    ):
        captured["text"] = text
        out_path.write_bytes(b"fake mp3 bytes for symbolic tests")
        return TTSResult(
            audio_path=out_path,
            provider="edge",
            voice=voice,
            language=language,
            metadata={**(metadata or {}), "edge_rate": "-7%", "codec": "mp3"},
        )

    async def fake_postprocess(raw_path: Path, out_path: Path, *, max_pause_ms: int) -> dict:
        shutil.copyfile(raw_path, out_path)
        return {
            "silence_postprocess_applied": True,
            "max_pause_ms": max_pause_ms,
            "original_duration": 12.0,
            "processed_duration": 10.5,
            "longest_silence_before": 1.2,
            "longest_silence_after": 0.7,
        }

    async def async_value(value):
        return value

    monkeypatch.delenv("TELLA_TTS_CONTINUOUS", raising=False)
    monkeypatch.delenv("TELLA_TTS_MAX_PAUSE_MS", raising=False)
    monkeypatch.setenv("TELLA_TTS_PROVIDER", "edge")
    monkeypatch.setattr(synth_all.EdgeTTSProvider, "synthesize", fake_synthesize)
    monkeypatch.setattr(synth_all, "_ffprobe_duration", lambda path: async_value(10.5))
    monkeypatch.setattr(synth_all, "_postprocess_narration_audio", fake_postprocess)

    plan = _symbolic_plan()
    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    assert plan.tts_continuous is True
    assert plan.tts_text_source == "global_narration_text"
    assert plan.tts_max_pause_ms == 700
    assert plan.tts_metadata["max_pause_ms"] == 700
    assert plan.tts_metadata["tts_text_source"] == "global_narration_text"
    assert captured["text"] == plan.global_narration_text
    assert "\n" not in captured["text"]
    assert plan.global_narration_text


def test_symbolic_topic_planner_retries_when_validation_fields_missing():
    client = _FakeClient(
        [
            _planner_payload(include_symbolic_fields=False),
            _planner_payload(include_symbolic_fields=True),
        ]
    )

    plan = asyncio.run(
        plan_story(
            topic="a soft emotional symbolic reel",
            target_lang="en",
            aspect_ratio="9:16",
            media_source="ai_image",
            duration_mode="short",
            theme="minimalist_symbolic_reel",
            client=client,
            model="fake-model",
        )
    )

    assert len(client.models.calls) == 2
    assert plan.subtitle_style == "reel_minimal"
    for scene in plan.scenes:
        assert scene.scene_meaning
        assert scene.symbolic_visual
        assert scene.visual_mode == "symbolic_listicle"
        assert scene.prompt_used == ""
        assert "scene meaning:" in scene.image_prompt
        assert "symbolic visual:" in scene.image_prompt


def test_symbolic_script_parse_path_preserves_fields_and_phrase_highlights():
    lines = [
        "M\u1ed9t m\u00ecnh kh\u00f4ng c\u00f2n l\u00e0 thua cu\u1ed9c.",
        "Im l\u1eb7ng c\u0169ng c\u00f3 th\u1ec3 l\u00e0 m\u1ed9t c\u00e1ch th\u01b0\u01a1ng m\u00ecnh.",
        "R\u1ed3i c\u00f4 \u1ea5y h\u1ecdc c\u00e1ch bu\u00f4ng xu\u1ed1ng.",
    ]
    client = _FakeClient([_planner_payload(include_symbolic_fields=True, voice_scripts=lines)])

    plan = asyncio.run(
        plan_story_from_script(
            user_script="\n".join(lines),
            target_lang="vi",
            aspect_ratio="9:16",
            media_source="ai_image",
            duration_mode="short",
            theme="minimalist_symbolic_reel",
            client=client,
            model="fake-model",
        )
    )

    assert [scene.voice_script for scene in plan.scenes] == lines
    assert plan.subtitle_style == "reel_minimal"
    assert plan.scenes[0].subtitle_highlight_words[0] == "m\u1ed9t m\u00ecnh"
    assert plan.scenes[1].subtitle_highlight_words[0] == "im l\u1eb7ng"
    assert plan.scenes[2].subtitle_highlight_words[0] == "bu\u00f4ng xu\u1ed1ng"
    for scene in plan.scenes:
        assert scene.scene_meaning
        assert scene.symbolic_visual
        assert scene.visual_mode == "symbolic_listicle"


def test_symbolic_dry_run_plan_writes_inspectable_metadata(monkeypatch, tmp_path):
    async def fake_translate_topic(topic, target_lang):
        return SimpleNamespace(
            translated_topic=topic,
            source_language_detected=target_lang,
            target_language=target_lang,
            needs_translation=False,
        )

    async def fake_plan_story(**kwargs):
        plan = _symbolic_plan()
        enforce_symbolic_reel_plan(plan)
        return plan

    async def fail_fetch_assets(*args, **kwargs):
        raise AssertionError("dry-run should not fetch assets")

    async def fail_tts(*args, **kwargs):
        raise AssertionError("dry-run should not synthesize TTS")

    async def fail_render(*args, **kwargs):
        raise AssertionError("dry-run should not render")

    monkeypatch.setattr(cli_module, "translate_topic", fake_translate_topic)
    monkeypatch.setattr(cli_module, "plan_story", fake_plan_story)
    monkeypatch.setattr(cli_module, "fetch_assets", fail_fetch_assets)
    monkeypatch.setattr(cli_module, "synthesize_all", fail_tts)
    monkeypatch.setattr(cli_module, "render", fail_render)
    monkeypatch.delenv("TELLA_TTS_CONTINUOUS", raising=False)
    monkeypatch.delenv("TELLA_TTS_MAX_PAUSE_MS", raising=False)
    monkeypatch.delenv("TELLA_TTS_STYLE", raising=False)

    plan_path = asyncio.run(
        cli_module.run_pipeline(
            topic="soft symbolic reel",
            target_lang="en",
            theme="minimalist_symbolic_reel",
            media_source="ai_image",
            duration_mode="short",
            aspect_ratio="9:16",
            voice_pace_name=None,
            voice_rate_custom=None,
            voice_gender="female",
            out_root=tmp_path,
            job_id="dry_symbolic",
            dry_run_plan=True,
        )
    )

    data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    assert data["theme"] == "minimalist_symbolic_reel"
    assert data["subtitle_style"] == "reel_minimal"
    assert data["tts_continuous"] is True
    assert data["tts_text_source"] == "global_narration_text"
    assert data["tts_max_pause_ms"] == 700
    assert data["tts_metadata"]["tts_continuous"] is True
    assert data["global_narration_text"]
    for scene in data["scenes"]:
        assert scene["visual_mode"] == "symbolic_listicle"
        assert scene["scene_meaning"]
        assert scene["symbolic_visual"]
        assert scene["emotional_metaphor"]
        assert scene["main_character_or_object"]
        assert scene["subtitle_highlight_words"]
        assert "scene meaning:" in scene["image_prompt"]
