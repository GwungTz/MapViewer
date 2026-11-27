"""Loopback-only HTTP server for generated report directories.

Requirements implemented here:

* bind to 127.0.0.1 on a system-assigned ephemeral port;
* serve only files inside the report directory (no traversal, no listings);
* return ``.jsonl.gz`` shards as plain gzip bytes *without* Content-Encoding,
  so the browser worker decompresses them exactly once;
* provide ETag / Last-Modified / Cache-Control for cheap revalidation.

The server never performs outbound requests and exposes no upload endpoint.
"""

from __future__ import annotations

import email.utils
import logging
import posixpath
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .errors import MapViewError

LOGGER = logging.getLogger("mapview.report_server")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".gz": "application/gzip",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}

_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


def _content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _make_handler(root: Path):
    class ReportRequestHandler(BaseHTTPRequestHandler):
        server_version = "MapViewReport/1.0"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._serve(send_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(send_body=False)

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _resolve(self) -> Path | None:
            split = urlsplit(self.path)
            raw_path = split.path
            if "\x00" in raw_path:
                return None
            decoded = unquote(raw_path)
            if decoded in ("", "/"):
                decoded = "/index.html"
            decoded = decoded.replace("\\", "/").lstrip("/")
            if _DRIVE_RE.match(decoded):
                return None
            normalized = posixpath.normpath(decoded)
            if normalized.startswith("..") or normalized in (".", ""):
                return None
            candidate = (root / normalized).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return None
            return candidate

        def _serve(self, *, send_body: bool) -> None:
            candidate = self._resolve()
            if candidate is None:
                self._send_error_text(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            if not candidate.is_file():
                self._send_error_text(HTTPStatus.NOT_FOUND, "Not found")
                return

            try:
                stat = candidate.stat()
            except OSError:
                self._send_error_text(HTTPStatus.NOT_FOUND, "Not found")
                return

            etag = f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self._common_headers(etag, stat.st_mtime)
                self.end_headers()
                return

            content_type = _content_type(candidate)
            if not send_body:
                self.send_response(HTTPStatus.OK)
                self._common_headers(etag, stat.st_mtime)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(stat.st_size))
                self.end_headers()
                return

            try:
                stream = candidate.open("rb")
            except OSError:
                self._send_error_text(HTTPStatus.NOT_FOUND, "Not found")
                return

            with stream:
                self.send_response(HTTPStatus.OK)
                self._common_headers(etag, stat.st_mtime)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(stat.st_size))
                self.end_headers()
                try:
                    while True:
                        block = stream.read(64 * 1024)
                        if not block:
                            break
                        self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _common_headers(self, etag: str, mtime: float) -> None:
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", email.utils.formatdate(mtime, usegmt=True))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")

        def _send_error_text(self, status: HTTPStatus, message: str) -> None:
            body = (message + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt: str, *args) -> None:
            LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    return ReportRequestHandler


class ReportServer:
    """A local HTTP server bound to ``127.0.0.1`` serving one report directory."""

    def __init__(
        self,
        report_dir: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        logger: logging.Logger | None = None,
    ) -> None:
        if host != "127.0.0.1":
            raise MapViewError("ReportServer may only bind to 127.0.0.1")
        self.root = Path(report_dir).resolve()
        if not (self.root / "index.html").is_file() or not (self.root / "manifest.json").is_file():
            raise MapViewError(
                f"'{self.root}' is not a Map View report directory "
                "(index.html and manifest.json are required)."
            )
        self.host = host
        self.requested_port = port
        self._logger = logger or LOGGER
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            return 0
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> str:
        if self._httpd is not None:
            return self.url
        handler = _make_handler(self.root)
        try:
            httpd = ThreadingHTTPServer((self.host, self.requested_port), handler)
        except OSError as exc:
            raise MapViewError(f"Cannot start local report server on {self.host}: {exc}") from exc
        httpd.daemon_threads = True
        httpd.allow_reuse_address = True
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name="mapview-report-server",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Report server listening on %s (root=%s)", self.url, self.root)
        return self.url

    def stop(self) -> None:
        if self._httpd is None:
            return
        httpd = self._httpd
        self._httpd = None
        try:
            httpd.shutdown()
        finally:
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._logger.info("Report server stopped")

    def __enter__(self) -> "ReportServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


__all__ = ["ReportServer"]
