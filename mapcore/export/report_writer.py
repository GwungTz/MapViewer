"""End-to-end report generation: parse -> transform -> shards -> manifest.

The report is written into a temporary sibling directory and only renamed to
its final name after every shard, index and the manifest have been written and
verified.  Replacing an existing report uses a backup rename so a failure never
leaves a half-written report behind.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..cancel import CancelToken
from ..errors import GenerationCancelled, MapViewError, ReportWriteError
from ..parser import ParseWarning, parse_map_file
from ..resources import ASSET_MAP, asset_paths
from ..transform.summary import build_memory_summary, normalize_grand_totals
from ..transform.tree_builder import build_views
from .chunk_builder import ChunkStore, plan_child_shards, write_single_page
from .jsonl_writer import write_json_gz
from .manifest import build_manifest
from .records import (
    build_module_leaf_groups,
    build_module_root_records,
    build_module_search_records,
    build_region_leaf_groups,
    build_region_object_groups,
    build_region_root_records,
)
from .verify import verify_report

ProgressCallback = Callable[[int, str], None]

# Above this object count the region root chunk stops embedding object summaries;
# objects move into per-region child shards and are loaded on drill-down.
MAX_ROOT_OBJECTS = 50_000


@dataclass(slots=True)
class GenerationResult:
    report_dir: Path
    manifest: dict[str, Any]
    warnings: list[ParseWarning] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)


def default_report_dir(map_path: str | Path) -> Path:
    path = Path(map_path)
    return path.parent / f"{path.stem}.mapview"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ms(start: float, end: float | None = None) -> int:
    return int(((end if end is not None else time.perf_counter()) - start) * 1000)


def _write_manifest(report_dir: Path, manifest: dict[str, Any]) -> None:
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    (report_dir / "manifest.json").write_text(payload + "\n", encoding="utf-8")


def _copy_assets(report_dir: Path) -> None:
    sources = asset_paths()
    for source_rel, dest_rel in ASSET_MAP.items():
        source = sources[source_rel]
        destination = report_dir / dest_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _rename_with_retry(source: Path, destination: Path, *, attempts: int = 6) -> None:
    """Rename a directory, tolerating short-lived Windows file locks."""

    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            source.rename(destination)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 == attempts:
                break
            time.sleep(0.12 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _publish(temp_dir: Path, out_dir: Path) -> None:
    backup: Path | None = None
    if out_dir.exists():
        if not out_dir.is_dir():
            raise ReportWriteError(f"Output path exists and is not a directory: {out_dir}")
        backup = out_dir.with_name(f"{out_dir.name}.bak-{uuid.uuid4().hex[:8]}")
        try:
            _rename_with_retry(out_dir, backup)
        except OSError as exc:
            raise ReportWriteError(
                f"Cannot replace the existing report directory '{out_dir}': {exc}. "
                "Close any browser report or file manager using it and try again."
            ) from exc

    try:
        _rename_with_retry(temp_dir, out_dir)
    except OSError as exc:
        if backup is not None and backup.exists() and not out_dir.exists():
            try:
                _rename_with_retry(backup, out_dir)
            except OSError:
                pass
        raise ReportWriteError(f"Cannot move the new report into place: {exc}") from exc

    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def generate_report(
    map_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    verify: str = "basic",
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    compresslevel: int = 6,
    max_root_objects: int = MAX_ROOT_OBJECTS,
) -> GenerationResult:
    """Generate a self-contained report directory from a ``.map`` file."""

    cancel = cancel or CancelToken()
    last_percent = -1

    def report(percent: int, message: str) -> None:
        nonlocal last_percent
        if percent < last_percent:
            percent = last_percent
        last_percent = percent
        if progress is not None:
            progress(percent, message)

    source_path = Path(map_path)
    destination = Path(out_dir) if out_dir is not None else default_report_dir(source_path)
    started = time.perf_counter()

    report(0, f"Reading {source_path.name}")
    parsed = parse_map_file(source_path)
    cancel.check()
    parse_ms = _ms(started)
    data = parsed.data

    report(35, "Building Region and Module views")
    transform_started = time.perf_counter()
    region_view, module_view = build_views(data)
    summary = build_memory_summary(data)
    grand_totals = normalize_grand_totals(data)
    transform_ms = _ms(transform_started)
    cancel.check()

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not destination.is_dir():
        raise ReportWriteError(f"Output path exists and is not a directory: {destination}")
    temp_dir = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex[:10]}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True)

    try:
        _copy_assets(temp_dir)
        store = ChunkStore(temp_dir, compresslevel=compresslevel, cancel=cancel)
        write_started = time.perf_counter()

        region_groups, search_records = build_region_leaf_groups(region_view)
        region_assignments, region_shards = plan_child_shards(region_groups, "rchunk")

        spill_objects = region_view.object_count > max_root_objects
        object_shards: list[tuple[str, list[dict]]] = []
        region_object_assignments: dict[str, str] = {}
        if spill_objects:
            object_groups = build_region_object_groups(region_view, region_assignments)
            region_object_assignments, object_shards = plan_child_shards(
                object_groups, "robjchunk"
            )

        region_root = store.add_root_chunk(
            "rroot",
            "region",
            "data/region/root.jsonl.gz",
            build_region_root_records(
                region_view,
                region_assignments,
                region_object_assignments,
                include_objects=not spill_objects,
            ),
        )

        if spill_objects:
            for index, (key, records) in enumerate(object_shards, start=1):
                cancel.check()
                store.add_child_chunk(
                    key, "region", f"data/region/chunks/{key}.jsonl.gz", records
                )
                report(
                    40 + int(5 * index / max(len(object_shards), 1)),
                    f"Writing region object shard {index}/{len(object_shards)}",
                )

        total_region_work = max(len(region_shards), 1)
        for index, (key, records) in enumerate(region_shards, start=1):
            cancel.check()
            store.add_child_chunk(
                key, "region", f"data/region/chunks/{key}.jsonl.gz", records
            )
            report(45 + int(15 * index / total_region_work), f"Writing region shard {index}/{len(region_shards)}")

        module_groups = build_module_leaf_groups(module_view)
        module_assignments, module_shards = plan_child_shards(module_groups, "mchunk")

        module_root = store.add_root_chunk(
            "mroot",
            "module",
            "data/module/root.jsonl.gz",
            build_module_root_records(module_view, module_assignments),
        )
        total_module_work = max(len(module_shards), 1)
        for index, (key, records) in enumerate(module_shards, start=1):
            cancel.check()
            store.add_child_chunk(
                key, "module", f"data/module/chunks/{key}.jsonl.gz", records
            )
            report(60 + int(15 * index / total_module_work), f"Writing module shard {index}/{len(module_shards)}")

        search_records.extend(build_module_search_records(module_view))
        report(76, "Writing search index")
        search_meta = write_single_page(
            temp_dir,
            "data/search-index.jsonl.gz",
            search_records,
            compresslevel=compresslevel,
            cancel=cancel,
        )

        report(80, "Writing tree index")
        tree_index_meta = write_json_gz(
            temp_dir, "data/tree-index.json.gz", store.to_index(), compresslevel=compresslevel
        )

        write_ms = _ms(write_started)

        region_node_count = sum(
            entry.records for entry in store.chunks.values() if entry.view == "region"
        )
        module_node_count = sum(
            entry.records for entry in store.chunks.values() if entry.view == "module"
        )
        region_chunk_count = sum(
            1
            for entry in store.chunks.values()
            if entry.view == "region" and entry.kind == "children"
        )
        module_chunk_count = sum(
            1
            for entry in store.chunks.values()
            if entry.view == "module" and entry.kind == "children"
        )

        stats: dict[str, Any] = {
            "source_bytes": parsed.size_bytes,
            "load_regions": len(data.load_regions),
            "execution_regions": data.execution_region_count(),
            "sections": data.section_count(),
            "objects": region_view.object_count,
            "region_leaves": region_view.leaf_count,
            "components": len(data.all_components()),
            "modules": module_view.module_count,
            "module_leaves": module_view.leaf_count,
            "region_nodes": region_node_count,
            "module_nodes": module_node_count,
            "chunks": len(store.chunks),
            "records": store.total_records,
            "compressed_bytes": store.total_compressed_bytes,
            "uncompressed_bytes": store.total_uncompressed_bytes,
            "timings_ms": {
                "parse": parse_ms,
                "transform": transform_ms,
                "write": write_ms,
            },
        }

        source = {
            "file_name": source_path.name,
            "size_bytes": parsed.size_bytes,
            "sha256": parsed.sha256,
            "format": data.format,
            "compiler": data.compiler,
            "compiler_version": data.compiler_version,
            "encoding": parsed.encoding,
        }

        manifest = build_manifest(
            source=source,
            summary=summary,
            grand_totals=grand_totals,
            region_view=region_view,
            module_view=module_view,
            region_root=region_root,
            module_root=module_root,
            region_node_count=region_node_count,
            module_node_count=module_node_count,
            region_chunk_count=region_chunk_count,
            module_chunk_count=module_chunk_count,
            objects_embedded=not spill_objects,
            tree_index=tree_index_meta,
            search_index=search_meta,
            warnings=data.warnings,
            stats=stats,
            generated_at=_now_iso(),
        )

        report(84, "Writing manifest")
        _write_manifest(temp_dir, manifest)

        if verify != "none":
            report(85, f"Verifying report ({verify})")
            verify_started = time.perf_counter()
            verify_report(temp_dir, level=verify)
            verify_ms = _ms(verify_started)
        else:
            verify_ms = 0
        cancel.check()

        timings = {
            "parse": parse_ms,
            "transform": transform_ms,
            "write": write_ms,
            "verify": verify_ms,
            "total": _ms(started),
        }
        manifest["stats"]["timings_ms"] = timings
        _write_manifest(temp_dir, manifest)

        report(98, "Publishing report directory")
        _publish(temp_dir, destination)

        report(100, f"Report ready: {destination}")
        return GenerationResult(
            report_dir=destination,
            manifest=manifest,
            warnings=list(data.warnings),
            stats=manifest["stats"],
            timings_ms=timings,
        )
    except GenerationCancelled:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except MapViewError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as exc:  # pragma: no cover - defensive cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise ReportWriteError(f"Report generation failed: {exc}") from exc


__all__ = [
    "GenerationResult",
    "default_report_dir",
    "generate_report",
    "verify_report",
    "__version__",
]
