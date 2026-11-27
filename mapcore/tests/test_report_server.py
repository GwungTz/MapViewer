"""Loopback report server security and HTTP semantics tests."""

from __future__ import annotations

import gzip
import http.client
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from mapcore.errors import MapViewError
from mapcore.export.report_writer import generate_report
from mapcore.report_server import ReportServer
from mapcore.tests.helpers import fixture


class ReportServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="mapview-server-")
        cls.report = Path(cls.temp.name) / "report.mapview"
        generate_report(fixture("keil_ac5.map"), cls.report, verify="basic")
        cls.server = ReportServer(cls.report)
        cls.url = cls.server.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        cls.temp.cleanup()

    def raw_get(self, path: str, method: str = "GET", headers: dict[str, str] | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_binds_loopback_ephemeral_port(self) -> None:
        self.assertTrue(self.url.startswith("http://127.0.0.1:"))
        self.assertGreater(self.server.port, 0)

    def test_index_and_manifest(self) -> None:
        status, headers, body = self.raw_get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Map View", body)
        self.assertIn("ETag", headers)
        self.assertIn("Last-Modified", headers)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

        status, headers, body = self.raw_get("/manifest.json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        manifest = json.loads(body)
        self.assertEqual(manifest["schema_version"], 1)

    def test_javascript_and_css(self) -> None:
        status, headers, _body = self.raw_get("/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        status, headers, _body = self.raw_get("/assets/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])

    def test_gzip_shard_is_not_double_encoded(self) -> None:
        status, headers, body = self.raw_get("/data/region/root.jsonl.gz")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/gzip")
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body[:2], b"\x1f\x8b")
        decoded = gzip.decompress(body).decode("utf-8")
        self.assertIn('"id":"rroot"', decoded)

    def test_etag_revalidation(self) -> None:
        status, headers, _body = self.raw_get("/manifest.json")
        etag = headers["ETag"]
        status, headers, body = self.raw_get("/manifest.json", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_head_request(self) -> None:
        status, headers, body = self.raw_get("/assets/app.js", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_directory_listing_disabled(self) -> None:
        status, _headers, _body = self.raw_get("/data/")
        self.assertEqual(status, 404)
        status, _headers, _body = self.raw_get("/data/region/")
        self.assertEqual(status, 404)

    def test_traversal_blocked(self) -> None:
        for path in (
            "/../manifest.json",
            "/%2e%2e/manifest.json",
            "/..%5cmanifest.json",
            "/data/../../manifest.json",
            "/C:/Windows/win.ini",
        ):
            status, _headers, _body = self.raw_get(path)
            self.assertIn(status, (403, 404), f"path {path} returned {status}")

    def test_post_not_allowed(self) -> None:
        status, headers, _body = self.raw_get("/manifest.json", method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_missing_file(self) -> None:
        status, _headers, _body = self.raw_get("/nope.json")
        self.assertEqual(status, 404)

    def test_query_string_ignored(self) -> None:
        status, _headers, body = self.raw_get("/manifest.json?v=3")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["schema_version"], 1)

    def test_urllib_smoke(self) -> None:
        with urllib.request.urlopen(self.url + "manifest.json") as response:
            self.assertEqual(response.status, 200)

    def test_invalid_report_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(MapViewError):
                ReportServer(temp)

    def test_loopback_only(self) -> None:
        with self.assertRaises(MapViewError):
            ReportServer(self.report, host="0.0.0.0")

    def test_stop_is_idempotent(self) -> None:
        server = ReportServer(self.report)
        server.start()
        server.stop()
        server.stop()
        self.assertEqual(server.port, 0)


if __name__ == "__main__":
    unittest.main()
