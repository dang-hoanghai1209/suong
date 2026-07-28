"""Loopback-only HTTP host for the bounded PLAN_ONLY application."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import socket
import sys
from threading import RLock
from typing import Any
from urllib.parse import unquote, urlsplit

from .plan_only_application import (
    PLAN_ONLY_API_PREFIX,
    PlanOnlyApplication,
    dispatch_plan_only_request,
)


DEFAULT_PLAN_ONLY_HOST = "127.0.0.1"
DEFAULT_PLAN_ONLY_PORT = 8765
MAX_PLAN_ONLY_REQUEST_BYTES = 64 * 1024
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def _health_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "ok",
        "contract_version": "v1",
        "plan_only_available": True,
        "render_available": False,
    }


def _error_payload(code: str, message: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _validate_binding(host: str, port: int, *, allow_ephemeral_port: bool) -> None:
    if host != DEFAULT_PLAN_ONLY_HOST:
        raise ValueError("PLAN_ONLY local host must bind to 127.0.0.1")
    if type(port) is not int or not (0 if allow_ephemeral_port else 1024) <= port <= 65535:
        raise ValueError("PLAN_ONLY local host port is outside the allowed range")


def _known_api_methods(path: str) -> frozenset[str] | None:
    if path in {
        f"{PLAN_ONLY_API_PREFIX}/health",
        f"{PLAN_ONLY_API_PREFIX}/capabilities",
    }:
        return frozenset({"GET"})
    if path == f"{PLAN_ONLY_API_PREFIX}/runs":
        return frozenset({"GET", "POST"})
    lookup_prefix = f"{PLAN_ONLY_API_PREFIX}/runs/"
    if not path.startswith(lookup_prefix):
        return None
    parts = path[len(lookup_prefix) :].split("/")
    if not all(parts):
        return None
    if len(parts) == 1:
        return frozenset({"GET"})
    if len(parts) == 2 and parts[1] == "review":
        return frozenset({"GET"})
    if len(parts) == 3 and parts[1:] == ["review", "accept"]:
        return frozenset({"POST"})
    if len(parts) == 2 and parts[1] == "revisions":
        return frozenset({"GET", "POST"})
    if len(parts) == 3 and parts[1] == "revisions":
        return frozenset({"GET"})
    if len(parts) >= 2 and parts[1] == "scene-plan":
        tail = parts[2:]
        if not tail:
            return frozenset({"GET"})
        if tail == ["initialize"]:
            return frozenset({"POST"})
        if tail == ["revisions"]:
            return frozenset({"GET"})
        if len(tail) == 2 and tail[0] == "revisions":
            return frozenset({"GET"})
        if len(tail) >= 3 and tail[0] == "scenes" and tail[2] == "visual-candidates":
            visual_tail = tail[3:]
            if not visual_tail:
                return frozenset({"GET"})
            if visual_tail == ["generate"]:
                return frozenset({"POST"})
            if len(visual_tail) == 1:
                return frozenset({"GET"})
            if len(visual_tail) == 2 and visual_tail[1] == "artifact":
                return frozenset({"GET"})
            if len(visual_tail) == 2 and visual_tail[1] in {
                "accept",
                "reject",
                "request-revision",
            }:
                return frozenset({"POST"})
        if len(tail) == 1 and tail[0] in {
            "reorder",
            "split",
            "merge",
            "duplicate",
        }:
            return frozenset({"POST"})
        if len(tail) == 2 and tail[0] == "scenes":
            return frozenset({"GET"})
        if len(tail) == 3 and tail[0] == "scenes":
            if tail[2] == "history":
                return frozenset({"GET"})
            if tail[2] in {
                "revisions",
                "restore",
                "accept",
                "request-revision",
            }:
                return frozenset({"POST"})
    return None


def _visual_artifact_identity(path: str) -> tuple[str, str, str] | None:
    prefix = f"{PLAN_ONLY_API_PREFIX}/runs/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix) :].split("/")
    if (
        len(parts) == 7
        and parts[1] == "scene-plan"
        and parts[2] == "scenes"
        and parts[4] == "visual-candidates"
        and parts[6] == "artifact"
        and all(parts)
    ):
        return parts[0], parts[3], parts[5]
    return None


class _PlanOnlyHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        application: PlanOnlyApplication,
        ui_root: Path,
    ) -> None:
        self.plan_only_application = application
        self.plan_only_application_lock = RLock()
        self.ui_root = ui_root
        super().__init__(server_address, _PlanOnlyRequestHandler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _PlanOnlyRequestHandler(BaseHTTPRequestHandler):
    server: _PlanOnlyHttpServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress request details and local paths from console output."""

    def _send_json(self, status: int, payload: object) -> None:
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            body = (
                b'{"error":{"code":"INTERNAL_ERROR",'
                b'"message":"The local host could not complete the request."},'
                b'"schema_version":1}'
            )
        self.send_response(status)
        self.send_header("Content-Type", _JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_error(self, status: int, code: str, message: str) -> None:
        self.close_connection = True
        self._send_json(status, _error_payload(code, message))

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del message, explain
        self._send_error(
            code,
            "INVALID_HTTP_REQUEST",
            "The local host rejected the HTTP request.",
        )

    def _request_path(self) -> str | None:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            return None
        return unquote(parsed.path, errors="strict")

    def _serve_static(self, path: str) -> None:
        decoded = unquote(path, errors="strict")
        relative = decoded.lstrip("/")
        requested = (self.server.ui_root / relative).resolve()
        try:
            requested.relative_to(self.server.ui_root)
        except ValueError:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "LOCAL_RESOURCE_NOT_FOUND",
                "The requested local resource was not found.",
            )
            return
        if decoded == "/" or not requested.is_file():
            if requested.suffix:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "LOCAL_RESOURCE_NOT_FOUND",
                    "The requested local resource was not found.",
                )
                return
            requested = (self.server.ui_root / "index.html").resolve()
            try:
                requested.relative_to(self.server.ui_root)
            except ValueError:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "LOCAL_RESOURCE_NOT_FOUND",
                    "The requested local resource was not found.",
                )
                return
        try:
            body = requested.read_bytes()
        except OSError:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "LOCAL_RESOURCE_NOT_FOUND",
                "The requested local resource was not found.",
            )
            return
        content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            f"{content_type}; charset=utf-8"
            if content_type.startswith("text/") or content_type == "application/javascript"
            else content_type,
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch_api(self, method: str, path: str, body: object | None = None) -> None:
        if method == "GET" and path == f"{PLAN_ONLY_API_PREFIX}/health":
            self._send_json(HTTPStatus.OK, _health_payload())
            return
        try:
            with self.server.plan_only_application_lock:
                response = dispatch_plan_only_request(
                    self.server.plan_only_application,
                    method=method,
                    path=path,
                    body=body,
                )
            if response.status_code == HTTPStatus.NOT_FOUND:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "PLAN_ONLY_ROUTE_NOT_FOUND",
                    "The requested PLAN_ONLY route was not found.",
                )
                return
            self._send_json(response.status_code, response.payload)
        except Exception:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "The local host could not complete the request.",
            )

    def _serve_visual_artifact(self, path: str) -> None:
        identity = _visual_artifact_identity(path)
        if identity is None:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "PLAN_ONLY_ROUTE_NOT_FOUND",
                "The requested PLAN_ONLY route was not found.",
            )
            return
        run_id, scene_id, candidate_id = identity
        try:
            with self.server.plan_only_application_lock:
                artifact = self.server.plan_only_application.get_visual_candidate_artifact(
                    run_id,
                    scene_id,
                    candidate_id,
                )
        except Exception:
            artifact = None
        if artifact is None:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "VISUAL_ARTIFACT_NOT_FOUND",
                "The requested visual candidate artifact was not found.",
            )
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", artifact.mime_type)
        self.send_header("Content-Length", str(len(artifact.content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("ETag", f'"sha256-{artifact.sha256}"')
        self.end_headers()
        try:
            self.wfile.write(artifact.content)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self) -> None:
        try:
            path = self._request_path()
        except (UnicodeDecodeError, ValueError):
            path = None
        if path is None:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "PLAN_ONLY_ROUTE_NOT_FOUND",
                "The requested PLAN_ONLY route was not found.",
            )
            return
        if path.startswith("/api/"):
            methods = _known_api_methods(path)
            if methods is None:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "PLAN_ONLY_ROUTE_NOT_FOUND",
                    "The requested PLAN_ONLY route was not found.",
                )
                return
            if "GET" not in methods:
                self._method_not_allowed(methods)
                return
            if _visual_artifact_identity(path) is not None:
                self._serve_visual_artifact(path)
                return
            self._dispatch_api("GET", path)
            return
        self._serve_static(path)

    def _read_json_body(self) -> tuple[bool, object | None]:
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "UNSUPPORTED_TRANSFER_ENCODING",
                "The request transfer encoding is not supported.",
            )
            return False, None
        content_lengths = self.headers.get_all("Content-Length", [])
        if len(content_lengths) != 1:
            self._send_error(
                HTTPStatus.LENGTH_REQUIRED,
                "CONTENT_LENGTH_REQUIRED",
                "A single Content-Length header is required.",
            )
            return False, None
        try:
            content_length = int(content_lengths[0])
        except (TypeError, ValueError):
            content_length = -1
        if content_length < 0:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "INVALID_CONTENT_LENGTH",
                "Content-Length must be a non-negative integer.",
            )
            return False, None
        if content_length > MAX_PLAN_ONLY_REQUEST_BYTES:
            self._send_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "REQUEST_BODY_TOO_LARGE",
                "The PLAN_ONLY request body is too large.",
            )
            return False, None
        if self.headers.get_content_type() != "application/json":
            self._send_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "JSON_CONTENT_TYPE_REQUIRED",
                "The PLAN_ONLY request body must use application/json.",
            )
            return False, None
        try:
            return True, json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "MALFORMED_JSON",
                "The PLAN_ONLY request body is not valid JSON.",
            )
            return False, None

    def do_POST(self) -> None:
        try:
            path = self._request_path()
        except (UnicodeDecodeError, ValueError):
            path = None
        if path is None or not path.startswith("/api/"):
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "PLAN_ONLY_ROUTE_NOT_FOUND",
                "The requested PLAN_ONLY route was not found.",
            )
            return
        methods = _known_api_methods(path)
        if methods is None:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "PLAN_ONLY_ROUTE_NOT_FOUND",
                "The requested PLAN_ONLY route was not found.",
            )
            return
        if "POST" not in methods:
            self._method_not_allowed(methods)
            return
        body_valid, body = self._read_json_body()
        if not body_valid:
            return
        self._dispatch_api("POST", path, body)

    def _method_not_allowed(self, methods: frozenset[str] | None = None) -> None:
        self.close_connection = True
        allowed = methods or frozenset({"GET"})
        body = json.dumps(
            _error_payload(
                "METHOD_NOT_ALLOWED",
                "The HTTP method is not supported for this local route.",
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", ", ".join(sorted(allowed)))
        self.send_header("Content-Type", _JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_PUT(self) -> None:
        self._reject_unsupported_method()

    def do_PATCH(self) -> None:
        self._reject_unsupported_method()

    def do_DELETE(self) -> None:
        self._reject_unsupported_method()

    def do_OPTIONS(self) -> None:
        self._reject_unsupported_method()

    def do_HEAD(self) -> None:
        self._reject_unsupported_method()

    def do_TRACE(self) -> None:
        self._reject_unsupported_method()

    def do_CONNECT(self) -> None:
        self._reject_unsupported_method()

    def _reject_unsupported_method(self) -> None:
        try:
            path = self._request_path()
        except (UnicodeDecodeError, ValueError):
            path = None
        self._method_not_allowed(_known_api_methods(path or ""))


def create_plan_only_http_server(
    *,
    host: str = DEFAULT_PLAN_ONLY_HOST,
    port: int = DEFAULT_PLAN_ONLY_PORT,
    ui_root: Path | None = None,
    application: PlanOnlyApplication | None = None,
    allow_ephemeral_port: bool = False,
) -> ThreadingHTTPServer:
    """Create a loopback-only server without starting its request loop."""

    _validate_binding(host, port, allow_ephemeral_port=allow_ephemeral_port)
    resolved_ui_root = (ui_root or Path(__file__).resolve().parents[2] / "ui" / "dist").resolve()
    resolved_index = (resolved_ui_root / "index.html").resolve()
    try:
        resolved_index.relative_to(resolved_ui_root)
    except ValueError as error:
        raise FileNotFoundError("The built UI is unavailable; run npm run build in ui/") from error
    if not resolved_ui_root.is_dir() or not resolved_index.is_file():
        raise FileNotFoundError("The built UI is unavailable; run npm run build in ui/")
    return _PlanOnlyHttpServer(
        (host, port),
        application=application or PlanOnlyApplication(),
        ui_root=resolved_ui_root,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tella.topic_production.local_host",
        description="Serve the built PLAN_ONLY UI on a loopback-only local host.",
    )
    parser.add_argument("--host", default=DEFAULT_PLAN_ONLY_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PLAN_ONLY_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        server = create_plan_only_http_server(host=args.host, port=args.port)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    print(f"PLAN_ONLY local UI: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping PLAN_ONLY local UI.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_PLAN_ONLY_HOST",
    "DEFAULT_PLAN_ONLY_PORT",
    "MAX_PLAN_ONLY_REQUEST_BYTES",
    "create_plan_only_http_server",
    "main",
]
