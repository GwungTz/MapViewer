"""PyQt6 launcher smoke tests (offscreen)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QEventLoop, QSettings, QTimer
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - optional GUI dependency
    QApplication = None  # type: ignore[assignment]

from mapcore.export.report_writer import GenerationResult
from mapcore.tests.helpers import fixture


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class LauncherSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="mapview-qt-")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.temp.name,
        )
        cls.app = QApplication.instance() or QApplication([])

        from app.logging_setup import setup_logging
        from app.main_window import MainWindow
        from app.settings import AppSettings

        cls.settings = AppSettings()
        cls.window = MainWindow(cls.settings, setup_logging())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.temp.cleanup()

    def test_window_builds(self) -> None:
        self.assertTrue(self.window.windowTitle().startswith("Map View Local"))
        self.assertTrue(self.window.generate_button.isEnabled())
        self.assertFalse(self.window.cancel_button.isEnabled())
        self.assertFalse(self.window.open_button.isEnabled())

    def test_map_selection_sets_default_output(self) -> None:
        path = fixture("keil_ac5.map")
        self.window.set_map_path(path)
        self.assertEqual(self.window.map_edit.text(), str(path))
        self.assertTrue(self.window.output_edit.text().endswith("keil_ac5.mapview"))
        self.assertIn("keil_ac5.map", self.window.windowTitle())

    def test_generation_service_runs_on_worker_thread(self) -> None:
        from app.generation_service import GenerationService

        out_dir = Path(self.temp.name) / "service-report.mapview"
        loop = QEventLoop()
        results: list[object] = []
        service = GenerationService()
        service.succeeded.connect(results.append)
        service.failed.connect(lambda title, detail: results.append((title, detail)))
        service.finished.connect(loop.quit)
        QTimer.singleShot(60000, loop.quit)
        self.assertTrue(service.start(fixture("keil_ac5.map"), out_dir, "none"))
        loop.exec()
        self.assertEqual(len(results), 1, results)
        self.assertIsInstance(results[0], GenerationResult)
        assert isinstance(results[0], GenerationResult)
        self.assertTrue((results[0].report_dir / "manifest.json").is_file())
        self.assertFalse(service.is_running())


if __name__ == "__main__":
    unittest.main()
