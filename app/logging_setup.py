"""Logging configuration.

Logs go to a user-writable location so the launcher never depends on a console
window (important for the PyInstaller ``--windowed`` build).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_directory() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "MapViewer" / "logs"


def setup_logging(level: int = logging.INFO) -> Path:
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "mapviewer.log"

    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)

    if (
        sys.stderr is not None
        and hasattr(sys.stderr, "write")
        and not any(
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, RotatingFileHandler)
            for handler in root.handlers
        )
    ):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(console)

    return log_file
