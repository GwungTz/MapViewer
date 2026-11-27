"""Main PyQt6 window: choose a MAP file, generate, serve and open the report.

The window owns no treemap logic.  It only launches background generation,
shows progress/log lines, starts a loopback report server and hands the URL to
the default browser.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mapcore import SCHEMA_VERSION, __version__
from mapcore.errors import MapViewError
from mapcore.export.report_writer import GenerationResult, default_report_dir
from mapcore.report_server import ReportServer

from .generation_service import GenerationService
from .settings import AppSettings

LOGGER = logging.getLogger("mapview.window")


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, log_file: Path) -> None:
        super().__init__()
        self._settings = settings
        self._log_file = log_file
        self._service = GenerationService(self)
        self._server: ReportServer | None = None
        self._server_report: Path | None = None
        self._current_report: Path | None = None
        self._custom_output = False
        self._last_logged_message = ""

        self.setWindowTitle("Map View Local")
        self.resize(QSize(1020, 760))
        self.setAcceptDrops(True)

        self._build_ui()
        self._build_menu()
        self._connect_signals()
        self._restore_settings()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Map View Local")
        title.setStyleSheet("font-size: 19px; font-weight: 700;")
        subtitle = QLabel(
            "选择嵌入式链接器生成的 .map 文件，生成可在默认浏览器中打开的分片式报告。"
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #8a9099;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.map_edit = QLineEdit()
        self.map_edit.setPlaceholderText("例如 D:/project/build/firmware.map（也可以把文件拖进窗口）")
        self.map_edit.setClearButtonEnabled(True)
        self.map_browse = QPushButton("浏览…")
        map_row = QWidget()
        map_layout = QHBoxLayout(map_row)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(6)
        map_layout.addWidget(self.map_edit, 1)
        map_layout.addWidget(self.map_browse)
        form.addRow("MAP 文件", map_row)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("默认：<MAP 所在目录>/<MAP 文件名>.mapview")
        self.output_edit.setClearButtonEnabled(True)
        self.output_browse = QPushButton("选择目录…")
        self.output_default = QPushButton("默认")
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        output_layout.addWidget(self.output_edit, 1)
        output_layout.addWidget(self.output_browse)
        output_layout.addWidget(self.output_default)
        form.addRow("报告目录", output_row)

        self.verify_combo = QComboBox()
        self.verify_combo.addItem("快速校验（哈希 + JSONL）", "basic")
        self.verify_combo.addItem("深度校验（父子/汇总/搜索路径）", "full")
        self.verify_combo.addItem("跳过校验（最快）", "none")
        self.auto_open_check = QCheckBox("生成后自动打开浏览器")
        self.auto_open_check.setChecked(True)
        options_row = QWidget()
        options_layout = QHBoxLayout(options_row)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(14)
        options_layout.addWidget(self.verify_combo)
        options_layout.addWidget(self.auto_open_check)
        options_layout.addStretch(1)
        form.addRow("生成选项", options_row)

        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.generate_button = QPushButton("生成报告")
        self.generate_button.setDefault(True)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.open_button = QPushButton("打开浏览器报告")
        self.open_button.setEnabled(False)
        self.open_existing_button = QPushButton("打开已有报告…")
        button_row.addWidget(self.generate_button)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch(1)
        button_row.addWidget(self.open_button)
        button_row.addWidget(self.open_existing_button)
        layout.addLayout(button_row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.status_label = QLabel("就绪")
        self.status_label.setMinimumWidth(360)
        self.status_label.setWordWrap(True)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.status_label, 2)
        layout.addLayout(progress_row)

        lower = QSplitter(Qt.Orientation.Horizontal)
        lower.setChildrenCollapsible(False)

        recent_frame = QFrame()
        recent_layout = QVBoxLayout(recent_frame)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_layout.setSpacing(6)
        recent_label = QLabel("最近生成的报告")
        recent_label.setStyleSheet("font-weight: 600;")
        self.recent_list = QListWidget()
        self.recent_list.setAlternatingRowColors(True)
        self.recent_list.setMinimumWidth(260)
        recent_layout.addWidget(recent_label)
        recent_layout.addWidget(self.recent_list, 1)

        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(6)
        log_label = QLabel("生成日志")
        log_label.setStyleSheet("font-weight: 600;")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlaceholderText("生成过程与错误信息会显示在这里。")
        log_layout.addWidget(log_label)
        log_layout.addWidget(self.log_view, 1)

        lower.addWidget(recent_frame)
        lower.addWidget(log_frame)
        lower.setStretchFactor(0, 2)
        lower.setStretchFactor(1, 5)
        layout.addWidget(lower, 1)

        self.setCentralWidget(central)
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)
        self.setStatusBarText(f"日志：{self._log_file}")

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("文件")
        open_map_action = QAction("打开 MAP 文件…", self)
        open_map_action.triggered.connect(self.browse_map)
        file_menu.addAction(open_map_action)

        open_report_action = QAction("打开已有报告…", self)
        open_report_action.triggered.connect(self.open_existing_report)
        file_menu.addAction(open_report_action)

        file_menu.addSeparator()
        open_log_action = QAction("打开日志目录", self)
        open_log_action.triggered.connect(self.open_log_folder)
        file_menu.addAction(open_log_action)

        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menu.addMenu("帮助")
        about_action = QAction("关于 Map View Local", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _connect_signals(self) -> None:
        self.map_browse.clicked.connect(self.browse_map)
        self.output_browse.clicked.connect(self.browse_output)
        self.output_default.clicked.connect(self.use_default_output)
        self.map_edit.editingFinished.connect(self._on_map_edited)
        self.output_edit.textEdited.connect(self._on_output_edited)
        self.generate_button.clicked.connect(self.start_generation)
        self.cancel_button.clicked.connect(self.cancel_generation)
        self.open_button.clicked.connect(self.open_report)
        self.open_existing_button.clicked.connect(self.open_existing_report)
        self.recent_list.itemDoubleClicked.connect(self._open_recent_item)

        self._service.progress.connect(self._on_progress)
        self._service.succeeded.connect(self._on_succeeded)
        self._service.failed.connect(self._on_failed)
        self._service.cancelled.connect(self._on_cancelled)
        self._service.finished.connect(self._on_service_finished)

    def _restore_settings(self) -> None:
        geometry = self._settings.geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = self._settings.window_state()
        if state is not None:
            self.restoreState(state)

        self.auto_open_check.setChecked(self._settings.auto_open())
        level = self._settings.verify_level()
        index = self.verify_combo.findData(level)
        if index >= 0:
            self.verify_combo.setCurrentIndex(index)

        self._refresh_recent()
        self._log("就绪。选择一个 .map 文件开始生成报告。")
        LOGGER.info("Map View Local %s started (schema v%s)", __version__, SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_map_edited(self) -> None:
        text = self.map_edit.text().strip()
        if text and not self._custom_output:
            self.output_edit.setText(str(default_report_dir(Path(text))))

    def _on_output_edited(self, _text: str) -> None:
        self._custom_output = True

    def browse_map(self) -> None:
        start_dir = self._settings.last_input_dir()
        if not start_dir:
            start_dir = str(Path.home())
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "选择 MAP 文件",
            start_dir,
            "MAP 文件 (*.map *.txt);;所有文件 (*)",
        )
        if path:
            self.set_map_path(path)

    def set_map_path(self, path: str | Path) -> None:
        map_path = Path(path)
        if not map_path.is_file():
            QMessageBox.warning(self, "文件不存在", f"找不到 MAP 文件：\n{map_path}")
            return
        self.map_edit.setText(str(map_path))
        if not self._custom_output or not self.output_edit.text().strip():
            self.output_edit.setText(str(default_report_dir(map_path)))
            self._custom_output = False
        self._settings.set_last_input_dir(map_path.parent)
        self.setWindowTitle(f"Map View Local · {map_path.name}")
        self._log(f"已选择 MAP 文件：{map_path}")
        self.status_label.setText(f"已选择 {map_path.name}")

    def browse_output(self) -> None:
        start_dir = self.output_edit.text().strip() or self._settings.last_output_dir()
        if not start_dir:
            start_dir = str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择报告输出目录", start_dir)
        if not directory:
            return
        map_text = self.map_edit.text().strip()
        stem = Path(map_text).stem if map_text else "report"
        output = Path(directory) / f"{stem}.mapview"
        self.output_edit.setText(str(output))
        self._custom_output = True
        self._settings.set_last_output_dir(directory)

    def use_default_output(self) -> None:
        map_text = self.map_edit.text().strip()
        if not map_text:
            return
        self.output_edit.setText(str(default_report_dir(Path(map_text))))
        self._custom_output = False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.is_file():
                    self.set_map_path(path)
                    event.acceptProposedAction()
                    return
                if path.is_dir() and (path / "manifest.json").is_file():
                    self._use_report(path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def start_generation(self) -> None:
        if self._service.is_running():
            return
        map_text = self.map_edit.text().strip()
        if not map_text:
            QMessageBox.information(self, "请选择 MAP 文件", "请先选择一个链接器 .map 文件。")
            return
        map_path = Path(map_text)
        if not map_path.is_file():
            QMessageBox.warning(self, "文件不存在", f"找不到 MAP 文件：\n{map_path}")
            return

        output_text = self.output_edit.text().strip()
        out_dir = Path(output_text) if output_text else default_report_dir(map_path)
        if out_dir.exists():
            answer = QMessageBox.question(
                self,
                "替换报告目录",
                f"报告目录已存在，是否替换？\n\n{out_dir}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        verify = self.verify_combo.currentData() or "basic"
        self._settings.set_verify_level(str(verify))
        self._settings.set_auto_open(self.auto_open_check.isChecked())
        if output_text:
            self._settings.set_last_output_dir(out_dir.parent)

        self._set_busy(True)
        self.progress.setValue(0)
        self.status_label.setText("正在启动后台生成任务…")
        self._log(f"开始生成：{map_path} → {out_dir}（校验级别：{verify}）")
        if not self._service.start(map_path, out_dir, str(verify)):
            self._set_busy(False)
            QMessageBox.warning(self, "任务已存在", "已有一个生成任务在运行。")

    def cancel_generation(self) -> None:
        if not self._service.is_running():
            return
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在取消…")
        self._log("正在取消生成任务…")
        self._service.cancel()

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.map_edit,
            self.map_browse,
            self.output_edit,
            self.output_browse,
            self.output_default,
            self.verify_combo,
            self.auto_open_check,
            self.generate_button,
            self.open_existing_button,
        ):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            self.progress.setValue(0)
            self.progress.setFormat("%p%")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.status_label.setText(message)
        if message != self._last_logged_message:
            self._last_logged_message = message
            self._log(message)

    def _on_succeeded(self, result: GenerationResult) -> None:
        self._set_busy(False)
        self._current_report = result.report_dir
        self.open_button.setEnabled(True)
        self._settings.add_recent_report(result.report_dir)
        self._refresh_recent()
        self._settings.set_last_output_dir(result.report_dir.parent)

        stats = result.stats
        self._log("报告生成完成。")
        self._log(
            f"  节点：region={stats.get('region_nodes')} module={stats.get('module_nodes')} "
            f"记录={stats.get('records')} 分片={stats.get('chunks')}"
        )
        self._log(
            f"  体积：{stats.get('compressed_bytes', 0) / 1024:.1f} KiB（gzip） / "
            f"{stats.get('uncompressed_bytes', 0) / 1024:.1f} KiB（原文）"
        )
        timings = result.timings_ms
        self._log(
            f"  耗时：解析 {timings.get('parse', 0)} ms，转换 {timings.get('transform', 0)} ms，"
            f"写入 {timings.get('write', 0)} ms，校验 {timings.get('verify', 0)} ms，"
            f"合计 {timings.get('total', 0)} ms"
        )
        warnings = result.warnings
        if warnings:
            self._log(f"  解析警告 {len(warnings)} 条：")
            for warning in warnings[:20]:
                prefix = f"line {warning.line_no}: " if warning.line_no else ""
                self._log(f"    {prefix}{warning.message}")
            if len(warnings) > 20:
                self._log(f"    … 其余 {len(warnings) - 20} 条见报告 manifest.json")
            self.status_label.setText(f"生成完成（{len(warnings)} 条警告）")
        else:
            self.status_label.setText("生成完成")

        self._log(f"报告目录：{result.report_dir}")
        if self.auto_open_check.isChecked():
            self.open_report()
        else:
            self.setStatusBarText(f"报告已生成：{result.report_dir}")

    def _on_failed(self, title: str, detail: str) -> None:
        self._set_busy(False)
        self.status_label.setText(f"{title}")
        self._log(f"{title}：{detail}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("生成失败")
        box.setText(title)
        box.setInformativeText("请查看日志或将详细信息展开。")
        box.setDetailedText(detail)
        box.exec()

    def _on_cancelled(self) -> None:
        self._set_busy(False)
        self.status_label.setText("已取消")
        self._log("生成任务已取消。")

    def _on_service_finished(self) -> None:
        if self._service.is_running():
            return
        self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Reports / server
    # ------------------------------------------------------------------

    def open_report(self) -> None:
        if self._current_report is None:
            self.open_existing_report()
            return
        self._open_report_dir(self._current_report)

    def open_existing_report(self) -> None:
        start_dir = self._settings.last_output_dir() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择报告目录", start_dir)
        if directory:
            self._use_report(Path(directory))

    def _use_report(self, report_dir: Path) -> None:
        if not (report_dir / "manifest.json").is_file():
            QMessageBox.warning(
                self,
                "不是有效的报告目录",
                f"该目录缺少 manifest.json：\n{report_dir}\n\n"
                "请选择由本程序生成的 *.mapview 目录。",
            )
            return
        self._current_report = report_dir
        self.open_button.setEnabled(True)
        self._settings.add_recent_report(report_dir)
        self._refresh_recent()
        self._log(f"打开已有报告：{report_dir}")
        self._open_report_dir(report_dir)

    def _open_report_dir(self, report_dir: Path) -> None:
        if not report_dir.is_dir() or not (report_dir / "manifest.json").is_file():
            QMessageBox.warning(self, "报告不存在", f"报告目录不存在或不完整：\n{report_dir}")
            return
        target = report_dir.resolve()
        if self._server is not None and self._server_report != target:
            self._stop_server()
        if self._server is None:
            preferred_port = self._settings.server_port()
            candidates = [preferred_port, 0] if preferred_port else [0]
            server: ReportServer | None = None
            last_error: MapViewError | None = None
            url = ""
            for candidate in candidates:
                try:
                    server = ReportServer(target, port=candidate)
                    url = server.start()
                    break
                except MapViewError as exc:
                    last_error = exc
                    server = None
            if server is None:
                self._log(f"启动本地报告服务失败：{last_error}")
                QMessageBox.critical(
                    self, "无法启动本地报告服务", str(last_error or "unknown error")
                )
                return
            self._server = server
            self._settings.set_server_port(server.port)
            self._server_report = target
            self._log(f"本地报告服务已启动：{url}")
        assert self._server is not None
        url = self._server.url
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(
                self,
                "无法打开浏览器",
                f"系统未能打开默认浏览器。请手动访问：\n{url}",
            )
        self.setStatusBarText(f"报告服务：{url}（关闭启动器后未缓存分片将无法加载）")
        self._log(f"已在默认浏览器中打开：{url}")

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
            self._server_report = None
            self._log("本地报告服务已停止。")

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        report_dir = Path(str(path))
        if not report_dir.is_dir():
            answer = QMessageBox.question(
                self,
                "报告不存在",
                f"报告目录不存在：\n{report_dir}\n\n是否从最近列表中移除？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._settings.remove_recent_report(report_dir)
                self._refresh_recent()
            return
        self._use_report(report_dir)

    def _refresh_recent(self) -> None:
        self.recent_list.clear()
        for path in self._settings.recent_reports():
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            if not Path(path).is_dir():
                item.setForeground(Qt.GlobalColor.gray)
            self.recent_list.addItem(item)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def setStatusBarText(self, text: str) -> None:
        if self.statusBar() is not None:
            self.statusBar().showMessage(text)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")
        LOGGER.info(message)

    def open_log_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_file.parent)))

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 Map View Local",
            f"<b>Map View Local</b> {__version__}<br>"
            f"报告 schema_version = {SCHEMA_VERSION}<br><br>"
            "独立版 Map View：PyQt6 负责生成与本地服务，Treemap 在浏览器中渲染。<br>"
            "解析与 Treemap 交互基于 charcoal141/Map-View（MIT License）。<br>"
            "详见随附 THIRD_PARTY_NOTICES.md 与 LICENSE。<br><br>"
            f"日志文件：{self._log_file}",
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._service.is_running():
            answer = QMessageBox.question(
                self,
                "正在生成报告",
                "后台生成任务仍在运行。是否取消并退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._service.cancel()
            if not self._service.wait(5000):
                QMessageBox.warning(
                    self,
                    "仍在停止",
                    "后台任务尚未停止，请稍后再试。",
                )
                event.ignore()
                return

        if self._server is not None:
            answer = QMessageBox.question(
                self,
                "关闭 Map View",
                "关闭启动器后将停止本地报告服务；浏览器中尚未加载的报告分片将无法继续加载。\n\n"
                "确定关闭吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._stop_server()

        self._settings.save_geometry(self.saveGeometry())
        self._settings.save_window_state(self.saveState())
        self._settings.set_auto_open(self.auto_open_check.isChecked())
        self._settings.set_verify_level(str(self.verify_combo.currentData() or "basic"))
        self._settings.sync()
        super().closeEvent(event)
