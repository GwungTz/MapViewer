"""Frozen-application entry point used by PyInstaller."""

from __future__ import annotations

import sys


def main() -> int:
    from app.main import main as gui_main

    return gui_main()


def smoke_test() -> int:
    """Verify that the bundled web assets are present and readable."""

    from mapcore.resources import asset_paths

    try:
        paths = asset_paths()
    except Exception:
        return 2
    if len(paths) != 6:
        return 3
    for path in paths.values():
        if not path.is_file() or path.stat().st_size == 0:
            return 4
    return 0


if __name__ == "__main__":
    if "--smoke-test" in sys.argv[1:]:
        raise SystemExit(smoke_test())
    raise SystemExit(main())
