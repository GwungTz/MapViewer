"""Persistent launcher settings via QSettings."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QByteArray, QSettings

ORGANIZATION = "MapViewLocal"
APPLICATION = "MapViewer"

MAX_RECENT_REPORTS = 10


class AppSettings:
    def __init__(self) -> None:
        self._settings = QSettings(ORGANIZATION, APPLICATION)

    # -- recent reports ---------------------------------------------------
    def recent_reports(self) -> list[str]:
        raw = self._settings.value("recentReports", "", type=str)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if item]

    def add_recent_report(self, report_dir: str | Path) -> None:
        path = str(Path(report_dir))
        reports = [item for item in self.recent_reports() if item != path]
        reports.insert(0, path)
        self._settings.setValue("recentReports", json.dumps(reports[:MAX_RECENT_REPORTS]))

    def remove_recent_report(self, report_dir: str | Path) -> None:
        path = str(Path(report_dir))
        reports = [item for item in self.recent_reports() if item != path]
        self._settings.setValue("recentReports", json.dumps(reports))

    # -- paths ------------------------------------------------------------
    def last_input_dir(self) -> str:
        return self._settings.value("lastInputDir", "", type=str)

    def set_last_input_dir(self, directory: str | Path) -> None:
        self._settings.setValue("lastInputDir", str(directory))

    def last_output_dir(self) -> str:
        return self._settings.value("lastOutputDir", "", type=str)

    def set_last_output_dir(self, directory: str | Path) -> None:
        self._settings.setValue("lastOutputDir", str(directory))

    # -- generation options ----------------------------------------------
    def auto_open(self) -> bool:
        return self._settings.value("autoOpen", True, type=bool)

    def set_auto_open(self, enabled: bool) -> None:
        self._settings.setValue("autoOpen", bool(enabled))

    def verify_level(self) -> str:
        value = self._settings.value("verifyLevel", "basic", type=str)
        return value if value in ("none", "basic", "full") else "basic"

    def set_verify_level(self, level: str) -> None:
        self._settings.setValue("verifyLevel", level if level in ("none", "basic", "full") else "basic")

    # -- local report server ---------------------------------------------
    def server_port(self) -> int:
        """Preferred loopback port (0 = let the OS choose).

        The port is reused across launches when available so the browser keeps
        the same origin and therefore the same localStorage memory overrides.
        """

        try:
            return int(self._settings.value("serverPort", 0, type=int))
        except (TypeError, ValueError):
            return 0

    def set_server_port(self, port: int) -> None:
        if port and 0 < int(port) < 65536:
            self._settings.setValue("serverPort", int(port))

    # -- window geometry --------------------------------------------------
    def geometry(self) -> QByteArray | None:
        value = self._settings.value("geometry")
        return value if isinstance(value, QByteArray) else None

    def save_geometry(self, geometry: QByteArray) -> None:
        self._settings.setValue("geometry", geometry)

    def window_state(self) -> QByteArray | None:
        value = self._settings.value("windowState")
        return value if isinstance(value, QByteArray) else None

    def save_window_state(self, state: QByteArray) -> None:
        self._settings.setValue("windowState", state)

    def sync(self) -> None:
        self._settings.sync()
