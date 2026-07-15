from pathlib import Path


def test_four_view_runbook_is_local_and_has_all_commands():
    text = Path("docs/runbooks/manual_four_view_package.md").read_text(encoding="utf-8")
    assert "validate-only" in text
    assert "import-views" in text
    assert "verify-draft" in text
    assert "BFL, R2" in text
    assert "three-quarter portrait" in text
    assert "final package approval is false" in text
