from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image
from pydantic import SecretStr

from tella.media.bfl_front_anchor_orchestration import (
    CHARACTER_FINGERPRINT,
    LiveFrontPlan,
    ProviderBundle,
    RepositoryState,
    SEEDS,
    execute_live_front,
)
from tella.media.bfl_front_anchor_provider import BFLFrontAnchorError


def _prompt() -> str:
    return "Create exactly one front anchor."


def _plan(session: str = "orchestration_test_01") -> LiveFrontPlan:
    import hashlib
    return LiveFrontPlan(
        session_id=session, character_id="practical_young_adult_male_teal_v1",
        character_fingerprint=CHARACTER_FINGERPRINT, canonical_spec_version=1,
        generation_spec_version=1, prompt=_prompt(),
        prompt_sha256=hashlib.sha256(_prompt().encode()).hexdigest(),
        asset_role="front_portrait", width=768, height=1024, output_format="png",
        prompt_upsampling=False, seeds=SEEDS, maximum_submissions=3,
        targeted_submissions=0, retries=0, fallbacks=0,
        output_root=Path("out") / "character_reference_bootstrap" / session,
    )


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (768, 1024), "white").save(output, format="PNG")
    return output.getvalue()


class FakeProvider:
    def __init__(self, fail_at: int | None = None):
        self.calls = []
        self.accounting = {}
        self.fail_at = fail_at

    async def generate(self, request, out_path):
        self.calls.append(request.seed)
        if self.fail_at == request.seed:
            self.accounting.update({"application_image_submissions": len(self.calls), "bfl_create_attempts": len(self.calls)})
            raise BFLFrontAnchorError("download_failure", "safe")
        self.accounting.update({
            "application_image_submissions": len(self.calls),
            "bfl_create_attempts": len(self.calls),
            "bfl_poll_attempts": 1,
            "bfl_result_download_attempts": len(self.calls),
        })
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_png())
        return type("Result", (), {"metadata": {"request_id": f"req-{request.seed}"}})()


def _state(_root):
    return RepositoryState(
        branch="feature/reference-conditioned-image-provider",
        tracked_clean=True, staged_zero=True,
    )


def test_wrong_authorization_reads_no_credential_and_creates_no_artifacts(tmp_path):
    (tmp_path / ".git").mkdir()
    reads = []
    provider_calls = []
    result = asyncio.run(execute_live_front(
        plan=_plan(), repository_root=tmp_path, authorization_token="wrong",
        credential_reader=lambda: reads.append(1) or SecretStr("never"),
        provider_factory=lambda key: provider_calls.append(key), state_reader=_state,
    ))
    assert result.exit_code == 2
    assert reads == [] and provider_calls == []
    assert list(tmp_path.rglob("out")) == []


def test_three_candidates_are_sequential_and_manifest_is_atomic(tmp_path):
    (tmp_path / ".git").mkdir()
    fake = FakeProvider()
    result = asyncio.run(execute_live_front(
        plan=_plan(), repository_root=tmp_path, authorization_token="AUTHORIZE_BFL_FRONT_ANCHOR_CANARY_01",
        credential_reader=lambda: SecretStr("fake"),
        provider_factory=lambda key: ProviderBundle(provider=fake, close=lambda: None), state_reader=_state,
    ))
    assert result.exit_code == 0
    assert result.status == "completed_candidates"
    assert fake.calls == [17001, 17002, 17003]
    manifest = result.manifest_path.read_text(encoding="utf-8")
    assert "fake" not in manifest and "://" not in manifest
    assert [
        (tmp_path / "out" / "character_reference_bootstrap" / _plan().session_id / f"candidate_{i:02d}.png").exists()
        for i in range(1, 4)
    ] == [True, True, True]


def test_failure_preserves_prior_candidate_and_stops_later(tmp_path):
    (tmp_path / ".git").mkdir()
    fake = FakeProvider(fail_at=17002)
    result = asyncio.run(execute_live_front(
        plan=_plan("orchestration_partial_01"), repository_root=tmp_path,
        authorization_token="AUTHORIZE_BFL_FRONT_ANCHOR_CANARY_01",
        credential_reader=lambda: SecretStr("fake"),
        provider_factory=lambda key: ProviderBundle(provider=fake, close=lambda: None), state_reader=_state,
    ))
    assert result.exit_code == 3 and result.status == "partial_failed"
    assert fake.calls == [17001, 17002]
    root = tmp_path / "out" / "character_reference_bootstrap" / "orchestration_partial_01"
    assert (root / "candidate_01.png").exists()
    assert not (root / "candidate_03.png").exists()
    payload = __import__("json").loads((root / "candidates_manifest.json").read_text())
    assert payload["candidates"][1]["status"] == "failed"
    assert payload["candidates"][2]["status"] == "not_attempted_due_to_fail_closed"
    assert payload["review_artifacts_created"] is False
