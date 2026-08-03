"""Local production web server for real Tella renders."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from tella.web_contract import MAX_WEB_REQUEST_BYTES, WebRenderRequest
from tella.web_jobs import JobManager, web_readiness

_JOB_ROUTE = re.compile(
    r"^/api/jobs/(?P<job_id>web-[0-9a-f]{24})(?:/(?P<action>preview|download|cancel|compact|retry))?$"
)
_STATIC_ROOT = Path(__file__).with_name("web_static")


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ProductionWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        manager: JobManager,
        *,
        static_root: Path = _STATIC_ROOT,
    ) -> None:
        self.manager = manager
        self.static_root = Path(static_root).resolve()
        super().__init__(address, ProductionRequestHandler)

    def server_close(self) -> None:
        super().server_close()
        if not self.manager.close():
            raise RuntimeError("JOB_MANAGER_SHUTDOWN_INCOMPLETE")


class ProductionRequestHandler(BaseHTTPRequestHandler):
    server: ProductionWebServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        logging.getLogger(__name__).info("%s - %s", self.client_address[0], format % args)

    def _headers(
        self,
        status: HTTPStatus,
        content_type: str,
        length: int,
        *,
        disposition: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; media-src 'self'")
        if self.close_connection:
            self.send_header("Connection", "close")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = _json_bytes(payload)
        self._headers(status, "application/json; charset=utf-8", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _read_json(self) -> dict[str, Any] | None:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            self.close_connection = True
            self._error(
                HTTPStatus.BAD_REQUEST,
                "INVALID_CONTENT_LENGTH",
                "Invalid body framing.",
            )
            return None
        if not lengths:
            self.close_connection = True
            self._error(HTTPStatus.LENGTH_REQUIRED, "CONTENT_LENGTH_REQUIRED", "Body required.")
            return None
        raw_length = lengths[0]
        if not raw_length.isdecimal():
            self.close_connection = True
            self._error(HTTPStatus.BAD_REQUEST, "INVALID_CONTENT_LENGTH", "Invalid body size.")
            return None
        length = int(raw_length)
        if length <= 0 or length > MAX_WEB_REQUEST_BYTES:
            if length > MAX_WEB_REQUEST_BYTES:
                self.close_connection = True
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "REQUEST_SIZE_INVALID",
                f"JSON body must be 1..{MAX_WEB_REQUEST_BYTES} bytes.",
            )
            return None
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Body must be valid UTF-8 JSON.")
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "INVALID_JSON_OBJECT", "Body must be an object.")
            return None
        return payload

    def _discard_body(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            self.close_connection = True
            return
        if not lengths:
            return
        raw_length = lengths[0]
        if not raw_length.isdecimal():
            self.close_connection = True
            return
        length = int(raw_length)
        if 0 < length <= MAX_WEB_REQUEST_BYTES:
            self.rfile.read(length)
        elif length > MAX_WEB_REQUEST_BYTES:
            self.close_connection = True

    def do_GET(self) -> None:
        target = urlsplit(self.path)
        path = target.path
        if path == "/api/health":
            media_source = parse_qs(target.query).get("media_source", ["ai_image"])[-1]
            if media_source != "ai_image":
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "INVALID_MEDIA_SOURCE",
                    "Unsupported media source.",
                )
                return
            self._json(
                HTTPStatus.OK,
                web_readiness(
                    self.server.manager.output_root,
                    media_source=media_source,
                    storage_summary=self.server.manager.storage_summary(),
                ),
            )
            return
        if path == "/api/storage":
            self._json(
                HTTPStatus.OK,
                self.server.manager.storage_summary().model_dump(mode="json"),
            )
            return
        if path == "/api/jobs":
            self._json(
                HTTPStatus.OK,
                {"jobs": [job.model_dump(mode="json") for job in self.server.manager.list()]},
            )
            return
        match = _JOB_ROUTE.fullmatch(path)
        if match:
            self._get_job(match.group("job_id"), match.group("action"))
            return
        if path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "API route not found.")
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/jobs":
            payload = self._read_json()
            if payload is None:
                return
            try:
                request = WebRenderRequest.model_validate(payload, strict=True)
            except ValidationError as exc:
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "INVALID_RENDER_REQUEST",
                    "; ".join(error["msg"] for error in exc.errors())[:1000],
                )
                return
            readiness = web_readiness(
                self.server.manager.output_root,
                request,
                storage_summary=self.server.manager.storage_summary(),
            )
            if not readiness["ready"]:
                storage = readiness.get("storage")
                if isinstance(storage, dict) and storage.get("configuration_valid") is False:
                    self._error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "STORAGE_CONFIGURATION_INVALID",
                        "The server storage policy is invalid.",
                    )
                    return
                if isinstance(storage, dict) and storage.get("submissions_allowed") is False:
                    self._json(
                        HTTPStatus.INSUFFICIENT_STORAGE,
                        {
                            "error": {
                                "code": "INSUFFICIENT_DISK_SPACE",
                                "message": (
                                    "Not enough free disk space is available for a new production."
                                ),
                            },
                            "storage": storage,
                        },
                    )
                    return
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": {
                            "code": "PRODUCTION_NOT_READY",
                            "message": "Required local production capabilities are not ready.",
                        },
                        "readiness": {
                            "ready": False,
                            "media_source": readiness["media_source"],
                            "failed_checks": readiness["failed_checks"],
                        },
                    },
                )
                return
            try:
                job = self.server.manager.create(request)
            except RuntimeError as exc:
                if str(exc) == "INSUFFICIENT_DISK_SPACE":
                    self._error(
                        HTTPStatus.INSUFFICIENT_STORAGE,
                        "INSUFFICIENT_DISK_SPACE",
                        "Not enough free disk space is available for a new production.",
                    )
                    return
                if str(exc) == "TELLA_WEB_MIN_FREE_BYTES_INVALID":
                    self._error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "STORAGE_CONFIGURATION_INVALID",
                        "The server storage policy is invalid.",
                    )
                    return
                if str(exc) != "JOB_MANAGER_CLOSING":
                    raise
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "JOB_MANAGER_CLOSING",
                    "The production worker is shutting down.",
                )
                return
            self._json(HTTPStatus.ACCEPTED, job.model_dump(mode="json"))
            return
        match = _JOB_ROUTE.fullmatch(path)
        if match and match.group("action") == "retry":
            self._discard_body()
            job_id = match.group("job_id")
            try:
                original = self.server.manager.get(job_id)
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job not found.")
                return
            if original.status.value not in {"failed", "cancelled"}:
                self._error(
                    HTTPStatus.CONFLICT,
                    "JOB_NOT_RETRYABLE",
                    "Only failed or cancelled jobs can be retried.",
                )
                return
            readiness = web_readiness(
                self.server.manager.output_root,
                original.request,
                storage_summary=self.server.manager.storage_summary(),
            )
            storage = readiness.get("storage")
            if isinstance(storage, dict) and storage.get("configuration_valid") is False:
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "STORAGE_CONFIGURATION_INVALID",
                    "The server storage policy is invalid.",
                )
                return
            if isinstance(storage, dict) and storage.get("submissions_allowed") is False:
                self._error(
                    HTTPStatus.INSUFFICIENT_STORAGE,
                    "INSUFFICIENT_DISK_SPACE",
                    "Not enough free disk space is available for a retry.",
                )
                return
            if not readiness["ready"]:
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "PRODUCTION_NOT_READY",
                    "Required local production capabilities are not ready.",
                )
                return
            try:
                retried = self.server.manager.retry(job_id)
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job not found.")
            except ValueError:
                self._error(
                    HTTPStatus.CONFLICT,
                    "JOB_NOT_RETRYABLE",
                    "The job cannot be retried safely.",
                )
            except RuntimeError as exc:
                code = str(exc)
                if code == "INSUFFICIENT_DISK_SPACE":
                    self._error(
                        HTTPStatus.INSUFFICIENT_STORAGE,
                        code,
                        "Not enough free disk space is available for a retry.",
                    )
                else:
                    self._error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "RETRY_NOT_ADMITTED",
                        "The retry could not be admitted safely.",
                    )
            else:
                self._json(HTTPStatus.ACCEPTED, retried.model_dump(mode="json"))
            return
        if match and match.group("action") == "compact":
            payload = self._read_json()
            if payload is None:
                return
            if set(payload) != {"confirm"} or payload["confirm"] is not True:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "COMPACTION_CONFIRMATION_REQUIRED",
                    "Explicit compaction confirmation is required.",
                )
                return
            try:
                result = self.server.manager.compact(match.group("job_id"))
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job not found.")
            except (OSError, ValueError) as exc:
                code = str(exc)
                if code == "ACTIVE_JOB_STORAGE_LOCKED":
                    self._error(
                        HTTPStatus.CONFLICT,
                        code,
                        "Queued and running jobs cannot be compacted.",
                    )
                elif code == "STORAGE_ARTIFACT_INVALID":
                    self._error(
                        HTTPStatus.CONFLICT,
                        code,
                        "The published artifact is unavailable; compaction was not applied.",
                    )
                else:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "STORAGE_PATH_UNSAFE",
                        "Job storage could not be compacted safely.",
                    )
            else:
                self._json(HTTPStatus.OK, result.model_dump(mode="json"))
            return
        if match and match.group("action") == "cancel":
            self._discard_body()
            try:
                job = self.server.manager.cancel(match.group("job_id"))
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job not found.")
            except ValueError:
                self._error(
                    HTTPStatus.CONFLICT,
                    "JOB_NOT_CANCELLABLE",
                    "Only a queued job can be cancelled.",
                )
            else:
                self._json(HTTPStatus.OK, job.model_dump(mode="json"))
            return
        self._discard_body()
        self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "API route not found.")

    def _get_job(self, job_id: str, action: str | None) -> None:
        if action in {"preview", "download"}:
            self._serve_artifact(job_id, download=action == "download")
            return
        if action is not None:
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "API route not found.")
            return
        try:
            job = self.server.manager.get(job_id)
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job not found.")
            return
        self._json(HTTPStatus.OK, job.model_dump(mode="json"))

    def _serve_artifact(self, job_id: str, *, download: bool) -> None:
        try:
            artifact = self.server.manager.artifact_path(job_id)
            size = artifact.stat().st_size
        except (KeyError, OSError):
            self._error(HTTPStatus.NOT_FOUND, "ARTIFACT_NOT_FOUND", "Artifact not available.")
            return
        disposition = f'attachment; filename="{job_id}.mp4"' if download else None
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match is None or (not match.group(1) and not match.group(2)):
                self._headers(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    "text/plain; charset=utf-8",
                    0,
                    extra={"Content-Range": f"bytes */{size}"},
                )
                return
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                suffix = int(match.group(2))
                start = max(0, size - suffix)
            if start >= size or end < start:
                self._headers(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    "text/plain; charset=utf-8",
                    0,
                    extra={"Content-Range": f"bytes */{size}"},
                )
                return
            end = min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        extra = {"Accept-Ranges": "bytes"}
        if status is HTTPStatus.PARTIAL_CONTENT:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._headers(status, "video/mp4", length, disposition=disposition, extra=extra)
        if self.command != "HEAD":
            with artifact.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining and (chunk := handle.read(min(1024 * 1024, remaining))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.removeprefix("/")
        if any(part.startswith(".") for part in Path(relative).parts):
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "File not found.")
            return
        candidate = (self.server.static_root / relative).resolve()
        try:
            candidate.relative_to(self.server.static_root)
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "File not found.")
            return
        if not candidate.is_file():
            candidate = self.server.static_root / "index.html"
        if not candidate.is_file():
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "File not found.")
            return
        body = candidate.read_bytes()
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in {"application/javascript"}:
            media_type += "; charset=utf-8"
        self._headers(HTTPStatus.OK, media_type, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _unsupported(self) -> None:
        self._discard_body()
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "METHOD_NOT_ALLOWED", "Method not allowed.")

    do_DELETE = _unsupported
    do_PATCH = _unsupported
    do_PUT = _unsupported


def create_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    output_root: Path | None = None,
    manager: JobManager | None = None,
) -> ProductionWebServer:
    root = output_root or Path(os.environ.get("TELLA_WEB_OUTPUT_DIR", "out/web_jobs"))
    owned_manager = manager or JobManager(root)
    return ProductionWebServer((host, port), owned_manager)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Tella production web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.host, args.port, output_root=args.output_root)
    logging.info("Tella production web UI: http://%s:%d", *server.server_address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ProductionWebServer", "create_server", "main"]
