from pathlib import Path


def test_manual_runbook_documents_zero_cost_and_human_boundary():
    text = Path("docs/runbooks/manual_character_bootstrap.md").read_text(encoding="utf-8")
    assert "optional premium integrations" in text
    assert "--mode validate-only" in text
    assert "--mode import" in text
    assert "--candidate-01" in text and "--candidate-02" in text and "--candidate-03" in text
    assert "pending_human_review" in text
    assert "Stage B remains" in text
    assert "No provider call" in text
    assert "candidate_02" not in text or "approved candidate_02" not in text
