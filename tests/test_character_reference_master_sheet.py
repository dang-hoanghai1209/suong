from __future__ import annotations

import socket
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from tella.media.character_reference_package import (
    ATOMIC_DIMENSIONS,
    ATOMIC_VIEW_ORDER,
    MASTER_SHEET_DIMENSIONS,
    build_master_sheet,
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("master-sheet tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
    colors = ("#5b7f76", "#df8668", "#26332f", "#eef0e7")
    sources = []
    for index, (role, color) in enumerate(zip(ATOMIC_VIEW_ORDER, colors, strict=True)):
        path = tmp_path / f"{role}.png"
        image = Image.new("RGB", ATOMIC_DIMENSIONS, color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((20 + index, 30, 120, 140 + index), fill="#ffffff")
        image.save(path, format="PNG")
        sources.append((role, path))
    return tuple(sources)


def test_lossless_exact_grid_geometry_order_and_determinism(tmp_path):
    sources = _sources(tmp_path)
    first_path = tmp_path / "master_first.png"
    second_path = tmp_path / "master_second.png"
    first = build_master_sheet(sources, first_path)
    second = build_master_sheet(sources, second_path)

    assert (first.width, first.height) == MASTER_SHEET_DIMENSIONS == (1536, 2048)
    assert first.sha256 == second.sha256
    assert first_path.read_bytes() == second_path.read_bytes()

    positions = ((0, 0), (768, 0), (0, 1024), (768, 1024))
    with Image.open(first_path) as master:
        master.load()
        assert master.size == MASTER_SHEET_DIMENSIONS
        assert master.format == "PNG"
        assert not master.info
        for (_, source_path), (left, top) in zip(sources, positions, strict=True):
            with Image.open(source_path) as source:
                source.load()
                cell = master.crop((left, top, left + 768, top + 1024))
                assert ImageChops.difference(
                    cell.convert("RGBA"), source.convert("RGBA")
                ).getbbox() is None


@pytest.mark.parametrize("case", ["missing", "duplicate", "wrong_order"])
def test_missing_duplicate_or_wrong_order_fails_closed(tmp_path, case):
    sources = list(_sources(tmp_path))
    if case == "missing":
        sources.pop()
    elif case == "duplicate":
        sources[1] = (sources[0][0], sources[1][1])
    else:
        sources[0], sources[1] = sources[1], sources[0]
    with pytest.raises(ValueError, match="missing, duplicated, or out of order"):
        build_master_sheet(tuple(sources), tmp_path / "master.png")


@pytest.mark.parametrize("case", ["dimensions", "mime"])
def test_incorrect_atomic_dimensions_or_mime_fails_closed(tmp_path, case):
    sources = list(_sources(tmp_path))
    role, path = sources[0]
    if case == "dimensions":
        Image.new("RGB", (767, 1024), "#5b7f76").save(path, format="PNG")
        expected = "dimensions mismatch"
    else:
        Image.new("RGB", ATOMIC_DIMENSIONS, "#5b7f76").save(path, format="JPEG")
        expected = "MIME mismatch"
    sources[0] = (role, path)
    with pytest.raises(ValueError, match=expected):
        build_master_sheet(tuple(sources), tmp_path / "master.png")


def test_missing_atomic_file_fails_without_output(tmp_path):
    sources = list(_sources(tmp_path))
    sources[2][1].unlink()
    output = tmp_path / "master.png"
    with pytest.raises(ValueError, match="existing PNG"):
        build_master_sheet(tuple(sources), output)
    assert not output.exists()


def test_atomic_change_changes_master_hash(tmp_path):
    sources = _sources(tmp_path)
    first = build_master_sheet(sources, tmp_path / "master_first.png")
    role, changed_path = sources[3]
    with Image.open(changed_path) as image:
        changed = image.convert("RGB")
    changed.putpixel((0, 0), (1, 2, 3))
    changed.save(changed_path, format="PNG")
    changed_sources = (*sources[:3], (role, changed_path))
    second = build_master_sheet(changed_sources, tmp_path / "master_second.png")
    assert second.sha256 != first.sha256
    assert second.source_sha256[3] != first.source_sha256[3]
