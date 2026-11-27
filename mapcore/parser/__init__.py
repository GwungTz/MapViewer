"""Parser facade: detection, dispatch and empty-result validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..errors import EmptyMapData, MapParseError
from .detect import detect_format
from .gnu_ld_parser import classify_section_type, parse_gnu_ld_map
from .keil_parser import parse_keil_map
from .models import (
    CATEGORIES,
    ComponentSize,
    ExecutionRegion,
    GrandTotals,
    LoadRegion,
    MapFileData,
    MemoryRegion,
    MemorySection,
    ParseWarning,
)
from .textio import DecodedText, decode_map_bytes, read_map_file

__all__ = [
    "CATEGORIES",
    "ComponentSize",
    "DecodedText",
    "ExecutionRegion",
    "GrandTotals",
    "LoadRegion",
    "MapFileData",
    "MemoryRegion",
    "MemorySection",
    "ParseWarning",
    "classify_section_type",
    "decode_map_bytes",
    "detect_format",
    "parse_map_file",
    "parse_map_text",
    "read_map_file",
]


@dataclass(slots=True)
class ParsedMapFile:
    data: MapFileData
    raw: bytes
    text: str
    encoding: str
    sha256: str
    size_bytes: int


MAX_SAFE_INTEGER = 2**53 - 1


def _validate_result(data: MapFileData) -> None:
    has_sections = any(
        region.sections for load in data.load_regions for region in load.execution_regions
    )
    has_components = bool(data.component_sizes or data.library_members)
    if not has_sections and not has_components:
        raise EmptyMapData(
            "The MAP file was recognised but contains no object sections or component sizes. "
            "Check that the linker produced a complete map file."
        )

    # The report schema (and the browser layout code) uses JavaScript numbers.
    # Reject values that cannot be represented exactly instead of silently
    # rounding them in the UI.
    for load in data.load_regions:
        for region in load.execution_regions:
            region_values = (
                region.exec_base,
                region.load_base,
                region.size,
                region.max_size,
            )
            if any(value > MAX_SAFE_INTEGER for value in region_values):
                raise MapParseError(
                    0,
                    f"execution region '{region.name}' has an address or size above 2^53-1, "
                    "which the browser cannot represent exactly.",
                )
            for section in region.sections:
                values = (section.exec_addr, section.size)
                if section.load_addr is not None:
                    values += (section.load_addr,)
                if any(value > MAX_SAFE_INTEGER for value in values):
                    raise MapParseError(
                        0,
                        f"section '{section.section_name}' of '{section.object_name}' has an "
                        "address or size above 2^53-1, which the browser cannot represent exactly. "
                        "Reject the map or split the 64-bit image.",
                    )
    for memory_region in data.memory_regions:
        if max(memory_region.origin, memory_region.length) > MAX_SAFE_INTEGER:
            raise MapParseError(
                0,
                f"memory region '{memory_region.name}' exceeds the JavaScript safe integer range.",
            )


def parse_map_text(text: str, *, format_hint: str | None = None) -> MapFileData:
    """Parse MAP text and return the canonical model.

    ``format_hint`` is used by tests/tools that already know the format; the
    normal path always detects from content.
    """

    detected = format_hint or detect_format(text)
    if detected == "gnu-ld":
        data = parse_gnu_ld_map(text)
    else:
        data = parse_keil_map(text)
        data.format = detected
    _validate_result(data)
    return data


def parse_map_file(path: str | Path) -> ParsedMapFile:
    raw, decoded = read_map_file(path)
    data = parse_map_text(decoded.text)
    for warning in decoded.warnings:
        data.warnings.insert(0, ParseWarning(0, warning))
    return ParsedMapFile(
        data=data,
        raw=raw,
        text=decoded.text,
        encoding=decoded.encoding,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
