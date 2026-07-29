from __future__ import annotations

from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from tella.media.image_provider_contract import ImageProviderCapabilities
from tella.topic_production.local_host import create_plan_only_http_server
from tella.topic_production.plan_only_application import PlanOnlyApplication


class DeterministicVisualProvider:
    provider_name = "deterministic-test-provider"

    def __init__(
        self,
        *,
        fail_indices: set[int] | None = None,
        duplicate_bytes: bool = False,
        invalid_mime: bool = False,
        zero_bytes: bool = False,
        invalid_dimensions: bool = False,
        mime_extension_mismatch: bool = False,
        timeout: bool = False,
        configured: bool = True,
    ) -> None:
        self.fail_indices = fail_indices or set()
        self.duplicate_bytes = duplicate_bytes
        self.invalid_mime = invalid_mime
        self.zero_bytes = zero_bytes
        self.invalid_dimensions = invalid_dimensions
        self.mime_extension_mismatch = mime_extension_mismatch
        self.timeout = timeout
        self.configured = configured
        self.calls: list[dict[str, object]] = []

    def capabilities(self) -> ImageProviderCapabilities:
        return ImageProviderCapabilities(
            provider_id=self.provider_name,
            supports_text_to_image=True,
            supports_reference_conditioning=False,
            supports_image_to_image=False,
            supports_structural_conditioning=False,
            supports_seed=True,
            supports_negative_prompt=True,
            max_prompt_utf8_bytes=12_000,
            max_reference_images=0,
            accepted_reference_mime_types=(),
            supports_character_identity_anchor=False,
            provider_retry_control="caller_bounded",
        )

    def is_configured(self) -> bool:
        return self.configured

    async def generate_text_image(
        self,
        prompt: str,
        negative_prompt: str,
        aspect: str,
        seed: int | None,
        out_path: Path,
        metadata: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        index = len(self.calls) + 1
        self.calls.append(
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "aspect": aspect,
                "seed": seed,
                "out_path": out_path,
                "metadata": deepcopy(metadata),
            }
        )
        if self.timeout:
            raise TimeoutError
        if index in self.fail_indices:
            raise RuntimeError("provider-secret-marker")
        if self.zero_bytes:
            out_path.write_bytes(b"")
        elif self.invalid_mime:
            out_path.write_text("<html>not an image</html>", encoding="utf-8")
        else:
            color = (
                (40, 80, 120)
                if self.duplicate_bytes
                else (
                    index * 30 % 255,
                    index * 60 % 255,
                    index * 90 % 255,
                )
            )
            size = (40, 40) if self.invalid_dimensions else (576, 1024)
            image_format = "JPEG" if self.mime_extension_mismatch else "PNG"
            Image.new("RGB", size, color).save(out_path, format=image_format)
        return SimpleNamespace(output_path=out_path)


def _request_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "input_mode": "TOPIC",
        "source_content": "A calm story about choosing a nourishing daily ritual",
        "language": "en",
        "character_scope": "recurring_female",
        "requested_scene_count": 8,
    }


def _accepted_scene_application(
    tmp_path: Path,
    provider: DeterministicVisualProvider,
) -> tuple[PlanOnlyApplication, str, dict[str, object], dict[str, object]]:
    application = PlanOnlyApplication(
        visual_provider=provider,
        visual_artifact_root=tmp_path / "visual-artifacts",
    )
    created = application.create_run(_request_payload())
    run_id = str(created["run"]["run_id"])
    application.accept_story_plan(
        run_id,
        {"schema_version": 1, "current_revision_id": "revision-0001"},
    )
    initialized = application.initialize_scene_plan(
        run_id,
        {"schema_version": 1, "accepted_story_revision_id": "revision-0001"},
    )
    collection = initialized["access"]["collection"]
    scene = collection["scenes"][0]
    accepted = application.mutate_scene_plan(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": collection["collection_revision_id"],
            "scene_id": scene["scene_id"],
            "base_scene_revision_id": scene["scene_revision_id"],
        },
        scene_id=scene["scene_id"],
    )
    accepted_collection = accepted["access"]["collection"]
    accepted_scene = accepted_collection["scenes"][0]
    return application, run_id, accepted_collection, accepted_scene


def _generate_payload(
    access: dict[str, object],
    *,
    count: object = 2,
) -> dict[str, object]:
    collection = access["collection"]
    return {
        "schema_version": 1,
        "visual_collection_revision_id": collection["visual_collection_revision_id"],
        "scene_plan_collection_revision_id": collection["scene_plan_collection_revision_id"],
        "scene_revision_id": collection["scene_revision_id"],
        "candidate_count": count,
        "aspect_ratio": "9:16",
        "composition_emphasis": "Keep the recurring woman as the clear focal subject.",
        "correction_dimensions": [],
        "note": None,
    }


def _mutation_payload(
    collection: dict[str, object],
    candidate: dict[str, object],
    *,
    reason: str = "COMPOSITION_MISMATCH",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "visual_collection_revision_id": collection["visual_collection_revision_id"],
        "scene_plan_collection_revision_id": collection["scene_plan_collection_revision_id"],
        "scene_revision_id": collection["scene_revision_id"],
        "candidate_revision_id": candidate["candidate_revision_id"],
        "reason_code": reason,
        "note": "Bounded visual review note.",
    }


def test_access_requires_current_accepted_scene_and_configured_provider(
    tmp_path: Path,
) -> None:
    unavailable = DeterministicVisualProvider(configured=False)
    application = PlanOnlyApplication(
        visual_provider=unavailable,
        visual_artifact_root=tmp_path,
    )
    created = application.create_run(_request_payload())
    run_id = str(created["run"]["run_id"])

    locked = application.get_visual_candidate_access(run_id, "scene_01")
    assert locked["request_authorized"] is False
    assert "STORYPLAN_NOT_ACCEPTED" in locked["blocker_codes"]
    assert "VISUAL_PROVIDER_UNAVAILABLE" in locked["blocker_codes"]

    available = DeterministicVisualProvider()
    application, run_id, _, _ = _accepted_scene_application(tmp_path, available)
    access = application.get_visual_candidate_access(run_id, "scene_01")
    assert access["request_authorized"] is True
    assert access["review_authorized"] is True
    assert access["render_authority"] is False
    assert access["video_render_authority"] is False
    assert access["final_media_capability"] is False
    assert access["tts_capability"] is False

    lazy_root = tmp_path / "lazy-artifacts"
    lazy_application = PlanOnlyApplication(
        visual_provider=DeterministicVisualProvider(),
        visual_artifact_root=lazy_root,
    )
    assert not lazy_root.exists()
    lazy_application.create_run(_request_payload())
    assert not lazy_root.exists()


def test_generation_is_bounded_deterministic_detached_and_artifact_validated(
    tmp_path: Path,
) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, scene_collection, scene = _accepted_scene_application(tmp_path, provider)
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    before = deepcopy(access)
    result = application.generate_visual_candidates(
        run_id,
        scene["scene_id"],
        _generate_payload(access),
    )

    assert result["error"] is None
    collection = result["access"]["collection"]
    assert access == before
    assert len(provider.calls) == 2
    assert len(collection["candidates"]) == 2
    assert len(collection["requests"]) == 1
    assert len(collection["attempts"]) == 1
    assert collection["attempts"][0]["status"] == "SUCCESS"
    for candidate in collection["candidates"]:
        assert candidate["mime_type"] == "image/png"
        assert (candidate["width"], candidate["height"]) == (576, 1024)
        assert len(candidate["sha256"]) == 64
        assert len(candidate["logical_request_hash"]) == 64
        assert len(candidate["provider_request_hash"]) == 64
        assert candidate["technical_validation"]["passed"] is True
        artifact = application.get_visual_candidate_artifact(
            run_id, scene["scene_id"], candidate["candidate_id"]
        )
        assert artifact is not None
        assert hashlib.sha256(artifact.content).hexdigest() == candidate["sha256"]
    assert collection["candidates"][0]["sha256"] != collection["candidates"][1]["sha256"]
    assert (
        collection["scene_plan_collection_revision_id"]
        == scene_collection["collection_revision_id"]
    )

    result["access"]["collection"]["candidates"][0]["status"] = "forged"
    restored = application.get_visual_candidate_access(run_id, scene["scene_id"])
    assert restored["collection"]["candidates"][0]["status"] == "REVIEW_PENDING"


def test_candidate_count_and_arbitrary_authority_fields_fail_closed(
    tmp_path: Path,
) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, _, scene = _accepted_scene_application(tmp_path, provider)
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    for count in (0, -1, 5, True, 2.0, "2"):
        result = application.generate_visual_candidates(
            run_id,
            scene["scene_id"],
            _generate_payload(access, count=count),
        )
        assert result["error"]["code"] == "INVALID_VISUAL_GENERATION_REQUEST"
    injected = _generate_payload(access)
    injected["provider"] = "arbitrary"
    assert (
        application.generate_visual_candidates(run_id, scene["scene_id"], injected)["error"]["code"]
        == "INVALID_VISUAL_GENERATION_REQUEST"
    )
    assert provider.calls == []


def test_partial_invalid_timeout_and_duplicate_outputs_are_sanitized(
    tmp_path: Path,
) -> None:
    cases = (
        (DeterministicVisualProvider(fail_indices={2}), "PARTIAL_SUCCESS", 1, 1),
        (DeterministicVisualProvider(invalid_mime=True), "FAILED", 0, 2),
        (DeterministicVisualProvider(timeout=True), "FAILED", 0, 2),
        (DeterministicVisualProvider(duplicate_bytes=True), "PARTIAL_SUCCESS", 1, 1),
        (DeterministicVisualProvider(zero_bytes=True), "FAILED", 0, 2),
        (DeterministicVisualProvider(invalid_dimensions=True), "FAILED", 0, 2),
        (DeterministicVisualProvider(mime_extension_mismatch=True), "FAILED", 0, 2),
    )
    for index, (provider, status, returned, invalid) in enumerate(cases):
        root = tmp_path / str(index)
        application, run_id, _, scene = _accepted_scene_application(root, provider)
        access = application.get_visual_candidate_access(run_id, scene["scene_id"])
        result = application.generate_visual_candidates(
            run_id, scene["scene_id"], _generate_payload(access)
        )
        serialized = json.dumps(result)
        attempt = result["access"]["collection"]["attempts"][0]
        assert attempt["status"] == status
        assert attempt["returned_candidate_count"] == returned
        assert attempt["invalid_candidate_count"] == invalid
        assert "provider-secret-marker" not in serialized
        assert str(tmp_path) not in serialized


def test_review_history_accept_reject_revision_and_supersession(
    tmp_path: Path,
) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, _, scene = _accepted_scene_application(tmp_path, provider)
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    generated = application.generate_visual_candidates(
        run_id, scene["scene_id"], _generate_payload(access)
    )
    collection = generated["access"]["collection"]
    first, second = collection["candidates"]

    rejected = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        first["candidate_id"],
        "reject",
        _mutation_payload(collection, first),
    )
    collection = rejected["access"]["collection"]
    first = collection["candidates"][0]
    assert first["status"] == "REJECTED"
    assert (
        application.mutate_visual_candidate(
            run_id,
            scene["scene_id"],
            first["candidate_id"],
            "accept",
            _mutation_payload(collection, first),
        )["error"]["code"]
        == "REJECTED_VISUAL_CANDIDATE"
    )

    second = collection["candidates"][1]
    accepted = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        second["candidate_id"],
        "accept",
        _mutation_payload(collection, second),
    )
    collection = accepted["access"]["collection"]
    second = collection["candidates"][1]
    assert second["accepted_for_composition_planning"] is True
    assert collection["current_accepted_candidate_id"] == second["candidate_id"]
    assert (
        application.mutate_visual_candidate(
            run_id,
            scene["scene_id"],
            second["candidate_id"],
            "accept",
            _mutation_payload(collection, second),
        )["error"]["code"]
        == "VISUAL_CANDIDATE_ALREADY_ACCEPTED"
    )

    revision = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        second["candidate_id"],
        "request-revision",
        _mutation_payload(collection, second, reason="STYLE_MISMATCH"),
    )
    collection = revision["access"]["collection"]
    assert collection["current_accepted_candidate_id"] is None
    assert collection["candidates"][1]["status"] == "REVISION_REQUESTED"
    assert len(collection["candidates"][1]["review_history"]) == 2

    regenerate = _generate_payload(revision["access"], count=1)
    regenerated = application.generate_visual_candidates(run_id, scene["scene_id"], regenerate)
    collection = regenerated["access"]["collection"]
    assert collection["requests"][-1]["correction_dimensions"] == ["STYLE_MISMATCH"]
    assert collection["requests"][-1]["note"] == "Bounded visual review note."
    new_candidate = collection["candidates"][-1]
    reaccepted = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        new_candidate["candidate_id"],
        "accept",
        _mutation_payload(collection, new_candidate),
    )
    final = reaccepted["access"]["collection"]
    assert final["current_accepted_candidate_id"] == new_candidate["candidate_id"]
    assert final["candidates"][1]["review_history"] == collection["candidates"][1]["review_history"]


def test_scene_and_storyplan_changes_invalidate_old_visual_authority(
    tmp_path: Path,
) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, scene_collection, scene = _accepted_scene_application(tmp_path, provider)
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    stale_payload = _generate_payload(access, count=1)
    generated = application.generate_visual_candidates(run_id, scene["scene_id"], stale_payload)
    old_candidate = generated["access"]["collection"]["candidates"][0]
    stale_payload = _generate_payload(generated["access"], count=1)

    edited = application.mutate_scene_plan(
        run_id,
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": scene_collection["collection_revision_id"],
            "base_scene_revision_id": scene["scene_revision_id"],
            "changes": {"environment": "A changed current environment"},
        },
        scene_id=scene["scene_id"],
    )
    assert edited["error"] is None
    stale = application.generate_visual_candidates(run_id, scene["scene_id"], stale_payload)
    assert stale["error"]["code"] == "VISUAL_CANDIDATE_NOT_AUTHORIZED"

    edited_collection = edited["access"]["collection"]
    edited_scene = edited_collection["scenes"][0]
    reaccepted = application.mutate_scene_plan(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": edited_collection["collection_revision_id"],
            "scene_id": edited_scene["scene_id"],
            "base_scene_revision_id": edited_scene["scene_revision_id"],
        },
        scene_id=edited_scene["scene_id"],
    )
    new_access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    assert (
        new_access["collection"]["visual_collection_id"]
        != access["collection"]["visual_collection_id"]
    )
    historical = application.get_visual_candidate(
        run_id, scene["scene_id"], old_candidate["candidate_id"]
    )
    assert historical["status"] == "SUPERSEDED"
    assert historical["superseded_reason"] == "UPSTREAM_SCENE_AUTHORITY_CHANGED"
    assert (
        application.get_visual_candidate_artifact(
            run_id, scene["scene_id"], old_candidate["candidate_id"]
        )
        is None
    )

    application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-0001",
            "feedback": ["story_focus_incorrect"],
            "custom_note": None,
        },
    )
    access_after_replan = application.get_visual_candidate_access(run_id, scene["scene_id"])
    assert access_after_replan["request_authorized"] is False
    assert "STORYPLAN_NOT_ACCEPTED" in access_after_replan["blocker_codes"]
    assert reaccepted["error"] is None


def test_exact_candidate_and_artifact_http_routes(tmp_path: Path) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, _, scene = _accepted_scene_application(tmp_path, provider)
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    generated = application.generate_visual_candidates(
        run_id, scene["scene_id"], _generate_payload(access, count=1)
    )
    candidate = generated["access"]["collection"]["candidates"][0]
    ui_root = tmp_path / "ui"
    ui_root.mkdir()
    (ui_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    server = create_plan_only_http_server(
        port=0,
        allow_ephemeral_port=True,
        ui_root=ui_root,
        application=application,
    )
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address[:2]
    try:
        connection = http.client.HTTPConnection(*address, timeout=2)
        connection.request("GET", candidate["artifact_url"])
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        connection.close()
        assert response.status == 200
        assert headers["Content-Type"] == "image/png"
        assert headers["Cache-Control"] == "no-store"
        assert hashlib.sha256(body).hexdigest() == candidate["sha256"]

        connection = http.client.HTTPConnection(*address, timeout=2)
        connection.request(
            "GET",
            candidate["artifact_url"].replace(candidate["candidate_id"], "../secret"),
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        assert response.status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_candidate_lookup_and_process_local_identities_are_exact_and_unique(
    tmp_path: Path,
) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, accepted_collection, first_scene = _accepted_scene_application(
        tmp_path, provider
    )
    first_access = application.get_visual_candidate_access(run_id, first_scene["scene_id"])
    first_result = application.generate_visual_candidates(
        run_id, first_scene["scene_id"], _generate_payload(first_access, count=1)
    )
    first_candidate = first_result["access"]["collection"]["candidates"][0]
    assert (
        application.get_visual_candidate(
            run_id, first_scene["scene_id"], first_candidate["candidate_id"]
        )
        == first_candidate
    )
    assert (
        application.get_visual_candidate(
            run_id, first_scene["scene_id"], "visual-candidate-9999-01"
        )
        is None
    )

    second_scene = accepted_collection["scenes"][1]
    second_accepted = application.mutate_scene_plan(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": accepted_collection["collection_revision_id"],
            "scene_id": second_scene["scene_id"],
            "base_scene_revision_id": second_scene["scene_revision_id"],
        },
        scene_id=second_scene["scene_id"],
    )
    current_second = second_accepted["access"]["collection"]["scenes"][1]
    second_access = application.get_visual_candidate_access(run_id, current_second["scene_id"])
    second_result = application.generate_visual_candidates(
        run_id, current_second["scene_id"], _generate_payload(second_access, count=1)
    )
    second_candidate = second_result["access"]["collection"]["candidates"][0]
    assert second_candidate["candidate_id"] != first_candidate["candidate_id"]
    assert (
        second_result["access"]["collection"]["visual_collection_id"]
        != first_result["access"]["collection"]["visual_collection_id"]
    )
    assert (
        application.get_visual_candidate_artifact(
            run_id, first_scene["scene_id"], second_candidate["candidate_id"]
        )
        is None
    )


def test_visual_routes_reject_malformed_json_methods_and_unknown_paths(
    tmp_path: Path,
) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, _, scene = _accepted_scene_application(tmp_path, provider)
    base = (
        f"/api/v1/plan-only/runs/{run_id}/scene-plan/scenes/{scene['scene_id']}/visual-candidates"
    )
    ui_root = tmp_path / "ui"
    ui_root.mkdir()
    (ui_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    server = create_plan_only_http_server(
        port=0,
        allow_ephemeral_port=True,
        ui_root=ui_root,
        application=application,
    )
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for method, path, body, expected, expected_code in (
            ("POST", f"{base}/generate", b"{", 400, "MALFORMED_JSON"),
            ("PUT", f"{base}/generate", b"{}", 405, "METHOD_NOT_ALLOWED"),
            ("GET", f"{base}/unknown", None, 404, "PLAN_ONLY_ROUTE_NOT_FOUND"),
            ("POST", base, b"{}", 405, "METHOD_NOT_ALLOWED"),
        ):
            connection = http.client.HTTPConnection(*server.server_address[:2], timeout=2)
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            connection.close()
            assert response.status == expected
            assert response.getheader("Content-Length") == str(len(payload))
            assert json.loads(payload)["error"]["code"] == expected_code
            assert str(tmp_path).encode() not in payload
        assert provider.calls == []

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_short_lived_loopback_fake_provider_visual_lifecycle(tmp_path: Path) -> None:
    provider = DeterministicVisualProvider()
    application = PlanOnlyApplication(
        visual_provider=provider,
        visual_artifact_root=tmp_path / "artifacts",
    )
    ui_root = tmp_path / "ui"
    ui_root.mkdir()
    (ui_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    server = create_plan_only_http_server(
        port=0,
        allow_ephemeral_port=True,
        ui_root=ui_root,
        application=application,
    )
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address[:2]

    def request(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> tuple[int, object]:
        body = None if payload is None else json.dumps(payload).encode()
        headers = (
            {}
            if body is None
            else {"Content-Type": "application/json", "Content-Length": str(len(body))}
        )
        connection = http.client.HTTPConnection(*address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        status, created = request("POST", "/api/v1/plan-only/runs", _request_payload())
        assert status == 200
        run_id = created["run"]["run_id"]
        root = f"/api/v1/plan-only/runs/{run_id}"
        assert (
            request(
                "POST",
                f"{root}/review/accept",
                {"schema_version": 1, "current_revision_id": "revision-0001"},
            )[0]
            == 200
        )
        _, initialized = request(
            "POST",
            f"{root}/scene-plan/initialize",
            {"schema_version": 1, "accepted_story_revision_id": "revision-0001"},
        )
        scene_collection = initialized["access"]["collection"]
        scene = scene_collection["scenes"][0]
        _, accepted = request(
            "POST",
            f"{root}/scene-plan/scenes/{scene['scene_id']}/accept",
            {
                "schema_version": 1,
                "base_collection_revision_id": scene_collection["collection_revision_id"],
                "scene_id": scene["scene_id"],
                "base_scene_revision_id": scene["scene_revision_id"],
            },
        )
        scene = accepted["access"]["collection"]["scenes"][0]
        visual_base = f"{root}/scene-plan/scenes/{scene['scene_id']}/visual-candidates"
        _, access = request("GET", visual_base)
        _, generated = request("POST", f"{visual_base}/generate", _generate_payload(access))
        collection = generated["access"]["collection"]
        first, second = collection["candidates"]
        for candidate in (first, second):
            connection = http.client.HTTPConnection(*address, timeout=3)
            connection.request("GET", candidate["artifact_url"])
            response = connection.getresponse()
            content = response.read()
            connection.close()
            assert response.status == 200
            assert response.getheader("Content-Type") == candidate["mime_type"]
            assert hashlib.sha256(content).hexdigest() == candidate["sha256"]

        _, rejected = request(
            "POST",
            f"{visual_base}/{first['candidate_id']}/reject",
            _mutation_payload(collection, first),
        )
        collection = rejected["access"]["collection"]
        second = collection["candidates"][1]
        _, accepted_candidate = request(
            "POST",
            f"{visual_base}/{second['candidate_id']}/accept",
            _mutation_payload(collection, second),
        )
        collection = accepted_candidate["access"]["collection"]
        assert collection["render_authority"] is False
        assert collection["final_media_capability"] is False
        second = collection["candidates"][1]
        _, revision = request(
            "POST",
            f"{visual_base}/{second['candidate_id']}/request-revision",
            _mutation_payload(collection, second, reason="STYLE_MISMATCH"),
        )
        _, revised_generation = request(
            "POST",
            f"{visual_base}/generate",
            _generate_payload(revision["access"], count=1),
        )
        revised_collection = revised_generation["access"]["collection"]
        revised = revised_collection["candidates"][-1]
        _, final_acceptance = request(
            "POST",
            f"{visual_base}/{revised['candidate_id']}/accept",
            _mutation_payload(revised_collection, revised),
        )
        final_collection = final_acceptance["access"]["collection"]
        assert final_collection["current_accepted_candidate_id"] == revised["candidate_id"]
        assert final_collection["candidates"][1]["review_history"]

        _, scene_access = request("GET", f"{root}/scene-plan")
        current_scene = scene_access["collection"]["scenes"][0]
        _, requested_revision = request(
            "POST",
            f"{root}/scene-plan/scenes/{scene['scene_id']}/request-revision",
            {
                "schema_version": 1,
                "base_collection_revision_id": scene_access["collection"]["collection_revision_id"],
                "scene_id": scene["scene_id"],
                "base_scene_revision_id": current_scene["scene_revision_id"],
                "reason_code": "CONTINUITY_NEEDS_REVISION",
                "note": "Change the environment.",
            },
        )
        final_candidate = final_collection["candidates"][-1]
        stale_status, stale = request(
            "POST",
            f"{visual_base}/{revised['candidate_id']}/reject",
            _mutation_payload(final_collection, final_candidate),
        )
        assert stale_status == 200
        assert stale["error"]["code"] == "VISUAL_CANDIDATE_NOT_AUTHORIZED"
        revision_collection = requested_revision["access"]["collection"]
        revision_scene = revision_collection["scenes"][0]
        _, edited = request(
            "POST",
            f"{root}/scene-plan/scenes/{scene['scene_id']}/revisions",
            {
                "schema_version": 1,
                "base_collection_revision_id": revision_collection["collection_revision_id"],
                "base_scene_revision_id": revision_scene["scene_revision_id"],
                "changes": {"environment": "A changed environment"},
            },
        )
        edited_collection = edited["access"]["collection"]
        edited_scene = edited_collection["scenes"][0]
        request(
            "POST",
            f"{root}/scene-plan/scenes/{scene['scene_id']}/accept",
            {
                "schema_version": 1,
                "base_collection_revision_id": edited_collection["collection_revision_id"],
                "scene_id": edited_scene["scene_id"],
                "base_scene_revision_id": edited_scene["scene_revision_id"],
            },
        )
        _, new_access = request("GET", visual_base)
        assert (
            new_access["collection"]["visual_collection_id"]
            != final_collection["visual_collection_id"]
        )
        _, historical = request("GET", f"{visual_base}/{revised['candidate_id']}")
        assert historical["status"] == "SUPERSEDED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()

    connection = http.client.HTTPConnection(*address, timeout=0.2)
    try:
        with __import__("pytest").raises(OSError):
            connection.connect()
    finally:
        connection.close()
