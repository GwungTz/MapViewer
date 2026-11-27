"""Background report generation using a QThread worker.

``generate_report`` is pure Python and cooperatively cancellable.  The worker
only emits Qt signals; no UI work ever happens on the generation thread.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from mapcore.cancel import CancelToken
from mapcore.errors import GenerationCancelled, MapViewError
from mapcore.export.report_writer import GenerationResult, generate_report

LOGGER = logging.getLogger("mapview.generation")


class GenerationWorker(QObject):
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, map_path: Path, out_dir: Path, verify: str) -> None:
        super().__init__()
        self._map_path = map_path
        self._out_dir = out_dir
        self._verify = verify
        self._cancel = CancelToken()

    def cancel(self) -> None:
        self._cancel.cancel()

    @pyqtSlot()
    def run(self) -> None:
        LOGGER.info(
            "Generating report: map=%s out=%s verify=%s",
            self._map_path,
            self._out_dir,
            self._verify,
        )
        try:
            result: GenerationResult = generate_report(
                self._map_path,
                self._out_dir,
                verify=self._verify,
                progress=lambda percent, message: self.progress.emit(percent, message),
                cancel=self._cancel,
            )
        except GenerationCancelled:
            LOGGER.info("Generation cancelled: %s", self._map_path)
            self.cancelled.emit()
        except MapViewError as exc:
            LOGGER.error("Generation failed: %s", exc)
            self.failed.emit("生成失败", str(exc))
        except Exception:
            detail = traceback.format_exc()
            LOGGER.exception("Unexpected generation failure")
            self.failed.emit("意外错误", detail)
        else:
            LOGGER.info(
                "Generation finished: %s (chunks=%s records=%s)",
                result.report_dir,
                result.stats.get("chunks"),
                result.stats.get("records"),
            )
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class GenerationService(QObject):
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal()
    started = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: GenerationWorker | None = None

    def is_running(self) -> bool:
        return self._thread is not None

    def start(self, map_path: str | Path, out_dir: str | Path, verify: str) -> bool:
        if self._thread is not None:
            return False
        thread = QThread()
        worker = GenerationWorker(Path(map_path), Path(out_dir), verify)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.progress)
        worker.succeeded.connect(self.succeeded)
        worker.failed.connect(self.failed)
        worker.cancelled.connect(self.cancelled)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(worker.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()
        self.started.emit()
        return True

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def wait(self, timeout_ms: int = 5000) -> bool:
        thread = self._thread
        if thread is None:
            return True
        return thread.wait(timeout_ms)

    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        self.finished.emit()
