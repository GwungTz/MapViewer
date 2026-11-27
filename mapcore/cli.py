"""Command line interface: ``python -m mapcore.cli <command>``."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .errors import MapViewError
from .export.report_writer import default_report_dir, generate_report
from .export.verify import verify_report
from .report_server import ReportServer


def _progress_printer(quiet: bool):
    last = {"percent": -1, "time": 0.0}

    def report(percent: int, message: str) -> None:
        if quiet:
            return
        now = time.monotonic()
        if percent >= 100 or now - last["time"] > 0.15 or percent != last["percent"]:
            print(f"[{percent:3d}%] {message}", file=sys.stderr, flush=True)
            last["percent"] = percent
            last["time"] = now

    return report


def _cmd_generate(args: argparse.Namespace) -> int:
    report = _progress_printer(args.quiet)
    result = generate_report(
        args.map_file,
        args.output,
        verify=args.verify,
        progress=report,
    )
    if not args.quiet:
        stats = result.stats
        print(f"Report: {result.report_dir}")
        print(
            f"Nodes: region={stats['region_nodes']} module={stats['module_nodes']} "
            f"chunks={stats['chunks']} records={stats['records']}"
        )
        print(
            f"Size: {stats['compressed_bytes'] / 1024:.1f} KiB gzip / "
            f"{stats['uncompressed_bytes'] / 1024:.1f} KiB raw"
        )
        for key, value in result.timings_ms.items():
            print(f"Time {key}: {value} ms")
        if result.warnings:
            print(f"Warnings: {len(result.warnings)} (first {min(5, len(result.warnings))} shown)")
            for warning in result.warnings[:5]:
                prefix = f"line {warning.line_no}: " if warning.line_no else ""
                print(f"  {prefix}{warning.message}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify_report(args.report_dir, level=args.level)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    server = ReportServer(args.report_dir, port=args.port)
    url = server.start()
    print(url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("Stopping report server.", file=sys.stderr)
    finally:
        server.stop()
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    manifest_path = Path(args.report_dir) / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = {
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "summary": payload.get("summary"),
        "grand_totals": payload.get("grand_totals"),
        "stats": payload.get("stats"),
        "warning_count": payload.get("warning_count"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mapview-cli",
        description="Generate and serve standalone Map View reports from embedded .map files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="parse a .map file and write a report directory")
    generate.add_argument("map_file", help="path to the linker .map file")
    generate.add_argument(
        "-o",
        "--output",
        help="report output directory (default: <map parent>/<map stem>.mapview)",
    )
    generate.add_argument(
        "--verify",
        choices=("none", "basic", "full"),
        default="basic",
        help="integrity verification level (default: basic)",
    )
    generate.add_argument("--quiet", action="store_true", help="suppress progress output")
    generate.set_defaults(func=_cmd_generate)

    verify = sub.add_parser("verify", help="verify an existing report directory")
    verify.add_argument("report_dir")
    verify.add_argument(
        "--level", choices=("none", "basic", "full"), default="full"
    )
    verify.set_defaults(func=_cmd_verify)

    serve = sub.add_parser("serve", help="serve a report directory on 127.0.0.1")
    serve.add_argument("report_dir")
    serve.add_argument("--port", type=int, default=0, help="port (0 = ephemeral)")
    serve.add_argument("--open", action="store_true", help="open the report in the default browser")
    serve.set_defaults(func=_cmd_serve)

    manifest = sub.add_parser("manifest", help="print a report manifest summary")
    manifest.add_argument("report_dir")
    manifest.set_defaults(func=_cmd_manifest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MapViewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
