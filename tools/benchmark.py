"""Benchmark the full parse -> transform -> shard -> verify pipeline.

Examples::

    python tools/benchmark.py --scenario small
    python tools/benchmark.py --scenario medium --verify full --json
    python tools/benchmark.py --sections 1000000 --objects 40000
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapcore.export.report_writer import generate_report  # noqa: E402
from tools.generate_synthetic_map import generate_gnu_map  # noqa: E402

SCENARIOS = {
    "small": (50_000, 2_000),
    "medium": (200_000, 8_000),
    "large": (1_000_000, 40_000),
}


def directory_size(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def run_benchmark(
    sections: int,
    objects: int,
    *,
    work_dir: Path,
    verify: str,
    keep: bool,
) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    map_path = work_dir / f"synthetic_{sections}.map"
    report_path = work_dir / f"report_{sections}.mapview"

    if not map_path.is_file():
        print(f"Generating synthetic map: {map_path}", file=sys.stderr)
        generate_gnu_map(sections, objects, map_path)
    else:
        print(f"Reusing synthetic map: {map_path}", file=sys.stderr)
    map_bytes = map_path.stat().st_size

    result = generate_report(map_path, report_path, verify=verify)
    report_bytes = directory_size(report_path)
    stats = result.stats
    manifest = result.manifest

    metrics = {
        "sections": sections,
        "objects": objects,
        "verify": verify,
        "map_bytes": map_bytes,
        "report_bytes": report_bytes,
        "gzip_bytes": stats.get("compressed_bytes"),
        "uncompressed_bytes": stats.get("uncompressed_bytes"),
        "records": stats.get("records"),
        "chunks": stats.get("chunks"),
        "region_nodes": stats.get("region_nodes"),
        "module_nodes": stats.get("module_nodes"),
        "region_root_compressed_bytes": manifest["views"]["region"]["root_compressed_bytes"],
        "module_root_compressed_bytes": manifest["views"]["module"]["root_compressed_bytes"],
        "timings_ms": result.timings_ms,
        "root_ratio": round(
            manifest["views"]["region"]["root_compressed_bytes"] / max(1, stats.get("compressed_bytes", 1)),
            4,
        ),
    }

    if not keep:
        shutil.rmtree(report_path, ignore_errors=True)
        map_path.unlink(missing_ok=True)
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS))
    parser.add_argument("--sections", type=int)
    parser.add_argument("--objects", type=int)
    parser.add_argument("--work-dir", default="build/perf")
    parser.add_argument("--verify", choices=("none", "basic", "full"), default="basic")
    parser.add_argument("--keep", action="store_true", help="keep the generated map and report")
    parser.add_argument("--json", action="store_true", help="print a JSON object")
    args = parser.parse_args(argv)

    if args.scenario:
        sections, objects = SCENARIOS[args.scenario]
    else:
        sections = args.sections or 50_000
        objects = args.objects or 2_000

    metrics = run_benchmark(
        sections,
        objects,
        work_dir=Path(args.work_dir),
        verify=args.verify,
        keep=args.keep,
    )

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(
            f"Nodes           : {metrics['region_nodes']} region / {metrics['module_nodes']} module"
        )
        print(
            f"MAP / report    : {metrics['map_bytes'] / 1024 / 1024:.1f} MiB / "
            f"{metrics['report_bytes'] / 1024 / 1024:.1f} MiB"
        )
        print(
            f"Shards          : {metrics['chunks']} chunks, {metrics['records']} records, "
            f"{metrics['gzip_bytes'] / 1024 / 1024:.1f} MiB gzip"
        )
        print(
            f"Root chunks     : region {metrics['region_root_compressed_bytes'] / 1024:.1f} KiB, "
            f"module {metrics['module_root_compressed_bytes'] / 1024:.1f} KiB"
        )
        timings = metrics["timings_ms"]
        print(
            f"Timings (ms)    : parse {timings.get('parse')} / transform {timings.get('transform')} / "
            f"write {timings.get('write')} / verify {timings.get('verify')} / total {timings.get('total')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
