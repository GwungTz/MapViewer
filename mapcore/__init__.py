"""Map View Local - standalone embedded MAP file analyzer core.

The package contains three independent layers:

* :mod:`mapcore.parser`   - reads Keil MDK and GNU ld / ESP-IDF MAP files.
* :mod:`mapcore.transform`- builds Region / Module views and memory summaries.
* :mod:`mapcore.export`   - writes the versioned manifest + JSONL.gz report package.

None of these modules import PyQt6 or the browser UI, so reports can be
generated from the command line or from a background Qt thread.
"""

from __future__ import annotations

__version__ = "0.1.0"
SCHEMA_VERSION = 1
GENERATOR_NAME = "mapview-local"

__all__ = ["__version__", "SCHEMA_VERSION", "GENERATOR_NAME"]
