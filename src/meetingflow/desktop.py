"""MeetingFlow 本地桌面工作台。"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .analyze import automodel_options
from .desktop_data import (
    MODEL_DIRECTORIES,
    STAGE_LABELS,
    STATUS_LABELS,
    DesktopJob,
    configure_path,
    default_settings,
    read_jobs,
    save_settings,
    settings_for_root,
)
from .desktop_environment import CHECK_TITLES, MODEL_FIELDS, EnvironmentCheck, ffmpeg_install_command
from .pipeline import Settings, job_speakers, load_settings, output_formats, save_output_formats

MEDIA_FILTER = "音频和视频 (*.aac *.flac *.m4a *.mka *.mkv *.mov *.mp3 *.mp4 *.wav *.webm);;所有文件 (*)"
MEDIA_SUFFIXES = {".aac", ".flac", ".m4a", ".mka", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}

STYLE = """
QWidget { font-size: 14px; color: #243d37; }
QMainWindow, QDialog { background: #f7f9f7; }
QFrame#sidebar { background: #edf2ef; border-right: 1px solid #d8e2dc; }
QLabel#brand { font-size: 22px; font-weight: 700; color: #174d40; }
QLabel#title { font-size: 30px; font-weight: 700; color: #173f33; }
QLabel#heading { font-size: 19px; font-weight: 600; }
QLabel#muted { color: #566b63; }
QLabel#eyebrow { font-size: 12px; font-weight: 600; color: #566b63; }
QLabel#badge { color: #145640; background: #e3eee7; border-radius: 12px; padding: 7px 12px; }
QLabel#notice { background: #fff2dc; color: #795015; border: 1px solid #ecd6ae; border-radius: 8px; padding: 12px; }
QFrame#drop { background: #ffffff; border: 1px dashed #9bb8a8; border-radius: 14px; }
QFrame#panel { background: #ffffff; border: 1px solid #dce5df; border-radius: 12px; }
QPushButton { background: #ffffff; border: 1px solid #b4c6bb; border-radius: 7px; padding: 10px 16px; font-weight: 500; }
QPushButton:hover { background: #e9f1ec; border-color: #759c87; }
QPushButton:pressed { background: #d9e8df; }
QPushButton#primary { background: #21684d; color: #ffffff; border-color: #21684d; }
QPushButton#primary:hover { background: #174f3a; }
QPushButton:disabled { background: #e7ece8; border-color: #d9e1db; color: #75867c; }
QPushButton#primary:disabled { background: #e7ece8; border-color: #d9e1db; color: #75867c; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QListWidget:focus, QTextBrowser:focus { border: 2px solid #21684d; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 13px 10px; border-radius: 8px; margin: 3px 0; }
QListWidget::item:hover { background: #e1eae3; }
QListWidget::item:selected { background: #d7e7dc; color: #164f36; }
QTextBrowser { background: #ffffff; border: none; padding: 12px; selection-background-color: #d7e7dc; }
QTabWidget::pane { background: #ffffff; border: none; }
QTabBar::tab { padding: 12px 18px; border-bottom: 2px solid transparent; color: #566b63; }
QTabBar::tab:selected { border-bottom: 2px solid #21684d; color: #174d40; }
QLineEdit, QComboBox { background: #ffffff; border: 1px solid #b4c6bb; border-radius: 6px; padding: 9px; min-height: 20px; }
QScrollArea { border: none; background: transparent; }
QProgressBar { border: none; background: #e3ebe5; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #40856a; border-radius: 3px; }
QStatusBar { background: #f7f9f7; color: #566b63; }
"""


def label(text: str, role: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text: str, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setMinimumHeight(42)
    if primary:
        widget.setObjectName("primary")
    return widget


class WaveMark(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(44, 44)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#21684d"))
        painter.drawRoundedRect(0, 0, 44, 44, 12, 12)
        painter.setBrush(QColor("#ffffff"))
        for index, height in enumerate((10, 20, 28, 16, 10)):
            painter.drawRoundedRect(9 + index * 6, (44 - height) // 2, 3, height, 1.5, 1.5)


class DropCard(QFrame):
    file_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(176)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        self.title = label("从一段录音开始", "heading", True)
        self.detail = label("将音频或视频拖到这里，或点击按钮选择文件。", "muted", True)
        self.choose = button("选择录音文件")
        self.choose.setAccessibleName("选择要转写的音频或视频文件")
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addSpacing(6)
        layout.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignLeft)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in MEDIA_SUFFIXES:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.file_selected.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, config: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置 · MeetingFlow")
        self.resize(850, 750)
        self.fields: dict[str, QLineEdit] = {}
        self._checks: list[EnvironmentCheck] = []
        self._setup_kind = ""
        self._setup_output = bytearray()
        self._setup_temporary: tempfile.TemporaryDirectory[str] | None = None
        self._setup_process = QProcess(self)
        self._setup_process.readyReadStandardOutput.connect(self._setup_read)
        self._setup_process.readyReadStandardError.connect(self._setup_read_error)
        self._setup_process.finished.connect(self._setup_finished)
        self._setup_process.errorOccurred.connect(self._setup_error)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.addWidget(label("设置与运行环境", "heading"))
        self.settings_tabs = QTabWidget()
        self.storage_page = QWidget()
        layout = QVBoxLayout(self.storage_page)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.addWidget(label("设置本机目录。原始录音始终只读，任务和输出分别保存。", "muted", True))
        root_form = QFormLayout()
        self.root_field = QLineEdit()
        self.root_field.setReadOnly(True)
        self.root_field.setAccessibleName("Meetings 根目录")
        self.root_field.setPlaceholderText("当前使用分别设置的目录，可选择统一根目录")
        self.root_button = button("更改根目录…", True)
        self.root_button.clicked.connect(self._meetings_root)
        root_row = QHBoxLayout()
        root_row.addWidget(self.root_field, 1)
        root_row.addWidget(self.root_button)
        root_label = QLabel("Meetings 根目录")
        root_label.setBuddy(self.root_field)
        root_form.addRow(root_label, root_row)
        layout.addLayout(root_form)
        layout.addWidget(label("一次更新 Inbox、Work、Output 和 Models 下的三个模型路径。点击「保存设置」生效，已有文件不会自动迁移。", "muted", True))
        layout.addWidget(label("各目录（可单独调整）", "eyebrow"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setSpacing(12)
        for key, title in (("inbox", "录音 Inbox"), ("work", "任务工作目录"), ("output", "成品输出目录"),
                           ("sensevoice_dir", "SenseVoice 模型"), ("vad_dir", "VAD 模型"), ("spk_dir", "CAM++ 模型")):
            value = settings[key] if key in {"inbox", "work", "output"} else settings["models"][key]
            field = QLineEdit(str(value))
            field.setAccessibleName(title)
            self.fields[key] = field
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            browse = button("浏览…")
            browse.clicked.connect(lambda _checked=False, target=field: self._browse(target))
            row_layout.addWidget(field, 1)
            row_layout.addWidget(browse)
            title_label = QLabel(title)
            title_label.setBuddy(field)
            form.addRow(title_label, row)
        root_button = button("从模型根目录填入三个路径…")
        root_button.clicked.connect(self._model_root)
        form.addRow("", root_button)
        for field in self.fields.values():
            field.textChanged.connect(self._sync_root)
        self._sync_root()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.settings_tabs.addTab(self.storage_page, "存储位置")
        self._build_environment()
        outer.addWidget(self.settings_tabs, 1)
        outer.addWidget(label(f"配置保存位置：{config}", "muted", True))
        self.error = label("", "notice", True)
        self.error.hide()
        outer.addWidget(self.error)
        self.actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.actions.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        self.actions.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.actions.accepted.connect(self.accept)
        self.actions.rejected.connect(self.reject)
        outer.addWidget(self.actions)

    def _build_environment(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.addWidget(label("检查当前填写的目录和本机依赖。下载模型需要联网，录音与转写内容不会上传。", "muted", True))
        toolbar = QHBoxLayout()
        self.check_button = button("校验运行环境", True)
        self.check_button.clicked.connect(self._check_environment)
        self.download_button = button("下载缺失模型")
        self.download_button.clicked.connect(self._download_missing)
        self.download_button.hide()
        self.stop_button = button("停止")
        self.stop_button.clicked.connect(self._setup_process.terminate)
        self.stop_button.hide()
        toolbar.addWidget(self.check_button)
        toolbar.addWidget(self.download_button)
        toolbar.addStretch()
        toolbar.addWidget(self.stop_button)
        layout.addLayout(toolbar)
        self.setup_summary = label("尚未校验。选择根目录后，可在这里检查并补齐运行环境。", "muted", True)
        layout.addWidget(self.setup_summary)
        self.setup_progress = QProgressBar()
        self.setup_progress.setRange(0, 0)
        self.setup_progress.hide()
        layout.addWidget(self.setup_progress)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        self.check_labels: dict[str, QLabel] = {}
        self.fix_buttons: dict[str, QPushButton] = {}
        for key, title in CHECK_TITLES.items():
            card = QFrame()
            card.setObjectName("panel")
            row = QHBoxLayout(card)
            texts = QVBoxLayout()
            texts.addWidget(label(title, "eyebrow"))
            self.check_labels[key] = label("尚未校验", "muted", True)
            texts.addWidget(self.check_labels[key])
            row.addLayout(texts, 1)
            action = button("补齐")
            action.clicked.connect(lambda _checked=False, target=key: self._fix_environment(target))
            action.hide()
            row.addWidget(action)
            self.fix_buttons[key] = action
            rows.addWidget(card)
        rows.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.setup_log = QPlainTextEdit()
        self.setup_log.setReadOnly(True)
        self.setup_log.setMaximumBlockCount(100)
        self.setup_log.setMaximumHeight(110)
        self.setup_log.setAccessibleName("环境安装与下载进度")
        self.setup_log.hide()
        layout.addWidget(self.setup_log)
        self.settings_tabs.addTab(page, "运行环境")

    def _reset_checks(self) -> None:
        if not hasattr(self, "check_labels"):
            return
        self._checks = []
        self.download_button.hide()
        self.setup_summary.setText("目录已更改，请重新校验运行环境。")
        for key in CHECK_TITLES:
            self.check_labels[key].setText("待重新校验")
            self.fix_buttons[key].hide()

    def _setup_busy(self, busy: bool) -> None:
        self.storage_page.setEnabled(not busy)
        self.actions.setEnabled(not busy)
        self.check_button.setEnabled(not busy)
        self.download_button.setEnabled(not busy)
        self.setup_progress.setVisible(busy)
        self.stop_button.setVisible(busy and self._setup_kind in {"check", "models"})
        for action in self.fix_buttons.values():
            action.setEnabled(not busy)

    def _start_setup(self, operation: str, command: list[str] | None = None, roles: list[str] | None = None) -> None:
        from .pipeline import validate_settings

        if self._setup_process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            settings = self.settings()
            validate_settings(settings)
            self._setup_temporary = tempfile.TemporaryDirectory(prefix="meetingflow-setup-")
            config = Path(self._setup_temporary.name) / "settings.toml"
            save_settings(config, settings)
        except (OSError, ValueError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        root = Path(__file__).resolve().parents[2]
        if operation == "models":
            command = [sys.executable, "-u", str(root / "scripts" / "prepare-models.py"), "--config", str(config), "--roles", *(roles or [])]
        if command is None:
            command = [sys.executable, "-u", "-m", "meetingflow.desktop_worker", "--config", str(config), operation]
        if Path(command[0]).name.lower() == "pythonw.exe":
            command[0] = str(Path(command[0]).with_name("python.exe"))
        self._setup_kind = operation
        self._setup_output.clear()
        self.error.hide()
        self.setup_log.clear()
        self.setup_log.setVisible(operation != "check")
        self.setup_summary.setText("正在校验目录、音频工具、推理依赖和模型完整性…" if operation == "check" else "正在补齐运行环境，完成后会自动重新校验。模型与配置路径需保存后生效。")
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("HOMEBREW_NO_AUTO_UPDATE", "1")
        self._setup_process.setProcessEnvironment(environment)
        self._setup_process.setWorkingDirectory(str(root))
        self._setup_process.start(command[0], command[1:])
        self._setup_process.write(b"{}")
        self._setup_process.closeWriteChannel()
        self._setup_busy(True)

    def _setup_read(self) -> None:
        chunk = bytes(self._setup_process.readAllStandardOutput())
        if self._setup_kind == "check":
            self._setup_output.extend(chunk)
        elif chunk:
            self.setup_log.appendPlainText(chunk.decode("utf-8", errors="replace").strip())

    def _setup_read_error(self) -> None:
        chunk = bytes(self._setup_process.readAllStandardError())
        if self._setup_kind != "check" and chunk:
            self.setup_log.appendPlainText(chunk.decode("utf-8", errors="replace").strip())

    def _setup_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._setup_read()
        self._setup_read_error()
        operation = self._setup_kind
        self._setup_busy(False)
        if self._setup_temporary is not None:
            self._setup_temporary.cleanup()
            self._setup_temporary = None
        if operation == "check":
            try:
                response = json.loads(self._setup_output.decode("utf-8"))
                if not isinstance(response, dict):
                    raise ValueError("校验结果格式异常，请重新校验")
                if not response.get("ok"):
                    raise ValueError(response.get("error", {}).get("message", "环境校验失败"))
                self._show_checks(response["checks"])
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
                self.setup_summary.setText("校验已停止或未返回有效结果，可再次点击校验。")
            except ValueError as error:
                self.setup_summary.setText(f"校验未完成：{error}。可再次点击校验。")
        elif exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self.setup_summary.setText("安装或下载未完成，请查看下方详情。模型暂存内容会保留，可重新校验后重试。")
        else:
            configure_path()
            QTimer.singleShot(0, self._check_environment)

    def _setup_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._setup_busy(False)
            self.setup_summary.setText(f"无法启动安装或校验进程：{self._setup_process.errorString()}")
            if self._setup_temporary is not None:
                self._setup_temporary.cleanup()
                self._setup_temporary = None

    def _show_checks(self, checks: list[EnvironmentCheck]) -> None:
        self._checks = checks
        for check in checks:
            key = check["key"]
            ready = check["status"] == "ready"
            message = check["message"]
            self.check_labels[key].setText(("可用 · " if ready else "待补齐 · ") + message[:180])
            self.check_labels[key].setToolTip(message)
            action = self.fix_buttons[key]
            action.setVisible(bool(check["action"]))
            if key in MODEL_FIELDS:
                action.setText("下载模型" if not self.settings()["models"][MODEL_FIELDS[key]].exists() else "下载到新目录")
            elif key == "directories":
                action.setText("创建目录")
            elif key == "ffmpeg":
                action.setText("安装 FFmpeg" if ffmpeg_install_command() else "打开安装页面")
            else:
                action.setText("驱动下载页面" if check["action"] == "driver" else "同步 Python 依赖")
        missing = sum(check["status"] != "ready" for check in checks)
        self.setup_summary.setText(f"检查完成：{missing} 项待补齐，可使用对应按钮处理。" if missing else "环境校验通过，可以开始本地转写。")
        self.download_button.setVisible(any(check["key"] in MODEL_FIELDS and check["status"] != "ready" for check in checks))

    def _check_environment(self) -> None:
        self._start_setup("check")

    def _download_missing(self) -> None:
        roles = [check["key"] for check in self._checks if check["key"] in MODEL_FIELDS and check["status"] != "ready"]
        self._download_models(roles)

    def _download_models(self, roles: list[str]) -> None:
        if not roles:
            return
        try:
            settings = self.settings()
            for role in roles:
                field = self.fields[MODEL_FIELDS[role]]
                target = settings["models"][MODEL_FIELDS[role]]
                if not target.name:
                    raise ValueError("模型目录不能设置为磁盘根目录")
                if target.exists():
                    candidate = target.with_name(target.name + ".downloaded")
                    suffix = 2
                    while candidate.exists():
                        candidate = target.with_name(f"{target.name}.downloaded-{suffix}")
                        suffix += 1
                    field.setText(str(candidate))
        except (OSError, ValueError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self._start_setup("models", roles=roles)

    def _fix_environment(self, key: str) -> None:
        if key in MODEL_FIELDS:
            self._download_models([key])
        elif key == "directories":
            self._start_setup("create-directories")
        elif key == "ffmpeg":
            command = ffmpeg_install_command()
            if command:
                self._start_setup("ffmpeg", command)
            else:
                url = "https://brew.sh" if sys.platform == "darwin" else "https://ffmpeg.org/download.html"
                if not QDesktopServices.openUrl(QUrl(url)):
                    self.setup_summary.setText(f"请在浏览器打开安装页面：{url}")
        elif key == "python":
            current = next((check for check in self._checks if check["key"] == key), None)
            if current is not None and current["action"] == "driver":
                if not QDesktopServices.openUrl(QUrl("https://www.nvidia.com/Download/index.aspx")):
                    self.setup_summary.setText("请打开 NVIDIA 官方驱动页面：https://www.nvidia.com/Download/index.aspx")
            elif uv := shutil.which("uv"):
                self._start_setup("python", [uv, "sync", "--locked"])
            else:
                self.setup_summary.setText("未找到 uv。请先安装 uv，再在项目目录运行 uv sync。")

    def _browse(self, field: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本机目录", field.text())
        if path:
            field.setText(path)

    def _sync_root(self) -> None:
        self._reset_checks()
        try:
            settings = self.settings()
        except ValueError:
            self.root_field.clear()
            return
        root = settings["inbox"].parent
        self.root_field.setText(str(root) if settings == settings_for_root(root) else "")

    def _meetings_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Meetings 根目录", self.root_field.text())
        if path:
            settings = settings_for_root(Path(path))
            for key in ("inbox", "work", "output"):
                self.fields[key].setText(str(settings[key]))
            for key, directory in settings["models"].items():
                self.fields[key].setText(str(directory))

    def _model_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择包含三个版本化模型的根目录")
        if path:
            for key, name in MODEL_DIRECTORIES.items():
                self.fields[key].setText(str(Path(path) / name))

    def settings(self) -> Settings:
        if any(not field.text().strip() for field in self.fields.values()):
            raise ValueError("请填写全部目录")
        paths = {key: Path(field.text().strip()).expanduser().resolve() for key, field in self.fields.items()}
        return {"inbox": paths["inbox"], "work": paths["work"], "output": paths["output"],
                "models": {"sensevoice_dir": paths["sensevoice_dir"], "vad_dir": paths["vad_dir"], "spk_dir": paths["spk_dir"]}}

    def accept(self) -> None:
        from .pipeline import validate_settings

        if self._setup_process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            validate_settings(self.settings())
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        super().accept()

    def reject(self) -> None:
        if self._setup_process.state() != QProcess.ProcessState.NotRunning:
            self.setup_summary.setText("正在校验或安装，请等待完成；模型下载与校验可用「停止」按钮中断。")
            return
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self, config: Path) -> None:
        super().__init__()
        self.config = config.resolve()
        self.settings = load_settings(self.config) if self.config.is_file() else default_settings()
        self.jobs: list[DesktopJob] = []
        self.source: Path | None = None
        self.selected_id = ""
        self._view_key: tuple[object, ...] = ()
        self._operation = ""
        self._request = QProcess(self)
        self._request.finished.connect(self._request_finished)
        self._request.errorOccurred.connect(self._request_error)
        self.setWindowTitle("MeetingFlow · 本地会议转写")
        self.resize(1180, 820)
        self.setMinimumSize(900, 680)
        self._build()
        self._reload()
        self._refresh_environment()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._reload)
        self._timer.start(1200)
        self._recovery_timer = QTimer(self)
        self._recovery_timer.timeout.connect(self._recover)
        self._recovery_timer.start(6000)
        QTimer.singleShot(0, self._recover)

    def _build(self) -> None:
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(240)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(20, 28, 20, 18)
        brand = QHBoxLayout()
        brand.addWidget(WaveMark())
        brand.addWidget(label("MeetingFlow", "brand"))
        left.addLayout(brand)
        left.addWidget(label("本地会议转写工作台", "muted"))
        left.addSpacing(24)
        new_button = button("＋  新建转写", True)
        new_button.clicked.connect(self._new)
        left.addWidget(new_button)
        left.addSpacing(18)
        self.history_heading = label("最近任务", "eyebrow")
        left.addWidget(self.history_heading)
        self.history = QListWidget()
        self.history.setAccessibleName("最近转写任务")
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.currentItemChanged.connect(self._select_job)
        left.addWidget(self.history, 1)
        self.settings_button = button("设置与模型")
        self.settings_button.clicked.connect(self._settings)
        left.addWidget(self.settings_button)
        left.addWidget(label("音频与转写始终留在这台电脑。", "muted", True))
        splitter.addWidget(sidebar)

        main = QWidget()
        body = QVBoxLayout(main)
        body.setContentsMargins(32, 28, 32, 18)
        body.setSpacing(16)
        top = QHBoxLayout()
        title_group = QVBoxLayout()
        title_group.addWidget(label("把录音，变成对话。", "title"))
        title_group.addWidget(label("选择一段会议录音，剩下的交给本机处理。", "muted"))
        top.addLayout(title_group, 1)
        try:
            device = "CPU" if automodel_options()["device"] == "cpu" else "CUDA"
        except ValueError:
            device = "平台不支持"
        top.addWidget(label(f"{device} · 本地运行", "badge"), alignment=Qt.AlignmentFlag.AlignTop)
        body.addLayout(top)
        self.notice = label("", "notice", True)
        self.notice.hide()
        body.addWidget(self.notice)
        self.drop_card = DropCard()
        self.drop_card.choose.clicked.connect(self._choose_file)
        self.drop_card.file_selected.connect(self._choose_source)
        body.addWidget(self.drop_card)
        controls = QHBoxLayout()
        controls.addWidget(label("输出格式", "muted"))
        self.formats = QComboBox()
        self.formats.setAccessibleName("转写输出格式")
        self.formats.addItems(["Markdown", "SRT 字幕", "Markdown + SRT"])
        formats = output_formats(self.settings)
        self.formats.setCurrentIndex(2 if len(formats) == 2 else (1 if formats == ("srt",) else 0))
        controls.addWidget(self.formats)
        controls.addStretch()
        self.start_button = button("开始转写", True)
        self.start_button.setToolTip("先选择录音文件，并在设置中准备 FFmpeg 和模型")
        self.start_button.clicked.connect(self._submit)
        controls.addWidget(self.start_button)
        body.addLayout(controls)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 12)
        panel_top = QHBoxLayout()
        self.job_title = label("准备好后，就从第一段录音开始", "heading", True)
        panel_top.addWidget(self.job_title, 1)
        self.retry_button = button("重试任务")
        self.retry_button.clicked.connect(self._retry)
        self.retry_button.hide()
        panel_top.addWidget(self.retry_button)
        panel_layout.addLayout(panel_top)
        self.stage = label("转写完成后，可以在这里预览对话、修改发言人姓名。", "muted", True)
        panel_layout.addWidget(self.stage)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setAccessibleName("任务正在处理中")
        self.progress.hide()
        panel_layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setAccessibleName("转录文本预览")
        self.preview.setPlaceholderText("让每一段讨论，都有迹可循。\n\n完成的转录将显示在这里。")
        self.tabs.addTab(self.preview, "对话预览")
        speakers_page = QWidget()
        speakers_layout = QVBoxLayout(speakers_page)
        speakers_layout.addWidget(label("为发言人填入姓名，保存后会同步更新输出文件。", "muted", True))
        self.speakers = QComboBox()
        self.speakers.setAccessibleName("选择要改名的发言人")
        self.speakers.currentIndexChanged.connect(self._speaker_changed)
        self.speaker_name = QLineEdit()
        self.speaker_name.setPlaceholderText("发言人姓名")
        self.speaker_name.setAccessibleName("发言人的新姓名")
        self.rename_button = button("保存姓名并更新结果", True)
        self.rename_button.clicked.connect(self._rename)
        speakers_layout.addWidget(self.speakers)
        speakers_layout.addWidget(self.speaker_name)
        speakers_layout.addWidget(self.rename_button, alignment=Qt.AlignmentFlag.AlignLeft)
        speakers_layout.addStretch()
        self.tabs.addTab(speakers_page, "发言人")
        panel_layout.addWidget(self.tabs, 1)
        footer = QHBoxLayout()
        self.open_button = button("打开输出文件夹")
        self.open_button.clicked.connect(self._open_output)
        self.log_button = button("打开任务日志目录")
        self.log_button.clicked.connect(self._open_logs)
        self.log_button.hide()
        self.export_button = button("按所选格式重新导出")
        self.export_button.clicked.connect(self._render)
        footer.addWidget(self.open_button)
        footer.addWidget(self.log_button)
        footer.addStretch()
        footer.addWidget(self.export_button)
        panel_layout.addLayout(footer)
        body.addWidget(panel, 1)
        bottom = QHBoxLayout()
        self.environment = label("", "muted", True)
        bottom.addWidget(self.environment, 1)
        body.addLayout(bottom)
        splitter.addWidget(main)
        splitter.setSizes([264, 916])
        splitter.setStretchFactor(1, 1)
        self.statusBar().showMessage("就绪 · 关闭窗口后，已提交的转写任务会继续处理。")
        choose = QAction("选择录音…", self)
        choose.setShortcut("Ctrl+O")
        choose.triggered.connect(self._choose_file)
        self.addAction(choose)

    def _message(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def _refresh_environment(self) -> None:
        missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        models = sum((path / "manifest.json").is_file() for path in self.settings["models"].values())
        self.environment.setText(f"FFmpeg：{'未找到' if missing else '可用'}  ·  模型清单：{models}/3")
        self._environment_ready = not missing and models == 3
        if not self._environment_ready:
            self._message("首次使用请先在「设置与模型」中配置模型目录，并确保 FFmpeg 已安装。")
        elif self.notice.text().startswith("首次使用请先"):
            self._message("")
        self._buttons()

    def _buttons(self) -> None:
        busy = self._request.state() != QProcess.ProcessState.NotRunning
        current = self._current_job()
        active = any(job.status in {"queued", "running"} for job in self.jobs)
        self.start_button.setEnabled(self.source is not None and getattr(self, "_environment_ready", False) and not busy)
        self.start_button.setText("加入处理队列" if active else "开始转写")
        self.settings_button.setEnabled(not busy and not active)
        self.settings_button.setToolTip("请等待当前任务完成后再修改目录" if active else "配置录音、任务、输出和模型目录")
        complete = current is not None and current.status == "succeeded"
        self.rename_button.setEnabled(complete and not busy and not active and self.speakers.count() > 0)
        self.export_button.setEnabled(complete and not busy and not active)
        self.open_button.setEnabled(current is not None and current.output_dir.is_dir())
        self.retry_button.setEnabled(not busy)

    def _current_job(self) -> DesktopJob | None:
        return next((job for job in self.jobs if job.job_id == self.selected_id), None)

    def _reload(self) -> None:
        try:
            jobs = read_jobs(self.settings)
        except sqlite3.Error as error:
            self._message(f"暂时无法读取任务列表：{error}")
            return
        if jobs != self.jobs:
            self.jobs = jobs
            self.history.blockSignals(True)
            scroll = self.history.verticalScrollBar().value()
            self.history.clear()
            for job in jobs:
                status = STAGE_LABELS.get(job.stage, "处理中") if job.status == "running" else STATUS_LABELS.get(job.status, job.status)
                date = job.updated_at[:10]
                item = QListWidgetItem(f"{job.source.name}\n{status}  ·  {date}")
                item.setData(Qt.ItemDataRole.UserRole, job.job_id)
                item.setToolTip(str(job.source))
                self.history.addItem(item)
                if job.job_id == self.selected_id:
                    self.history.setCurrentItem(item)
            self.history.verticalScrollBar().setValue(scroll)
            self.history.blockSignals(False)
            self.history_heading.setText(f"最近任务  ·  {len(jobs)}")
        self._show_job()
        self._buttons()

    def _select_job(self, item: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.selected_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        self._view_key = ()
        self._show_job()
        self._buttons()

    def _show_job(self) -> None:
        job = self._current_job()
        if job is None:
            return
        result_path = job.output_dir / "result.json"
        try:
            modified = result_path.stat().st_mtime_ns if result_path.is_file() else 0
            key = (job, modified)
            if key == self._view_key:
                return
            self._view_key = key
            self.drop_card.hide()
            self.start_button.hide()
            self.job_title.setText(job.source.name)
            self.retry_button.setVisible(job.status == "failed")
            self.log_button.setVisible(job.status == "failed")
            self.export_button.setVisible(job.status == "succeeded")
            self.progress.setVisible(job.status == "running")
            text = STATUS_LABELS.get(job.status, job.status)
            if job.status == "running":
                text += f" · {STAGE_LABELS.get(job.stage, '准备处理')} · 关闭窗口后任务仍会继续"
            if job.status == "failed":
                text += f" · {job.error or '请检查运行环境及任务目录中的 run.jsonl，然后重试。'}"
            self.stage.setText(text)
            self.preview.clear()
            self.speakers.clear()
            if job.status != "succeeded":
                return
            if result_path.is_file():
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                lines: list[str] = []
                if payload.get("review_flags"):
                    lines.append("存在待人工核听标记，请结合原音频复核。\n")
                for turn in payload["turns"]:
                    seconds = int(turn["start_seconds"])
                    lines.append(f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}  {turn['speaker_name']}\n{turn['text']}\n")
                self.preview.setPlainText("\n".join(lines))
            else:
                path = job.output_dir / "speakers.md"
                self.preview.setPlainText(path.read_text(encoding="utf-8") if path.is_file() else "旧任务尚无公开结果，可重新导出。")
            for speaker, name in job_speakers(job.job_id, self.settings):
                self.speakers.addItem(f"{name} · {speaker}", (speaker, name))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            self._message(f"读取结果失败：{error}")

    def _speaker_changed(self) -> None:
        data = self.speakers.currentData()
        self.speaker_name.setText(data[1] if data else "")

    def _new(self) -> None:
        self.selected_id = ""
        self.history.setCurrentRow(-1)
        self.source = None
        self.drop_card.show()
        self.start_button.show()
        self.export_button.show()
        self.drop_card.title.setText("从一段录音开始")
        self.drop_card.detail.setText("将音频或视频拖到这里，或点击按钮选择文件。")
        self.job_title.setText("准备好后，就从第一段录音开始")
        self.stage.setText("转写完成后，可以在这里预览对话、修改发言人姓名。")
        self.preview.clear()
        self.speakers.clear()
        self.progress.hide()
        self.retry_button.hide()
        self.log_button.hide()
        self._message("")
        self._buttons()

    def _choose_file(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(self, "选择会议录音", str(self.settings["inbox"]), MEDIA_FILTER)
        if path:
            self._choose_source(path)

    def _choose_source(self, path: str) -> None:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in MEDIA_SUFFIXES:
            self._message("请选择一个可读取的音频或视频文件。")
            return
        self._new()
        self.source = source
        self.drop_card.title.setText(source.name)
        self.drop_card.detail.setText(f"{source.stat().st_size / 1024 / 1024:.1f} MB · {source.parent}")
        self._message("")
        self._buttons()

    def _persist(self, formats: bool) -> None:
        if not self.config.is_file():
            save_settings(self.config, self.settings)
        if formats:
            choices = (("md",), ("srt",), ("md", "srt"))
            save_output_formats(self.settings, choices[self.formats.currentIndex()])

    def _launch(self, operation: str, payload: dict[str, object], agent: bool = False) -> None:
        if self._request.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            self._persist(operation in {"submit", "render"})
        except (OSError, ValueError) as error:
            self._message(f"保存配置失败：{error}")
            return
        self._operation = operation
        self._message("")
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        self._request.setProcessEnvironment(environment)
        executable = sys.executable
        # Windows GUI 入口使用 pythonw；子进程需要可用的 stdin/stdout 管道。
        if Path(executable).name.lower() == "pythonw.exe":
            executable = str(Path(executable).with_name("python.exe"))
        arguments = ["-m", "meetingflow", "--config", str(self.config), "agent"] if agent else [
            "-m", "meetingflow.desktop_worker", "--config", str(self.config), operation,
        ]
        if agent:
            payload = {"schema_version": 1, "operation": operation, **payload}
        self._request.start(executable, arguments)
        self._request.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self._request.closeWriteChannel()
        self.statusBar().showMessage("正在处理，请稍候…")
        self._buttons()

    def _request_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        try:
            data = bytes(self._request.readAllStandardOutput()).decode("utf-8")
            self._request.readAllStandardError()
            response = json.loads(data)
            if not response.get("ok"):
                self._message(response.get("error", {}).get("message", "操作失败，请检查任务日志。"))
            elif "job" in response:
                if self._operation != "status":
                    self.selected_id = response["job"]["job_id"]
                if self._operation == "submit" and response["job"]["status"] == "failed":
                    job_id = response["job"]["job_id"]
                    QTimer.singleShot(0, lambda: self._launch("retry", {"job_id": job_id}, agent=True))
                warning = response["job"].get("warning")
                if warning:
                    self._message(warning["message"])
            self._reload()
            self.statusBar().showMessage("就绪 · 关闭窗口后，已提交的转写任务会继续处理。")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            self._message("本机处理进程未返回有效结果，请检查配置和任务日志。")
        self._buttons()

    def _request_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._message(f"无法启动本机处理进程：{self._request.errorString()}")
            self._buttons()

    def _submit(self) -> None:
        if self.source is not None:
            self._launch("submit", {"source": str(self.source)}, agent=True)

    def _retry(self) -> None:
        job = self._current_job()
        if job is not None:
            self._launch("submit", {"source": str(job.source)}, agent=True)

    def _recover(self) -> None:
        pending = next((job for job in self.jobs if job.status in {"queued", "running"}), None)
        if pending is not None and self._request.state() == QProcess.ProcessState.NotRunning:
            self._launch("status", {"job_id": pending.job_id}, agent=True)

    def _rename(self) -> None:
        speaker = self.speakers.currentData()
        if speaker:
            name = self.speaker_name.text().strip()
            if not name:
                self._message("发言人姓名不能为空。")
                self.speaker_name.setFocus()
                return
            self._launch("rename", {"job_id": self.selected_id, "speaker": speaker[0], "name": name})

    def _render(self) -> None:
        self._launch("render", {"job_id": self.selected_id})

    def _settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                settings = dialog.settings()
                save_settings(self.config, settings)
                self.settings = settings
                self._new()
                self._reload()
                self._refresh_environment()
            except (OSError, ValueError) as error:
                self._message(f"保存设置失败：{error}")
        self._refresh_environment()

    def _open_output(self) -> None:
        job = self._current_job()
        if job is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(job.output_dir))):
            self._message(f"无法打开文件夹：{job.output_dir}")

    def _open_logs(self) -> None:
        job = self._current_job()
        if job is not None:
            directory = self.settings["work"] / "jobs" / job.job_id
            if not directory.is_dir():
                directory = job.output_dir
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                self._message(f"无法打开任务目录：{directory}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._request.state() != QProcess.ProcessState.NotRunning:
            self._message("本机操作尚未完成，请稍候再关闭窗口。已提交的转写任务会继续在后台处理。")
            event.ignore()
        else:
            event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(description="MeetingFlow 本地桌面工作台")
    parser.add_argument("--config", type=Path, help="使用已有 TOML 配置")
    arguments = parser.parse_args()
    configure_path()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MeetingFlow")
    app.setOrganizationName("MeetingFlow")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    font = app.font()
    font.setPointSize(11)
    app.setFont(font)
    config = arguments.config
    if config is None:
        local = Path.cwd() / "config" / "meetingflow.toml"
        config = local if local.is_file() else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "meetingflow.toml"
    try:
        window = MainWindow(config)
        mark = WaveMark()
        pixmap = QPixmap(mark.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        mark.render(pixmap)
        window.setWindowIcon(QIcon(pixmap))
        window.show()
        return app.exec()
    except Exception:
        logging.getLogger(__name__).exception("桌面入口启动失败")
        QMessageBox.critical(None, "MeetingFlow 启动失败", "无法读取配置或启动桌面窗口。请检查 TOML 配置和文件访问权限。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
