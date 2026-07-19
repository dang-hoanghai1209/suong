from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from tella.object_library.models import LicenseMetadata, SourceCandidate
from tella.object_library.style_evaluation import (
    FIXED_BACKGROUND,
    adapt_svg,
    candidate_variant,
    family_candidates,
    rank_family_candidates,
    render_character_context,
    select_family_candidate,
)


def _candidate(source_id: str, family: str, label: str = "phone") -> SourceCandidate:
    return SourceCandidate(
        source="iconify",
        source_object_id=source_id,
        canonical_label=label,
        style_family=family,
        license=LicenseMetadata(name="MIT"),
    )


def test_family_filter_is_exact_and_preserves_metadata():
    candidates = [
        _candidate("mdi:phone-outline", "mdi"),
        _candidate("material-symbols:phone", "material-symbols"),
    ]
    selected = family_candidates(candidates, "mdi")
    assert [item.source_object_id for item in selected] == ["mdi:phone-outline"]
    assert selected[0].style_family == "mdi"


def test_variant_detection_and_family_selection_are_deterministic():
    queried = [
        ("phone", _candidate("mdi:phone", "mdi")),
        ("phone", _candidate("mdi:phone-outline", "mdi", "phone outline")),
    ]
    first = select_family_candidate("phone", "mdi", queried)
    second = select_family_candidate("phone", "mdi", list(reversed(queried)))
    assert first == second
    assert first is not None
    assert first.candidate.source_object_id == "mdi:phone-outline"
    assert candidate_variant(first.candidate) == "outline"


def test_missing_family_candidate_is_honest():
    queried = [("phone", _candidate("mdi:phone", "mdi"))]
    assert select_family_candidate("phone", "boxicons", queried) is None
    assert rank_family_candidates("phone", "boxicons", queried) == []


def test_tella_adaptation_preserves_geometry_and_changes_only_safe_style():
    source = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="#F0E6D8" d="M1 1L23 23"/></svg>'
    adapted = adapt_svg(source)
    assert b"M1 1L23 23" in adapted
    assert b'stroke-linecap="round"' in adapted
    assert b'stroke-linejoin="round"' in adapted
    assert b"#EAD9C8" in adapted


def test_fixed_background_and_context_render_are_deterministic(tmp_path):
    character = tmp_path / "character.png"
    prop = tmp_path / "prop.png"
    Image.new("RGBA", (100, 200), (220, 150, 130, 255)).save(character)
    Image.new("RGBA", (80, 80), (240, 230, 216, 255)).save(prop)
    first = render_character_context(character, [prop], tmp_path / "first.png")
    second = render_character_context(character, [prop], tmp_path / "second.png")
    assert (
        hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    )
    with Image.open(first) as image:
        assert image.getpixel((0, 0)) == (51, 40, 33)
        assert FIXED_BACKGROUND == "#332821"


def test_evaluation_paths_do_not_overlap_production_paths(tmp_path):
    production = tmp_path / "records" / "object.json"
    evaluation = tmp_path / "style_family_evaluation" / "objects" / "mdi" / "phone.png"
    production.parent.mkdir(parents=True)
    production.write_text("baseline", encoding="utf-8")
    evaluation.parent.mkdir(parents=True)
    evaluation.write_bytes(b"evaluation")
    assert production.read_text(encoding="utf-8") == "baseline"
    assert not evaluation.is_relative_to(production.parent)


def test_style_policy_config_is_evaluation_only():
    path = Path("configs/object_library/style_family_policy.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "evaluation_only"
    assert payload["production_expansion_allowed"] is False
    assert payload["decision"] == "NEEDS_STYLE_ADAPTATION"


def test_missing_noun_credentials_do_not_affect_iconify_family_selection(monkeypatch):
    monkeypatch.delenv("NOUN_PROJECT_KEY", raising=False)
    monkeypatch.delenv("NOUN_PROJECT_SECRET", raising=False)
    queried = [("phone", _candidate("mdi:phone-outline", "mdi", "phone outline"))]
    assert select_family_candidate("phone", "mdi", queried) is not None
