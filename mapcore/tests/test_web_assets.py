"""Static checks for the localized web assets and copied report assets."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mapcore.export.report_writer import generate_report
from mapcore.resources import ASSET_MAP, asset_paths, web_dir
from mapcore.tests.helpers import fixture


class WebAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = web_dir()
        cls.paths = asset_paths()
        cls.texts = {
            relative: (cls.root / relative).read_text(encoding="utf-8")
            for relative in ASSET_MAP
        }

    def test_required_assets(self) -> None:
        for relative in ASSET_MAP:
            self.assertTrue((self.root / relative).is_file(), relative)

    def test_no_external_resources(self) -> None:
        for relative, text in self.texts.items():
            # XML namespaces are identifiers, not fetched resources.
            scan = text.replace('xmlns="http://www.w3.org/2000/svg"', 'xmlns="svg"')
            for needle in ("http://", "https://", "//cdn.", "unpkg.com"):
                self.assertNotIn(needle, scan, f"{relative} references {needle}")

    def test_no_vscode_api(self) -> None:
        for relative, text in self.texts.items():
            self.assertNotIn("acquireVsCodeApi", text, relative)
            self.assertNotIn("postMessage({ type: 'configMemory'", text, relative)
            self.assertNotIn("var(--vscode-", text, relative)

    def test_no_innerhtml_for_map_text(self) -> None:
        for relative, text in self.texts.items():
            self.assertNotIn(".innerHTML", text, relative)
            self.assertNotIn("insertAdjacentHTML", text, relative)

    def test_hidden_attribute_beats_flex_layout(self) -> None:
        css = self.texts["css/style.css"]
        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)

    def test_csp_is_strict_and_local(self) -> None:
        html = self.texts["index.html"]
        self.assertIn("default-src 'self'", html)
        self.assertIn("script-src 'self'", html)
        self.assertIn("worker-src 'self'", html)
        self.assertIn("connect-src 'self'", html)

    def test_scripts_are_external_files(self) -> None:
        html = self.texts["index.html"]
        self.assertIn('<script src="assets/treemap.js" defer></script>', html)
        self.assertIn('<script src="assets/app.js" defer></script>', html)
        self.assertNotIn("<script>", html)

    def test_worker_uses_relative_url(self) -> None:
        app = self.texts["js/app.js"]
        self.assertIn("assets/data-worker.js", app)
        self.assertIn("new Worker(new URL(", app)

    def test_gzip_stream_support(self) -> None:
        worker = self.texts["js/data-worker.js"]
        self.assertIn("DecompressionStream('gzip')", worker)
        self.assertIn("TextDecoderStream", worker)
        self.assertIn("CHUNK_INTEGRITY_FAILED", worker)

    def test_safe_tooltip_construction(self) -> None:
        app = self.texts["js/app.js"]
        self.assertIn("textContent", app)
        self.assertNotIn(".innerHTML", app)

    def test_js_syntax_if_node_available(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        for relative in ("js/app.js", "js/treemap.js", "js/data-worker.js"):
            process = subprocess.run(
                [node, "--check", str(self.root / relative)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_report_copies_assets_verbatim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-assets-") as temp:
            report = Path(temp) / "report.mapview"
            generate_report(fixture("keil_ac5.map"), report, verify="none")
            for source_rel, destination_rel in ASSET_MAP.items():
                self.assertEqual(
                    (self.root / source_rel).read_bytes(),
                    (report / destination_rel).read_bytes(),
                    destination_rel,
                )


if __name__ == "__main__":
    unittest.main()
