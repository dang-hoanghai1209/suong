from tella.planner.models import Scene, TellaScenePlan
from tella.tts.synth_all import _resolve_tts_settings


def _plan() -> TellaScenePlan:
    return TellaScenePlan(
        title="Voice smoke",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        voice_name="vi-VN-HoaiMyNeural",
        voice_edge_rate="-10%",
        scenes=[
            Scene(scene_index=1, kind="scene", title="One", voice_script="Một."),
            Scene(scene_index=2, kind="scene", title="Two", voice_script="Hai."),
            Scene(scene_index=3, kind="scene", title="Three", voice_script="Ba."),
        ],
    )


def test_tts_env_voice_overrides_edge_voice(monkeypatch):
    monkeypatch.setenv("TELLA_TTS_VOICE", "vi-VN-HoaiMyNeural")
    settings = _resolve_tts_settings(_plan(), "edge")

    assert settings["provider"] == "edge"
    assert settings["voice"] == "vi-VN-HoaiMyNeural"
    assert settings["language"] == "vi"


def test_google_tts_uses_google_voice_env_when_tella_voice_missing(monkeypatch):
    monkeypatch.delenv("TELLA_TTS_VOICE", raising=False)
    monkeypatch.setenv("GOOGLE_TTS_VOICE", "vi-VN-Chirp3-HD-Achernar")
    settings = _resolve_tts_settings(_plan(), "google")

    assert settings["provider"] == "google"
    assert settings["voice"] == "vi-VN-Chirp3-HD-Achernar"
    assert settings["language"] == "vi"
