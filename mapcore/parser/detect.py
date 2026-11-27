"""MAP format detection.

Formats are detected from *content*, never from the file extension.  The
heuristics match upstream ``isGnuLdMap`` and additionally reject unknown files
instead of silently producing an empty tree.
"""

from __future__ import annotations

import re

from ..errors import UnsupportedMapFormat
from .keil_parser import detect_compiler_version

_GNU_MARKERS = (
    "Memory Configuration",
    "Linker script and memory map",
    "Archive member included",
)

_KEIL_MARKERS = (
    "Memory Map of the image",
    "Image component sizes",
    "Image Symbol Table",
)

_OTHER_MARKERS = (
    "IAR ELF Linker V",
    "MODULE SUMMARY",
    "SEGMENTS IN ADDRESS ORDER",
)


def _first_line(content: str) -> str:
    end = content.find("\n")
    return content if end < 0 else content[:end]


def is_gnu_ld_map(content: str) -> bool:
    first = _first_line(content)
    if "Arm Compiler" in first or "Component:" in first:
        return False
    head = content[:200]
    if "Archive member included" in head or head.startswith("LOAD "):
        return True
    return "Memory Configuration" in content and "Linker script and memory map" in content


def detect_format(content: str) -> str:
    """Return ``keil-ac5``, ``keil-ac6`` or ``gnu-ld``.

    Raises :class:`UnsupportedMapFormat` with the markers that were found (or
    expected) when nothing matches.
    """

    if not content.strip():
        raise UnsupportedMapFormat("The MAP file is empty.")

    first = _first_line(content)
    if is_gnu_ld_map(content):
        return "gnu-ld"

    keil_marker = next((marker for marker in _KEIL_MARKERS if marker in content), None)
    looks_like_keil_header = "Arm Compiler" in first or "Component:" in first
    if keil_marker or looks_like_keil_header:
        return "keil-ac6" if detect_compiler_version(first) == "AC6" else "keil-ac5"

    detected: list[str] = []
    for marker in (*_GNU_MARKERS, *_KEIL_MARKERS, *_OTHER_MARKERS):
        if marker in content:
            detected.append(marker)
    if re.search(r"^LOAD\s", content, re.M):
        detected.append("LOAD line")

    raise UnsupportedMapFormat(
        "Unsupported MAP format. Expected a Keil MDK map ('Memory Map of the image') "
        "or a GNU ld map ('Memory Configuration' + 'Linker script and memory map').",
        detected=detected,
    )
