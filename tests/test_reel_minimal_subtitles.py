import re

import pytest

from tella.composer.compose import compose_timing
from tella.planner.models import Scene, TellaScenePlan
from tella.render.text_overlay import (
    _REEL_MINIMAL_CAPTION_CENTER_Y_RATIO,
    _REEL_MINIMAL_CAPTION_SAFE_BOTTOM_RATIO,
    _REEL_MINIMAL_CAPTION_SAFE_TOP_RATIO,
    _highlight_token_indexes,
    _reel_minimal_caption_top_y,
)
from tella.subtitles import sanitize_subtitle_text, subtitle_text_for_style


def _caption_bounds(block_h: int) -> tuple[int, int]:
    top = _reel_minimal_caption_top_y(
        canvas_h=1920,
        block_h=block_h,
        safe_top=285,
        safe_bottom=1635,
    )
    return top, top + block_h


def test_reel_minimal_preferred_center_is_seventy_nine_percent():
    top, bottom = _caption_bounds(80)

    assert _REEL_MINIMAL_CAPTION_CENTER_Y_RATIO == pytest.approx(0.79)
    assert (top + bottom) / 2 == pytest.approx(1517, abs=1)


def test_two_line_subtitle_block_stays_inside_vertical_safe_region():
    top, bottom = _caption_bounds(104)

    assert top >= int(1920 * _REEL_MINIMAL_CAPTION_SAFE_TOP_RATIO)
    assert bottom <= int(1920 * _REEL_MINIMAL_CAPTION_SAFE_BOTTOM_RATIO)
    assert (top + bottom) / 2 == pytest.approx(1517, abs=1)


def test_one_line_subtitle_remains_centered_in_lower_safe_region():
    top, bottom = _caption_bounds(44)

    assert top >= int(1920 * 0.72)
    assert bottom <= int(1920 * 0.84)
    assert (top + bottom) / 2 == pytest.approx(1517, abs=1)


def test_bom_and_zero_width_prefix_are_removed():
    result = sanitize_subtitle_text("\uFEFF\u200BCó những nỗi buồn...")

    assert result.text == "Có những nỗi buồn..."
    assert result.removed_codepoints == ("U+FEFF", "U+200B")


@pytest.mark.parametrize("codepoint", ("\u200B", "\u200C", "\u200D", "\u2060"))
def test_zero_width_format_characters_are_removed(codepoint):
    result = sanitize_subtitle_text(f"{codepoint}Có những nỗi buồn...")

    assert result.text == "Có những nỗi buồn..."


def test_replacement_character_and_leading_empty_square_are_removed():
    result = sanitize_subtitle_text("\uFFFD\u25A1 Có những nỗi buồn...")

    assert result.text == "Có những nỗi buồn..."
    assert result.removed_codepoints == ("U+FFFD", "U+25A1")


def test_control_characters_are_removed_but_tab_and_newline_remain():
    result = sanitize_subtitle_text("Có\x00\x85\tnhững\nnỗi buồn")

    assert result.text == "Có\tnhững\nnỗi buồn"
    assert result.removed_codepoints == ("U+0000", "U+0085")


def test_vietnamese_diacritics_and_punctuation_remain_nfc():
    text = "Có những nỗi buồn… mình không nói ra, đúng không? “Ừ.”"
    decomposed = text.replace("Có", "Co\u0301")

    assert sanitize_subtitle_text(decomposed).text == text


def test_highlight_alignment_survives_leading_bom_removal():
    caption = sanitize_subtitle_text("\uFEFFCó những nỗi buồn...").text
    tokens = re.findall(r"\S+|\s+", caption)
    indexes = _highlight_token_indexes(tokens, ["Có những"])

    assert {tokens[index].strip() for index in indexes} == {"Có", "những"}
    assert "" not in {tokens[index].strip() for index in indexes}


def test_reel_minimal_subtitle_metadata_uses_sanitized_text():
    scenes = [
        Scene(
            scene_index=index,
            title=f"Scene {index}",
            voice_script=(
                "\uFEFF\u200BCó những nỗi buồn..."
                if index == 1
                else f"Caption {index}"
            ),
            subtitle_highlight_words=["\uFEFFCó những"] if index == 1 else [],
            audio_duration=2.0,
        )
        for index in range(1, 4)
    ]
    plan = TellaScenePlan(
        title="Subtitle sanitation",
        language="vi",
        aspect_ratio="9:16",
        media_source="stock_photo",
        duration_mode="short",
        theme="parable",
        scenes=scenes,
        subtitle_style="reel_minimal",
    )

    compose_timing(plan)

    assert plan.subtitle_segments[0]["text"] == "Có những nỗi buồn..."
    assert plan.subtitle_segments[0]["highlight_words"] == ["Có những"]
    assert plan.scenes[0].voice_script.startswith("\uFEFF")


def test_other_caption_styles_leave_text_unchanged():
    text = "\uFEFF\u200BCó những nỗi buồn..."

    result = subtitle_text_for_style(text, "boxed")

    assert result.text == text
    assert result.removed_codepoints == ()
