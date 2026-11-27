"""MAP file reading with explicit BOM / encoding handling.

Browsers and linkers do not agree on encodings: Keil on a Chinese Windows
install often emits GBK encoded object paths.  We never silently discard bytes:
each fallback is reported as a warning so the UI can explain the guess.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import MapViewError

# Candidate encodings tried in order when the file has no BOM and is not UTF-8.
_FALLBACK_ENCODINGS = ("utf-8", "gb18030", "cp1252")


@dataclass(slots=True)
class DecodedText:
    text: str
    encoding: str
    warnings: list[str] = field(default_factory=list)


def decode_map_bytes(raw: bytes) -> DecodedText:
    """Decode raw MAP bytes, preferring BOM / UTF-8 and reporting fallbacks."""

    warnings: list[str] = []
    if not raw:
        raise MapViewError("The MAP file is empty.")

    if raw.startswith(codecs.BOM_UTF8):
        return DecodedText(raw.decode("utf-8-sig", errors="replace"), "utf-8-sig", warnings)
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError as exc:  # pragma: no cover - very unusual
            raise MapViewError(f"The MAP file has a UTF-16 BOM but cannot be decoded: {exc}") from exc
        warnings.append("The MAP file uses a UTF-16 BOM; it was decoded as UTF-16.")
        return DecodedText(text, "utf-16", warnings)

    for encoding in _FALLBACK_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding != "utf-8":
            warnings.append(
                f"The MAP file is not UTF-8; it was decoded as {encoding}. "
                "If path or symbol names look corrupted, re-export the MAP file as UTF-8."
            )
        return DecodedText(text, encoding, warnings)

    # Latin-1 cannot fail, but arrive here defensively.
    text = raw.decode("latin-1", errors="replace")
    warnings.append(
        "The MAP file is not valid UTF-8 or GB18030; it was decoded as Latin-1 and "
        "some characters may be wrong. Re-export the MAP file as UTF-8 if names look corrupted."
    )
    return DecodedText(text, "latin-1", warnings)


def read_map_file(path: str | Path) -> tuple[bytes, DecodedText]:
    """Read a MAP file from disk and decode it.

    Returns the raw bytes (needed for the source SHA-256) and the decoded text.
    """

    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise MapViewError(f"Cannot read '{file_path}': {exc}") from exc
    return raw, decode_map_bytes(raw)
