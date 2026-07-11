from pathlib import Path

import pytest
from PIL import Image, ImageStat

from tella.render.pipeline import (
    _apply_image_grade,
    _prepare_image_asset_for_render,
)
from tella.themes.loader import ImageGrade, load_theme


def _mean_luminance(path: Path) -> float:
    with Image.open(path) as image:
        return float(ImageStat.Stat(image.convert("L")).mean[0])


def _symbolic_grade() -> ImageGrade:
    return load_theme("minimalist_symbolic_reel").image_grade


def test_symbolic_theme_loads_effective_image_grade():
    grade = _symbolic_grade()

    assert grade.enabled is True
    assert grade.brightness == pytest.approx(0.90)
    assert grade.contrast == pytest.approx(1.03)
    assert grade.saturation == pytest.approx(0.88)
    assert grade.overlay_color == "#504845"
    assert grade.overlay_opacity == pytest.approx(0.18)


def test_symbolic_grade_darkens_beige_but_preserves_readable_warm_midtones(tmp_path):
    source = tmp_path / "beige.png"
    graded = tmp_path / "graded.png"
    Image.new("RGB", (120, 180), "#ead8c0").save(source)

    _apply_image_grade(
        source,
        graded,
        canvas_w=120,
        canvas_h=180,
        grade=_symbolic_grade(),
    )

    source_luminance = _mean_luminance(source)
    graded_luminance = _mean_luminance(graded)
    with Image.open(graded) as image:
        red, green, blue = image.getpixel((60, 90))

    assert graded_luminance < source_luminance - 20
    assert graded_luminance > 100
    assert red > green > blue
    assert red - blue < 55


def test_grading_is_deterministic_and_never_mutates_or_progressively_darkens_source(
    tmp_path,
):
    source = tmp_path / "source.png"
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (80, 120), "#e8d4ba").save(source)
    source_before = source.read_bytes()

    first_hash = _apply_image_grade(
        source,
        first,
        canvas_w=80,
        canvas_h=120,
        grade=_symbolic_grade(),
    )
    second_hash = _apply_image_grade(
        source,
        second,
        canvas_w=80,
        canvas_h=120,
        grade=_symbolic_grade(),
    )

    assert source.read_bytes() == source_before
    assert first_hash == second_hash
    assert first.read_bytes() == second.read_bytes()
    assert _mean_luminance(first) == pytest.approx(_mean_luminance(second))


def test_subtitle_overlay_pixels_are_composited_after_and_not_tinted_by_grade(tmp_path):
    source = tmp_path / "source.png"
    graded = tmp_path / "graded.png"
    Image.new("RGB", (100, 160), "#ead8c0").save(source)
    _apply_image_grade(
        source,
        graded,
        canvas_w=100,
        canvas_h=160,
        grade=_symbolic_grade(),
    )

    with Image.open(graded) as background:
        composited = background.convert("RGBA")
    subtitle_overlay = Image.new("RGBA", composited.size, (0, 0, 0, 0))
    for x in range(30, 70):
        for y in range(120, 130):
            subtitle_overlay.putpixel((x, y), (255, 247, 237, 255))
    composited.alpha_composite(subtitle_overlay)

    assert composited.getpixel((50, 125)) == (255, 247, 237, 255)
    assert composited.getpixel((50, 80))[:3] != (255, 247, 237)


def test_theme_without_image_grade_returns_original_asset_unchanged(tmp_path):
    source = tmp_path / "source.png"
    disabled_output = tmp_path / "disabled.png"
    Image.new("RGB", (40, 60), "#ead8c0").save(source)
    source_before = source.read_bytes()
    grade = load_theme("parable").image_grade

    selected, source_hash, applied = _prepare_image_asset_for_render(
        source,
        disabled_output,
        canvas_w=40,
        canvas_h=60,
        grade=grade,
    )

    assert grade.enabled is False
    assert selected == source
    assert source_hash == ""
    assert applied is False
    assert source.read_bytes() == source_before
    assert not disabled_output.exists()
