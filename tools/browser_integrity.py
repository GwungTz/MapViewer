"""Check the browser's corrupted-shard error path with a real report copy."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapcore.report_server import ReportServer  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "browser_bench" / "browser_integrity.js"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir")
    parser.add_argument(
        "--chunk",
        default="data/region/chunks/rchunk00001.jsonl.gz",
        help="shard to corrupt (relative to the report root)",
    )
    args = parser.parse_args(argv)

    node = shutil.which("node")
    if not node or not (SCRIPT.parent / "node_modules" / "playwright-core").is_dir():
        print("node + playwright-core are required", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="mapview-corrupt-") as temp:
        copy = Path(temp) / "report.mapview"
        shutil.copytree(args.report_dir, copy)
        target = copy / args.chunk
        if not target.is_file():
            print(f"shard not found: {target}", file=sys.stderr)
            return 2
        data = bytearray(target.read_bytes())
        if len(data) < 8:
            print("shard is unexpectedly small", file=sys.stderr)
            return 2
        data[len(data) // 2] ^= 0xFF
        target.write_bytes(bytes(data))

        server = ReportServer(copy)
        url = server.start()
        try:
            process = subprocess.run(
                [node, str(SCRIPT), url],
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
