"""Manifest (schema_version = 1) construction and validation.

The manifest holds metadata, totals and file references only - never tree
records.  The first screen needs just this file plus the region root chunk.
"""

from __future__ import annotations

from typing import Any

from .. import GENERATOR_NAME, SCHEMA_VERSION, __version__
from ..errors import MapViewError, ReportWriteError
from ..parser.models import GrandTotals, ParseWarning
from ..transform.summary import MemorySummary
from ..transform.tree_builder import ModuleView, RegionView
from .chunk_builder import ChunkEntry
from .jsonl_writer import PageMeta


def _root_page(entry: ChunkEntry) -> PageMeta:
    if not entry.pages:
        raise ReportWriteError(f"chunk '{entry.key}' has no pages")
    return entry.pages[0]


def build_manifest(
    *,
    source: dict[str, Any],
    summary: MemorySummary,
    grand_totals: GrandTotals,
    region_view: RegionView,
    module_view: ModuleView,
    region_root: ChunkEntry,
    module_root: ChunkEntry,
    region_node_count: int,
    module_node_count: int,
    region_chunk_count: int,
    module_chunk_count: int,
    objects_embedded: bool,
    tree_index: PageMeta,
    search_index: PageMeta,
    warnings: list[ParseWarning],
    stats: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    region_root_page = _root_page(region_root)
    module_root_page = _root_page(module_root)

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": __version__},
        "generator_version": __version__,
        "generated_at": generated_at,
        "source": source,
        "summary": summary.to_dict(),
        "grand_totals": {
            "code": grand_totals.code,
            "ro_data": grand_totals.ro_data,
            "rw_data": grand_totals.rw_data,
            "zi_data": grand_totals.zi_data,
            "total_ro": grand_totals.total_ro,
            "total_rw": grand_totals.total_rw,
            "total_rom": grand_totals.total_rom,
        },
        "views": {
            "region": {
                "label": "Region View",
                "root_id": region_view.root_id,
                "root_chunk": region_root_page.path,
                "root_records": region_root_page.records,
                "root_compressed_bytes": region_root_page.compressed_bytes,
                "root_sha256": region_root_page.sha256,
                "total_bytes": region_view.total,
                "padding_bytes": region_view.padding_bytes,
                "node_count": region_node_count,
                "object_count": region_view.object_count,
                "objects_embedded": objects_embedded,
                "section_count": region_view.leaf_count,
                "chunk_count": region_chunk_count,
            },
            "module": {
                "label": "Module View",
                "root_id": module_view.root_id,
                "root_chunk": module_root_page.path,
                "root_records": module_root_page.records,
                "root_compressed_bytes": module_root_page.compressed_bytes,
                "root_sha256": module_root_page.sha256,
                "total_bytes": module_view.total,
                "node_count": module_node_count,
                "module_count": module_view.module_count,
                "section_count": module_view.leaf_count,
                "chunk_count": module_chunk_count,
            },
        },
        "tree_index": tree_index.path,
        "tree_index_sha256": tree_index.sha256,
        "tree_index_compressed_bytes": tree_index.compressed_bytes,
        "search_index": search_index.path,
        "search_index_sha256": search_index.sha256,
        "search_index_compressed_bytes": search_index.compressed_bytes,
        "search_index_records": search_index.records,
        "compression": "gzip",
        "encoding": "utf-8",
        "warnings": [
            {"line": warning.line_no, "message": warning.message}
            for warning in warnings[:200]
        ],
        "warning_count": len(warnings),
        "stats": stats,
    }


def validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise MapViewError("MANIFEST_INVALID: manifest is not a JSON object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise MapViewError(
            f"MANIFEST_INVALID: unsupported schema_version {manifest.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}"
        )
    source = manifest.get("source")
    if not isinstance(source, dict) or not source.get("sha256") or not source.get("file_name"):
        raise MapViewError("MANIFEST_INVALID: source.file_name / source.sha256 missing")
    summary = manifest.get("summary")
    if not isinstance(summary, dict):
        raise MapViewError("MANIFEST_INVALID: summary missing")
    for key in ("rom", "ram"):
        if not isinstance(summary.get(key), dict) or "used" not in summary[key]:
            raise MapViewError(f"MANIFEST_INVALID: summary.{key} missing")
    views = manifest.get("views")
    if not isinstance(views, dict):
        raise MapViewError("MANIFEST_INVALID: views missing")
    for view in ("region", "module"):
        entry = views.get(view)
        if not isinstance(entry, dict):
            raise MapViewError(f"MANIFEST_INVALID: views.{view} missing")
        for field in ("root_id", "root_chunk", "root_sha256", "root_records", "total_bytes"):
            if field not in entry:
                raise MapViewError(f"MANIFEST_INVALID: views.{view}.{field} missing")
    for field in ("tree_index", "search_index", "compression", "encoding"):
        if field not in manifest:
            raise MapViewError(f"MANIFEST_INVALID: {field} missing")
    if manifest["compression"] != "gzip":
        raise MapViewError("MANIFEST_INVALID: only gzip compression is supported")
