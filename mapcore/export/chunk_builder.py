"""Shard planner and gzip JSONL chunk store.

A *chunk* is addressed by a stable key (e.g. an object node ID).  A chunk may
contain the children of several sibling nodes (sharding keeps the report from
creating tens of thousands of tiny files); the UI filters records by
``parent_id``.  Oversized chunks are split into numbered pages, and the UI
loads all pages of one chunk before rendering.
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import SCHEMA_VERSION, __version__
from ..cancel import CancelToken
from ..errors import ChunkIntegrityError
from .checksum import sha256_file
from .jsonl_writer import PageMeta, dumps_record, write_json_gz

# Initial parameters; tune with tools/benchmark.py on real firmware maps.
MAX_SHARD_RECORDS = 5000
MAX_SHARD_BYTES = 4 * 1024 * 1024
MAX_SHARD_GROUPS = 200


def plan_child_shards(
    groups: Sequence[tuple[str, list[dict[str, Any]]]],
    prefix: str,
    *,
    max_records: int = 20000,
    max_groups: int = MAX_SHARD_GROUPS,
) -> tuple[dict[str, str], list[tuple[str, list[dict[str, Any]]]]]:
    """Group per-parent child records into shared shards.

    Returns ``(parent_id -> chunk_key, [(chunk_key, records), ...])``.  Parents
    with no records are omitted from the assignment map.
    """

    assignments: dict[str, str] = {}
    shards: list[tuple[str, list[dict[str, Any]]]] = []
    current_key: str | None = None
    current_records: list[dict[str, Any]] = []
    current_groups = 0

    def flush() -> None:
        nonlocal current_key, current_records, current_groups
        if current_records and current_key is not None:
            shards.append((current_key, current_records))
        current_key = None
        current_records = []
        current_groups = 0

    for parent_id, records in groups:
        if not records:
            continue
        if current_records and (
            len(current_records) + len(records) > max_records
            or current_groups >= max_groups
        ):
            flush()
        if current_key is None:
            current_key = f"{prefix}{len(shards) + 1:05d}"
        current_records.extend(records)
        current_groups += 1
        assignments[parent_id] = current_key

    flush()
    return assignments, shards


def _page_path(rel_base: str, page_index: int) -> str:
    if page_index == 1:
        return rel_base
    if rel_base.endswith(".jsonl.gz"):
        return f"{rel_base[:-len('.jsonl.gz')]}.p{page_index:03d}.jsonl.gz"
    return f"{rel_base}.p{page_index:03d}"


def _write_encoded_page(
    report_root: Path,
    rel_path: str,
    lines: list[bytes],
    *,
    compresslevel: int,
) -> PageMeta:
    path = report_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(
            fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0
        ) as gz:
            for line in lines:
                gz.write(line)
    return PageMeta(
        path=rel_path,
        records=len(lines),
        uncompressed_bytes=sum(len(line) for line in lines),
        compressed_bytes=path.stat().st_size,
        sha256=sha256_file(path),
    )


def write_single_page(
    report_root: Path,
    rel_path: str,
    records: Iterable[dict[str, Any]],
    *,
    compresslevel: int = 6,
    cancel: CancelToken | None = None,
) -> PageMeta:
    """Stream an arbitrary number of records into one gzip file."""

    path = report_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    uncompressed = 0
    count = 0
    try:
        with path.open("wb") as raw:
            with gzip.GzipFile(
                fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0
            ) as gz:
                for record in records:
                    if cancel is not None and count % 2048 == 0:
                        cancel.check()
                    encoded = (dumps_record(record) + "\n").encode("utf-8")
                    gz.write(encoded)
                    uncompressed += len(encoded)
                    count += 1
    except OSError as exc:
        raise ChunkIntegrityError(rel_path, f"cannot write shard: {exc}") from exc

    return PageMeta(
        path=rel_path,
        records=count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=path.stat().st_size,
        sha256=sha256_file(path),
    )


def write_pages(
    report_root: Path,
    rel_base: str,
    records: Iterable[dict[str, Any]],
    *,
    max_records: int = MAX_SHARD_RECORDS,
    max_bytes: int = MAX_SHARD_BYTES,
    compresslevel: int = 6,
    cancel: CancelToken | None = None,
) -> list[PageMeta]:
    """Write a JSONL stream, splitting into ``.pNNN`` pages when needed."""

    pages: list[PageMeta] = []
    lines: list[bytes] = []
    size = 0

    def flush() -> None:
        nonlocal lines, size
        if not lines:
            return
        pages.append(
            _write_encoded_page(
                report_root,
                _page_path(rel_base, len(pages) + 1),
                lines,
                compresslevel=compresslevel,
            )
        )
        lines = []
        size = 0

    for record in records:
        if cancel is not None:
            cancel.check()
        encoded = (dumps_record(record) + "\n").encode("utf-8")
        if lines and (
            len(lines) >= max_records or size + len(encoded) > max_bytes
        ):
            flush()
        lines.append(encoded)
        size += len(encoded)

    flush()
    return pages


@dataclass(slots=True)
class ChunkEntry:
    key: str
    view: str
    kind: str
    pages: list[PageMeta] = field(default_factory=list)

    @property
    def records(self) -> int:
        return sum(page.records for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": self.view,
            "kind": self.kind,
            "records": self.records,
            "pages": [page.to_dict() for page in self.pages],
        }


class ChunkStore:
    """Owns every gzip shard written into a report directory."""

    def __init__(
        self,
        report_root: Path,
        *,
        compresslevel: int = 6,
        cancel: CancelToken | None = None,
    ):
        self.report_root = report_root
        self.compresslevel = compresslevel
        self.cancel = cancel
        self.chunks: dict[str, ChunkEntry] = {}

    def add_root_chunk(
        self,
        key: str,
        view: str,
        rel_path: str,
        records: Iterable[dict[str, Any]],
    ) -> ChunkEntry:
        page = write_single_page(
            self.report_root,
            rel_path,
            records,
            compresslevel=self.compresslevel,
            cancel=self.cancel,
        )
        entry = ChunkEntry(key=key, view=view, kind="root", pages=[page])
        self.chunks[key] = entry
        return entry

    def add_child_chunk(
        self,
        key: str,
        view: str,
        rel_base: str,
        records: list[dict[str, Any]],
    ) -> ChunkEntry:
        pages = write_pages(
            self.report_root,
            rel_base,
            records,
            compresslevel=self.compresslevel,
            cancel=self.cancel,
        )
        entry = ChunkEntry(key=key, view=view, kind="children", pages=pages)
        self.chunks[key] = entry
        return entry

    def add_index(self, key: str, view: str, kind: str, rel_path: str, payload: Any) -> PageMeta:
        meta = write_json_gz(
            self.report_root, rel_path, payload, compresslevel=self.compresslevel
        )
        self.chunks[key] = ChunkEntry(key=key, view=view, kind=kind, pages=[meta])
        return meta

    @property
    def total_records(self) -> int:
        return sum(entry.records for entry in self.chunks.values())

    @property
    def total_compressed_bytes(self) -> int:
        return sum(
            page.compressed_bytes for entry in self.chunks.values() for page in entry.pages
        )

    @property
    def total_uncompressed_bytes(self) -> int:
        return sum(
            page.uncompressed_bytes for entry in self.chunks.values() for page in entry.pages
        )

    def to_index(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generator_version": __version__,
            "chunks": {key: entry.to_dict() for key, entry in self.chunks.items()},
        }


def check_page_integrity(report_root: Path, page: PageMeta) -> None:
    path = report_root / page.path
    if not path.is_file():
        raise ChunkIntegrityError(page.path, "referenced shard is missing")
    size = path.stat().st_size
    if size != page.compressed_bytes:
        raise ChunkIntegrityError(
            page.path, f"compressed size mismatch: expected {page.compressed_bytes}, got {size}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    if digest.hexdigest() != page.sha256:
        raise ChunkIntegrityError(page.path, "sha256 mismatch")


__all__ = [
    "ChunkEntry",
    "ChunkStore",
    "MAX_SHARD_BYTES",
    "MAX_SHARD_GROUPS",
    "MAX_SHARD_RECORDS",
    "check_page_integrity",
    "plan_child_shards",
    "write_pages",
    "write_single_page",
]
