"""Gzip'd JSONL / JSON writers with deterministic metadata.

Each shard is compressed on its own (gzip is not seekable).  ``mtime=0`` keeps
identical inputs byte-identical across runs, which makes reports easy to diff
and cache.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..errors import ChunkIntegrityError
from .checksum import sha256_file


def dumps_record(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class PageMeta:
    path: str  # POSIX-style path relative to the report root
    records: int
    uncompressed_bytes: int
    compressed_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "records": self.records,
            "uncompressed_bytes": self.uncompressed_bytes,
            "compressed_bytes": self.compressed_bytes,
            "sha256": self.sha256,
        }


def write_jsonl_gz(
    report_root: Path,
    rel_path: str,
    records: Iterable[dict[str, Any]],
    *,
    compresslevel: int = 6,
) -> PageMeta:
    """Write one gzip'd JSONL file and return its integrity metadata."""

    path = report_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    uncompressed = 0
    count = 0
    try:
        with path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0) as gz:
                for record in records:
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


def write_json_gz(
    report_root: Path,
    rel_path: str,
    payload: Any,
    *,
    compresslevel: int = 6,
) -> PageMeta:
    path = report_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0) as gz:
                gz.write(encoded)
    except OSError as exc:
        raise ChunkIntegrityError(rel_path, f"cannot write index: {exc}") from exc
    return PageMeta(
        path=rel_path,
        records=1,
        uncompressed_bytes=len(encoded),
        compressed_bytes=path.stat().st_size,
        sha256=sha256_file(path),
    )


def iter_jsonl_gz(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(line_no, record)`` from a gzip'd JSONL file.

    Raises :class:`ChunkIntegrityError` with the offending line number.
    """

    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="\n") as stream:
            for line_no, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ChunkIntegrityError(
                        path.name, f"invalid JSON on line {line_no}: {exc.msg}"
                    ) from exc
                if not isinstance(record, dict):
                    raise ChunkIntegrityError(
                        path.name, f"line {line_no} is not a JSON object"
                    )
                yield line_no, record
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ChunkIntegrityError(path.name, f"cannot read gzip shard: {exc}") from exc


def read_json_gz(path: Path) -> Any:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        raise ChunkIntegrityError(path.name, f"cannot read index: {exc}") from exc
