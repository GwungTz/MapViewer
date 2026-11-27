"""Run the headless browser benchmark against a generated report directory."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapcore.report_server import ReportServer  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "browser_bench" / "browser_bench.js"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir")
    parser.add_argument("--out", help="write the raw metrics JSON here")
    parser.add_argument("--screenshot", help="save a screenshot here")
    args = parser.parse_args(argv)

    node = shutil.which("node")
    if not node:
        print("node is required for the browser benchmark", file=sys.stderr)
        return 2
    if not (SCRIPT.parent / "node_modules" / "playwright-core").is_dir():
        print(
            "playwright-core is missing. Run: cd tools/browser_bench && npm install",
            file=sys.stderr,
        )
        return 2

    server = ReportServer(args.report_dir)
    url = server.start()
    command = [node, str(SCRIPT), url]
    if args.out:
        command.append(str(Path(args.out).resolve()))
    if args.screenshot:
        command.append(str(Path(args.screenshot).resolve()))
    try:
        process = subprocess.run(command, cwd=str(SCRIPT.parent), text=True, timeout=1800)
    finally:
        server.stop()
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
