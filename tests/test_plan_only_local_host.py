from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import subprocess
from threading import Thread

import pytest

from tella.topic_production.local_host import (
    DEFAULT_PLAN_ONLY_HOST,
    DEFAULT_PLAN_ONLY_PORT,
    MAX_PLAN_ONLY_REQUEST_BYTES,
    create_plan_only_http_server,
)
from tella.topic_production.plan_only_application import PlanOnlyApplication


def _request_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "input_mode": "TOPIC",
        "source_content": "Learning to make room for uncertainty",
        "language": "en",
        "character_scope": "recurring_female",
        "requested_scene_count": 8,
    }


@contextmanager
def _running_host(
    tmp_path: Path,
    *,
    application: PlanOnlyApplication | None = None,
) -> Iterator[tuple[str, int]]:
    ui_root = tmp_path / "dist"
    ui_root.mkdir()
    (ui_root / "index.html").write_text("<!doctype html><title>PLAN_ONLY</title>", "utf-8")
    server = create_plan_only_http_server(
        port=0,
        ui_root=ui_root,
        application=application,
        allow_ephemeral_port=True,
    )
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def _http_request(
    address: tuple[str, int],
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object], dict[str, str]]:
    connection = http.client.HTTPConnection(*address, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload, dict(response.getheaders())
    finally:
        connection.close()


def _json_request(
    address: tuple[str, int],
    method: str,
    path: str,
    payload: object,
) -> tuple[int, dict[str, object], dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _http_request(
        address,
        method,
        path,
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )


def test_default_binding_is_loopback_with_bounded_port(tmp_path: Path) -> None:
    ui_root = tmp_path / "dist"
    ui_root.mkdir()
    (ui_root / "index.html").write_text("PLAN_ONLY", "utf-8")

    server = create_plan_only_http_server(
        port=0,
        ui_root=ui_root,
        allow_ephemeral_port=True,
    )
    try:
        assert server.server_address[0] == DEFAULT_PLAN_ONLY_HOST
        assert 1024 <= DEFAULT_PLAN_ONLY_PORT <= 65535
    finally:
        server.server_close()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.168.1.10"])
def test_non_loopback_or_ambiguous_binding_is_rejected(tmp_path: Path, host: str) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_plan_only_http_server(host=host, port=8765, ui_root=tmp_path)


def test_unavailable_port_fails_at_startup(tmp_path: Path) -> None:
    ui_root = tmp_path / "dist"
    ui_root.mkdir()
    (ui_root / "index.html").write_text("PLAN_ONLY", "utf-8")
    first = create_plan_only_http_server(
        port=0,
        ui_root=ui_root,
        allow_ephemeral_port=True,
    )
    try:
        with pytest.raises(OSError):
            create_plan_only_http_server(
                port=int(first.server_address[1]),
                ui_root=ui_root,
            )
    finally:
        first.server_close()


def test_health_and_capabilities_are_exact_and_cors_free(tmp_path: Path) -> None:
    with _running_host(tmp_path) as address:
        status, health, headers = _http_request(address, "GET", "/api/v1/plan-only/health")
        capability_status, capabilities, _ = _http_request(
            address, "GET", "/api/v1/plan-only/capabilities"
        )

    assert status == 200
    assert health == {
        "schema_version": 1,
        "status": "ok",
        "contract_version": "v1",
        "plan_only_available": True,
        "render_available": False,
    }
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert "Access-Control-Allow-Origin" not in headers
    assert capability_status == 200
    assert capabilities["full_render_enabled"] is False
    assert capabilities["backend_render_capability"] is False


def test_acceptance_create_list_and_exact_lookup_use_actual_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("external media process invocation is forbidden")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    with _running_host(tmp_path) as address:
        create_status, created, _ = _json_request(
            address,
            "POST",
            "/api/v1/plan-only/runs",
            _request_payload(),
        )
        run = created["run"]
        assert isinstance(run, dict)
        run_id = run["run_id"]
        list_status, dashboard, _ = _http_request(address, "GET", "/api/v1/plan-only/runs")
        get_status, retrieved, _ = _http_request(address, "GET", f"/api/v1/plan-only/runs/{run_id}")
        unknown_status, unknown, _ = _http_request(
            address, "GET", "/api/v1/plan-only/runs/plan-00000000000000000000"
        )

    assert create_status == 200
    assert run["execution_mode"] == "PLAN_ONLY"
    assert run["render_readiness"]["ready"] is False
    assert list_status == 200
    assert [item["run_id"] for item in dashboard["runs"]] == [run_id]
    assert get_status == 200
    assert retrieved["run_id"] == run_id
    assert unknown_status == 404
    assert unknown["error"]["code"] == "PLAN_ONLY_ROUTE_NOT_FOUND"


def test_review_revision_routes_methods_and_malformed_json_are_bounded(
    tmp_path: Path,
) -> None:
    with _running_host(tmp_path) as address:
        _, created, _ = _json_request(
            address,
            "POST",
            "/api/v1/plan-only/runs",
            _request_payload(),
        )
        run = created["run"]
        assert isinstance(run, dict)
        run_id = str(run["run_id"])
        review_path = f"/api/v1/plan-only/runs/{run_id}/review"
        accept_path = f"{review_path}/accept"
        revisions_path = f"/api/v1/plan-only/runs/{run_id}/revisions"

        review_status, review, _ = _http_request(address, "GET", review_path)
        accept_status, accepted, _ = _json_request(
            address,
            "POST",
            accept_path,
            {"schema_version": 1, "current_revision_id": "revision-0001"},
        )
        history_status, history, _ = _http_request(
            address,
            "GET",
            revisions_path,
        )
        revision_status, revision, _ = _http_request(
            address,
            "GET",
            f"{revisions_path}/revision-0001",
        )
        method_status, method_error, method_headers = _http_request(
            address,
            "POST",
            review_path,
            body=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2"},
        )
        malformed_status, malformed, _ = _http_request(
            address,
            "POST",
            revisions_path,
            body=b"{",
            headers={"Content-Type": "application/json", "Content-Length": "1"},
        )

    assert review_status == 200
    assert review["review_status"] == "UNREVIEWED"
    assert review["render_authority"] is False
    assert accept_status == 200
    assert accepted["review"]["review_status"] == "ACCEPTED_FOR_SCENE_PLANNING"
    assert history_status == 200
    assert history["current_revision_id"] == "revision-0001"
    assert revision_status == 200
    assert revision["revision_id"] == "revision-0001"
    assert method_status == 405
    assert method_error["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert method_headers["Allow"] == "GET"
    assert malformed_status == 400
    assert malformed["error"]["code"] == "MALFORMED_JSON"


def test_scene_plan_routes_are_loopback_bounded_and_method_strict(
    tmp_path: Path,
) -> None:
    with _running_host(tmp_path) as address:
        _, created, _ = _json_request(
            address,
            "POST",
            "/api/v1/plan-only/runs",
            _request_payload(),
        )
        run_id = str(created["run"]["run_id"])
        root = f"/api/v1/plan-only/runs/{run_id}"
        _json_request(
            address,
            "POST",
            f"{root}/review/accept",
            {"schema_version": 1, "current_revision_id": "revision-0001"},
        )
        initialized_status, initialized, _ = _json_request(
            address,
            "POST",
            f"{root}/scene-plan/initialize",
            {"schema_version": 1, "accepted_story_revision_id": "revision-0001"},
        )
        access_status, access, _ = _http_request(address, "GET", f"{root}/scene-plan")
        scene_status, scene, _ = _http_request(address, "GET", f"{root}/scene-plan/scenes/scene_01")
        method_status, method_error, headers = _http_request(
            address, "GET", f"{root}/scene-plan/initialize"
        )
        malformed_status, malformed, _ = _http_request(
            address,
            "POST",
            f"{root}/scene-plan/reorder",
            body=b"{",
            headers={"Content-Type": "application/json", "Content-Length": "1"},
        )

    assert initialized_status == 200
    assert initialized["access"]["collection"]["render_authority"] is False
    assert access_status == 200
    assert access["editable"] is True
    assert scene_status == 200
    assert scene["scene_revision_id"] == "scene-revision-0001-01"
    assert method_status == 405
    assert method_error["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert headers["Allow"] == "POST"
    assert malformed_status == 400
    assert malformed["error"]["code"] == "MALFORMED_JSON"


@pytest.mark.parametrize(
    ("body", "expected_status", "expected_code"),
    [
        (b"{", 400, "MALFORMED_JSON"),
        (b"\xff", 400, "MALFORMED_JSON"),
        (b"", 400, "MALFORMED_JSON"),
    ],
)
def test_malformed_json_is_sanitized(
    tmp_path: Path,
    body: bytes,
    expected_status: int,
    expected_code: str,
) -> None:
    with _running_host(tmp_path) as address:
        status, payload, _ = _http_request(
            address,
            "POST",
            "/api/v1/plan-only/runs",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

    assert status == expected_status
    assert payload["error"]["code"] == expected_code
    assert "Traceback" not in json.dumps(payload)


def test_json_null_reaches_strict_application_validation(tmp_path: Path) -> None:
    with _running_host(tmp_path) as address:
        status, payload, _ = _json_request(
            address,
            "POST",
            "/api/v1/plan-only/runs",
            None,
        )

    assert status == 200
    assert payload["run"] is None
    assert payload["error"]["code"] == "INVALID_PLAN_ONLY_REQUEST"


def test_oversized_body_is_rejected_before_application(tmp_path: Path) -> None:
    class SentinelApplication(PlanOnlyApplication):
        def create_run(self, payload: object) -> dict[str, object]:
            del payload
            raise AssertionError("oversized payload reached application")

    with _running_host(tmp_path, application=SentinelApplication()) as address:
        status, payload, _ = _http_request(
            address,
            "POST",
            "/api/v1/plan-only/runs",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_PLAN_ONLY_REQUEST_BYTES + 1),
            },
        )

    assert status == 413
    assert payload["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


def test_unsupported_method_and_unknown_api_path_are_rejected(tmp_path: Path) -> None:
    with _running_host(tmp_path) as address:
        method_status, method_payload, method_headers = _http_request(
            address, "PUT", "/api/v1/plan-only/runs"
        )
        path_status, path_payload, _ = _http_request(address, "GET", "/api/v1/plan-only/render")
        options_status, _, options_headers = _http_request(
            address, "OPTIONS", "/api/v1/plan-only/runs"
        )

    assert method_status == 405
    assert method_payload["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert method_headers["Allow"] == "GET, POST"
    assert path_status == 404
    assert path_payload["error"]["code"] == "PLAN_ONLY_ROUTE_NOT_FOUND"
    assert options_status == 405
    assert "Access-Control-Allow-Origin" not in options_headers


def test_internal_error_is_sanitized_without_secret_or_path_leakage(tmp_path: Path) -> None:
    class FailingApplication(PlanOnlyApplication):
        def capabilities(self) -> dict[str, object]:
            raise RuntimeError(r"secret-token C:\private\credentials.json")

    with _running_host(tmp_path, application=FailingApplication()) as address:
        status, payload, _ = _http_request(address, "GET", "/api/v1/plan-only/capabilities")

    encoded = json.dumps(payload)
    assert status == 500
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "secret-token" not in encoded
    assert "credentials" not in encoded
    assert "C:" not in encoded


def test_static_host_serves_only_resolved_ui_root_without_listing(tmp_path: Path) -> None:
    outside = tmp_path / "private.txt"
    outside.write_text("secret", "utf-8")
    with _running_host(tmp_path) as address:
        connection = http.client.HTTPConnection(*address, timeout=2)
        try:
            connection.request("GET", "/")
            root = connection.getresponse()
            root_body = root.read().decode("utf-8")
        finally:
            connection.close()
        traversal_status, traversal, _ = _http_request(address, "GET", "/%2e%2e/private.txt")

    assert root.status == 200
    assert "<title>PLAN_ONLY</title>" in root_body
    assert traversal_status == 404
    assert "secret" not in json.dumps(traversal)


def test_external_index_symlink_is_not_served(tmp_path: Path) -> None:
    ui_root = tmp_path / "dist"
    ui_root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("private", "utf-8")
    try:
        (ui_root / "index.html").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(FileNotFoundError, match="built UI is unavailable"):
        create_plan_only_http_server(
            port=0,
            ui_root=ui_root,
            allow_ephemeral_port=True,
        )
