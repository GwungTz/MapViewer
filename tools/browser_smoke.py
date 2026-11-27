"""Functional browser smoke test for a generated report (headless Chrome)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapcore.report_server import ReportServer  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "browser_bench" / "browser_smoke.js"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir")
    parser.add_argument("--query", default="main", help="search query used for navigation")
    args = parser.parse_args(argv)

    node = shutil.which("node")
    if not node:
        print("node is required for the browser smoke test", file=sys.stderr)
        return 2
    if not (SCRIPT.parent / "node_modules" / "playwright-core").is_dir():
        print(
            "playwright-core is missing. Run: cd tools/browser_bench && npm install",
            file=sys.stderr,
        )
        return 2

    server = ReportServer(args.report_dir)
    url = server.start()
    try:
        process = subprocess.run(
            [node, str(SCRIPT), url, args.query],
            cwd=str(SCRIPT.parent),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    finally:
        server.stop()
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
