"""Unified error types used by the parser, exporter and report server."""

from __future__ import annotations

from typing import Iterable


class MapViewError(Exception):
    """Base class for all Map View Local errors."""


class UnsupportedMapFormat(MapViewError):
    """Raised when the input file is not a supported MAP format."""

    def __init__(self, message: str, detected: Iterable[str] | None = None) -> None:
        self.detected = list(detected or ())
        detail = ""
        if self.detected:
            detail = "\nDetected markers: " + ", ".join(self.detected)
        super().__init__(message + detail)


class MapParseError(MapViewError):
    """Raised when a supported MAP file cannot be parsed."""

    def __init__(self, line_no: int, message: str) -> None:
        self.line_no = int(line_no)
        self.message = message
        super().__init__(f"line {self.line_no}: {message}")


class EmptyMapData(MapViewError):
    """Raised when parsing produced no usable regions or components."""


class TreeBuildError(MapViewError):
    """Raised when the transform layer cannot build a view."""


class ReportWriteError(MapViewError):
    """Raised when the report directory cannot be written atomically."""


class ChunkIntegrityError(MapViewError):
    """Raised when a report shard fails checksum, size or schema validation."""

    def __init__(self, chunk: str, message: str) -> None:
        self.chunk = chunk
        self.message = message
        super().__init__(f"chunk {chunk}: {message}")


class GenerationCancelled(MapViewError):
    """Raised when the user cancels a running generation task."""
