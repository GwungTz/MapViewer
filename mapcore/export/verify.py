"""Report package verification.

``basic``  - manifest, file hashes/sizes, gzip + JSONL validity, record counts.
``full``   - everything above plus the parent/child graph, size aggregates,
             chunk references and search-index path resolution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import ChunkIntegrityError, MapViewError
from .chunk_builder import check_page_integrity
from .jsonl_writer import iter_jsonl_gz, read_json_gz
from .manifest import validate_manifest

_REQUIRED_NODE_FIELDS = (
    "id",
    "parent_id",
    "view",
    "kind",
    "name",
    "size",
    "category",
    "address",
    "object_file",
    "child_count",
    "children_chunk",
)


def _load_manifest(report_dir: Path) -> dict[str, Any]:
    manifest_path = report_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ChunkIntegrityError("manifest.json", f"cannot read manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ChunkIntegrityError("manifest.json", f"invalid JSON: {exc}") from exc
    validate_manifest(manifest)
    return manifest


def _check_file(report_dir: Path, path: str, sha256: str, compressed_bytes: int | None) -> None:
    file_path = report_dir / path
    if not file_path.is_file():
        raise ChunkIntegrityError(path, "file is missing")
    if compressed_bytes is not None and file_path.stat().st_size != compressed_bytes:
        raise ChunkIntegrityError(
            path,
            f"size mismatch: expected {compressed_bytes}, got {file_path.stat().st_size}",
        )
    import hashlib

    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise ChunkIntegrityError(path, "sha256 mismatch")


def verify_report(report_dir: str | Path, level: str = "basic") -> dict[str, Any]:
    root = Path(report_dir)
    if level not in ("none", "basic", "full"):
        raise MapViewError(f"unknown verification level {level!r}")
    manifest = _load_manifest(root)
    if level == "none":
        return {"level": level, "chunks": 0, "records": 0}

    views = manifest["views"]
    for view_name in ("region", "module"):
        entry = views[view_name]
        _check_file(
            root,
            entry["root_chunk"],
            entry["root_sha256"],
            entry.get("root_compressed_bytes"),
        )
    _check_file(root, manifest["tree_index"], manifest["tree_index_sha256"], None)
    _check_file(root, manifest["search_index"], manifest["search_index_sha256"], None)

    index = read_json_gz(root / manifest["tree_index"])
    if not isinstance(index, dict) or index.get("schema_version") != manifest["schema_version"]:
        raise ChunkIntegrityError(manifest["tree_index"], "invalid tree index schema")
    chunks = index.get("chunks")
    if not isinstance(chunks, dict):
        raise ChunkIntegrityError(manifest["tree_index"], "tree index has no chunks table")

    for key, entry in chunks.items():
        pages = entry.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ChunkIntegrityError(str(key), "chunk has no pages")
        total = 0
        for page in pages:
            check_page_integrity(root, _page_meta(page, str(key)))
            total += int(page.get("records", 0))
        if total != entry.get("records"):
            raise ChunkIntegrityError(str(key), "chunk record count does not match pages")

    if level == "basic":
        records = 0
        for entry in chunks.values():
            for page in entry["pages"]:
                path = root / page["path"]
                count = 0
                expected = int(page.get("records", 0))
                for _line_no, _record in iter_jsonl_gz(path):
                    count += 1
                if count != expected:
                    raise ChunkIntegrityError(path.name, f"expected {expected} records, got {count}")
                records += count
        return {
            "level": level,
            "chunks": len(chunks),
            "records": records,
            "views": {
                name: views[name].get("node_count") for name in ("region", "module")
            },
        }

    # -- full graph verification -----------------------------------------
    ids: set[str] = set()
    internal: dict[str, tuple[int, str]] = {}
    child_sums: dict[str, int] = {}
    expected_chunk_parents: dict[str, set[str]] = {}
    actual_chunk_parents: dict[str, set[str]] = {}
    view_record_counts = {"region": 0, "module": 0}
    view_root_sizes: dict[str, int] = {}
    records = 0

    def consume(record: dict[str, Any], chunk_key: str | None) -> None:
        nonlocal records
        records += 1
        for field in _REQUIRED_NODE_FIELDS:
            if field not in record:
                raise ChunkIntegrityError(chunk_key or "record", f"node is missing '{field}'")
        node_id = record["id"]
        if not isinstance(node_id, str) or not node_id:
            raise ChunkIntegrityError(chunk_key or "record", "node id must be a non-empty string")
        if node_id in ids:
            raise ChunkIntegrityError(node_id, "duplicate node id")
        view = record["view"]
        if view not in view_record_counts:
            raise ChunkIntegrityError(node_id, f"unknown view {view!r}")
        view_record_counts[view] += 1
        size = record["size"]
        if not isinstance(size, int) or size < 0:
            raise ChunkIntegrityError(node_id, f"invalid size {size!r}")
        child_count = record["child_count"]
        if not isinstance(child_count, int) or child_count < 0:
            raise ChunkIntegrityError(node_id, f"invalid child_count {child_count!r}")
        address = record["address"]
        if address is not None and (not isinstance(address, int) or address < 0):
            raise ChunkIntegrityError(node_id, f"invalid address {address!r}")
        category = record["category"]
        if category is not None and category not in (
            "code",
            "rodata",
            "rwdata",
            "zidata",
            "pad",
        ):
            raise ChunkIntegrityError(node_id, f"invalid category {category!r}")

        parent_id = record["parent_id"]
        if parent_id is not None:
            if parent_id not in ids:
                raise ChunkIntegrityError(node_id, f"parent '{parent_id}' has not been written")
            child_sums[parent_id] = child_sums.get(parent_id, 0) + size
            if chunk_key is not None:
                actual_chunk_parents.setdefault(chunk_key, set()).add(parent_id)
        elif record["kind"] != "root":
            raise ChunkIntegrityError(node_id, "only root nodes may have a null parent_id")

        if record["kind"] == "root":
            if view in view_root_sizes:
                raise ChunkIntegrityError(node_id, "duplicate root node for view")
            view_root_sizes[view] = size
            if parent_id is not None:
                raise ChunkIntegrityError(node_id, "root node has a parent")

        children_chunk = record["children_chunk"]
        if children_chunk is not None:
            if not isinstance(children_chunk, str):
                raise ChunkIntegrityError(node_id, "children_chunk must be a string or null")
            if children_chunk not in chunks:
                raise ChunkIntegrityError(node_id, f"unknown children chunk '{children_chunk}'")
            expected_chunk_parents.setdefault(children_chunk, set()).add(node_id)

        if child_count > 0:
            if record["kind"] == "module":
                parts = record.get("parts")
                if not isinstance(parts, dict):
                    raise ChunkIntegrityError(node_id, "module node is missing parts")
                if sum(int(value) for value in parts.values()) != size:
                    raise ChunkIntegrityError(node_id, "module size does not equal its parts")
            internal[node_id] = (size, record["kind"])
        else:
            if children_chunk is not None:
                raise ChunkIntegrityError(node_id, "leaf node has a children chunk")

        ids.add(node_id)

    # Roots first (chunks are written root-first), then children chunks.
    ordered: list[tuple[str | None, Path]] = []
    for view_name in ("region", "module"):
        entry = views[view_name]
        ordered.append((None, root / entry["root_chunk"]))
    for key, entry in chunks.items():
        if entry.get("kind") == "root":
            continue
        for page in entry["pages"]:
            ordered.append((str(key), root / page["path"]))

    for chunk_key, path in ordered:
        count = 0
        expected = None
        if chunk_key is not None:
            expected = next(
                (
                    int(page.get("records", 0))
                    for page in chunks[chunk_key]["pages"]
                    if page.get("path") == path.relative_to(root).as_posix()
                ),
                None,
            )
        for _line_no, record in iter_jsonl_gz(path):
            consume(record, chunk_key)
            count += 1
        if expected is not None and count != expected:
            raise ChunkIntegrityError(path.name, f"expected {expected} records, got {count}")

    for chunk_key, parents in expected_chunk_parents.items():
        actual = actual_chunk_parents.get(chunk_key, set())
        if not parents <= actual:
            missing = sorted(parents - actual)[:5]
            raise ChunkIntegrityError(
                chunk_key, f"chunk is missing children for parent(s): {', '.join(missing)}"
            )

    for node_id, (size, kind) in internal.items():
        if kind == "module":
            continue
        actual = child_sums.get(node_id, 0)
        if actual != size:
            raise ChunkIntegrityError(
                node_id, f"size aggregate mismatch: expected {size}, children sum to {actual}"
            )
    for node_id in child_sums:
        if node_id not in ids:
            raise ChunkIntegrityError(node_id, "record points at an unknown parent")
        if node_id not in internal:
            raise ChunkIntegrityError(node_id, "leaf node is referenced as a parent")

    if view_record_counts["region"] != views["region"].get("node_count"):
        raise ChunkIntegrityError("region", "manifest node_count does not match records")
    if view_record_counts["module"] != views["module"].get("node_count"):
        raise ChunkIntegrityError("module", "manifest node_count does not match records")

    for view_name, root_id in (
        ("region", views["region"]["root_id"]),
        ("module", views["module"]["root_id"]),
    ):
        expected_total = views[view_name]["total_bytes"]
        if view_root_sizes.get(view_name) != expected_total:
            raise ChunkIntegrityError(view_name, "root size does not match manifest total_bytes")
        if child_sums.get(root_id, 0) != expected_total:
            raise ChunkIntegrityError(view_name, "children do not sum to the view total")

    # Search index: every hit must resolve to a loaded node and a valid path.
    search_path = root / manifest["search_index"]
    search_records = 0
    for _line_no, record in iter_jsonl_gz(search_path):
        search_records += 1
        node_id = record.get("id")
        path_ids = record.get("path_ids")
        if not isinstance(node_id, str) or node_id not in ids:
            raise ChunkIntegrityError("search-index", f"unknown node id {node_id!r}")
        if not isinstance(path_ids, list) or len(path_ids) < 2:
            raise ChunkIntegrityError("search-index", f"invalid path for {node_id}")
        if path_ids[-1] != node_id:
            raise ChunkIntegrityError("search-index", f"path does not end at {node_id}")
        if path_ids[0] != views[record.get("view", "region")]["root_id"]:
            raise ChunkIntegrityError("search-index", f"path does not start at the view root")
        for path_id in path_ids:
            if path_id not in ids:
                raise ChunkIntegrityError(
                    "search-index", f"path for {node_id} contains unknown id {path_id!r}"
                )
    if search_records != manifest.get("search_index_records", search_records):
        raise ChunkIntegrityError("search-index", "record count does not match manifest")

    return {
        "level": level,
        "chunks": len(chunks),
        "records": records,
        "search_records": search_records,
        "views": view_record_counts,
    }


def _page_meta(page: dict[str, Any], chunk_key: str):
    from .jsonl_writer import PageMeta

    try:
        return PageMeta(
            path=str(page["path"]),
            records=int(page["records"]),
            uncompressed_bytes=int(page["uncompressed_bytes"]),
            compressed_bytes=int(page["compressed_bytes"]),
            sha256=str(page["sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ChunkIntegrityError(chunk_key, f"invalid page metadata: {exc}") from exc
