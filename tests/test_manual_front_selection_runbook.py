from pathlib import Path


def test_manual_front_selection_runbook_has_safe_commands_and_boundaries():
    text = Path("docs/runbooks/manual_front_anchor_selection.md").read_text(encoding="utf-8")
    assert "interactive-review" in text
    assert "verify-selection" in text
    assert "Do not paste raw JSON" in text
    assert "BFL, R2, credentials" in text
    assert "three-quarter, side-profile, and full-body" in text
    assert "candidate_02" not in text
