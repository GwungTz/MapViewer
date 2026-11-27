"""Application entry point (``python -m app.main`` or launcher.py)."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from mapcore import __version__
from mapcore.errors import MapViewError

from .logging_setup import setup_logging
from .main_window import MainWindow
from .settings import AppSettings


def main(argv: list[str] | None = None) -> int:
    log_file = setup_logging()
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Map View Local")
    app.setOrganizationName("MapViewLocal")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("MapViewLocal")

    settings = AppSettings()
    try:
        window = MainWindow(settings, log_file)
    except MapViewError as exc:  # pragma: no cover - startup failure path
        QMessageBox.critical(None, "Map View Local", str(exc))
        return 2
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
