"""Locate the bundled web assets both from source and from PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .errors import MapViewError

# source path -> path inside the report directory
ASSET_MAP = {
    "index.html": "index.html",
    "favicon.svg": "favicon.svg",
    "css/style.css": "assets/style.css",
    "js/app.js": "assets/app.js",
    "js/treemap.js": "assets/treemap.js",
    "js/data-worker.js": "assets/data-worker.js",
}


def _candidates() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("MAPVIEW_WEB_DIR")
    if env:
        candidates.append(Path(env))
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "web")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "web")
    candidates.append(Path(__file__).resolve().parent.parent / "web")
    candidates.append(Path(sys.prefix) / "web")
    candidates.append(Path.cwd() / "web")
    return candidates


def web_dir() -> Path:
    for candidate in _candidates():
        if (candidate / "index.html").is_file() and (candidate / "js" / "app.js").is_file():
            return candidate
    raise MapViewError(
        "Web assets were not found. Expected a 'web' directory next to the application "
        "(index.html, js/app.js, js/treemap.js, js/data-worker.js, css/style.css)."
    )


def asset_paths() -> dict[str, Path]:
    root = web_dir()
    missing = [name for name in ASSET_MAP if not (root / name).is_file()]
    if missing:
        raise MapViewError("Web assets are incomplete; missing: " + ", ".join(sorted(missing)))
    return {name: root / name for name in ASSET_MAP}
