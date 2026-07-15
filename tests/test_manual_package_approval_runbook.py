from pathlib import Path


def test_package_approval_runbook_has_exact_safe_commands():
    text = Path("docs/runbooks/manual_four_view_package_approval.md").read_text(encoding="utf-8")
    assert "interactive-review" in text
    assert "verify-approval" in text
    assert "no approve-all shortcut" in text
    assert "BFL, R2, API key" in text
    assert "package_approval.json" in text
