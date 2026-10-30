from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog  # noqa: E402

from meetingflow import pipeline  # noqa: E402
from meetingflow.desktop import MainWindow, SettingsDialog  # noqa: E402
from meetingflow.desktop_data import default_settings, read_jobs, save_settings, settings_for_root  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config(tmp_path: Path) -> Path:
    settings = default_settings()
    for key in ("inbox", "work", "output"):
        settings[key] = tmp_path / key
    settings["models"] = {
        "sensevoice_dir": tmp_path / "Models" / "sensevoice",
        "vad_dir": tmp_path / "Models" / "vad",
        "spk_dir": tmp_path / "Models" / "speaker",
    }
    for path in settings["models"].values():
        path.mkdir(parents=True)
        (path / "manifest.json").write_text('{"files":[]}', encoding="utf-8")
    path = tmp_path / "配置 目录" / "meetingflow.toml"
    save_settings(path, settings)
    return path


@pytest.fixture
def completed(config: Path, monkeypatch: pytest.MonkeyPatch) -> pipeline.ProcessResult:
    source = config.parent / "产品讨论 录音.wav"
    source.write_bytes(b"synthetic-audio")
    monkeypatch.setattr(pipeline, "ensure_ffmpeg_available", lambda: None)
    monkeypatch.setattr(pipeline, "probe_audio", lambda _: {
        "format_name": "wav", "duration_seconds": 2.0, "sample_rate": 16000,
        "channels": 1, "bit_rate": None, "warnings": [],
    })

    def normalize(_source: Path, destination: Path) -> tuple[Path, float | None]:
        destination.write_bytes(b"synthetic-pcm")
        return destination, -3.0

    monkeypatch.setattr(pipeline, "normalize_audio", normalize)
    monkeypatch.setattr(pipeline, "analyze", lambda *_args: {
        "format": "sensevoice-analysis-v1", "text": "合成测试内容", "review_flags": [],
        "sentences": [{"start": 0, "end": 1500, "speaker": "SPEAKER_00", "text": "合成测试内容"}],
    })
    return pipeline.process(source, pipeline.load_settings(config))


@pytest.fixture
def window(app: QApplication, config: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    monkeypatch.setattr(MainWindow, "_recover", lambda _self: None)
    view = MainWindow(config)
    view.show()
    app.processEvents()
    yield view
    view._timer.stop()
    view._recovery_timer.stop()
    if view._request.state() != QProcess.ProcessState.NotRunning:
        view._request.kill()
        view._request.waitForFinished(3000)
    view.close()
    app.processEvents()


def wait_for(app: QApplication, condition: Callable[[], bool], timeout: float = 6) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        assert time.monotonic() < deadline, "等待桌面操作超时"
        time.sleep(0.01)
    app.processEvents()


def test_config_roundtrip_and_reject_overlap(config: Path) -> None:
    settings = pipeline.load_settings(config)
    assert settings["work"].name == "work"
    assert settings["models"]["sensevoice_dir"].name == "sensevoice"
    previous = config.read_bytes()
    settings["output"] = settings["work"] / "nested"
    with pytest.raises(ValueError, match="不能互相包含"):
        save_settings(config, settings)
    assert config.read_bytes() == previous


def test_history_is_read_only_and_reports_stage(config: Path, completed: pipeline.ProcessResult) -> None:
    settings = pipeline.load_settings(config)
    jobs = read_jobs(settings)
    assert jobs[0].job_id == completed.job_id
    assert jobs[0].stage == "diarize"
    assert jobs[0].status == "succeeded"
    settings["work"] = config.parent / "never-created"
    assert read_jobs(settings) == []
    assert not settings["work"].exists()


@pytest.mark.parametrize("tools_available", [False, True])
def test_start_requires_source_and_available_tools(
    window: MainWindow, config: Path, monkeypatch: pytest.MonkeyPatch, tools_available: bool
) -> None:
    monkeypatch.setattr("meetingflow.desktop.shutil.which", lambda name: f"/synthetic/{name}" if tools_available else None)
    window._refresh_environment()
    assert not window.start_button.isEnabled()
    source = config.parent / "会议 录音.wav"
    source.write_bytes(b"audio")
    window._choose_source(str(source))
    assert window.source == source.resolve()
    assert window.drop_card.title.text() == source.name
    assert window.start_button.isEnabled() == tools_available


def test_settings_dialog_keeps_invalid_paths_visible(window: MainWindow) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    dialog.fields["output"].setText(str(window.settings["work"] / "nested"))
    dialog.accept()
    assert "不能互相包含" in dialog.error.text()
    assert dialog.fields["output"].text().endswith("nested")


def test_settings_root_picker_updates_all_six_paths_after_one_selection(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    original_config = window.config.read_bytes()
    root = tmp_path / "新位置 含空格" / "Meetings"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(root))
    dialog.root_button.click()

    assert dialog.fields["inbox"].text() == str(root / "Inbox")
    assert dialog.fields["work"].text() == str(root / "Work")
    assert dialog.fields["output"].text() == str(root / "Output")
    assert dialog.fields["sensevoice_dir"].text() == str(root / "Models" / "sensevoice-small-7bf4524")
    assert dialog.fields["vad_dir"].text() == str(root / "Models" / "fsmn-vad-f9a8b82")
    assert dialog.fields["spk_dir"].text() == str(root / "Models" / "campplus-a045b2a")
    assert dialog.root_field.text() == str(root)
    assert window.config.read_bytes() == original_config
    assert not root.exists()
    dialog.reject()
    assert window.config.read_bytes() == original_config


def test_cancel_root_picker_preserves_paths(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    previous = dialog.settings()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")
    dialog.root_button.click()
    assert dialog.settings() == previous


def test_settings_root_display_tracks_individual_overrides(window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "Meetings"
    dialog = SettingsDialog(settings_for_root(root), window.config, window)
    assert dialog.root_field.text() == str(root)
    dialog.fields["output"].setText(str(tmp_path / "custom-output"))
    assert not dialog.root_field.text()
    dialog.fields["output"].setText(str(root / "Output"))
    assert dialog.root_field.text() == str(root)


def test_save_root_setting_roundtrips_without_moving_existing_files(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = window.settings["work"] / "existing.txt"
    original.parent.mkdir(parents=True)
    original.write_text("keep", encoding="utf-8")
    root = tmp_path / "另一个根目录" / "Meetings"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(root))

    def accept_root(dialog: SettingsDialog) -> int:
        dialog.root_button.click()
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", accept_root)
    window._settings()

    assert window.settings == settings_for_root(root)
    assert pipeline.load_settings(window.config) == settings_for_root(root)
    assert original.read_text(encoding="utf-8") == "keep"
    assert not root.exists()


def test_preview_rename_and_export_run_in_subprocess(
    app: QApplication, completed: pipeline.ProcessResult, window: MainWindow
) -> None:
    window.selected_id = completed.job_id
    window._reload()
    assert "合成测试内容" in window.preview.toPlainText()
    assert not window.drop_card.isVisible()
    assert window.speakers.count() == 1
    window.speaker_name.setText("张三")
    window._rename()
    assert window._request.state() != QProcess.ProcessState.NotRunning
    assert not window.rename_button.isEnabled()
    wait_for(app, lambda: window._request.state() == QProcess.ProcessState.NotRunning)
    assert "张三" in window.preview.toPlainText()
    payload = json.loads((completed.output_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["turns"][0]["speaker_name"] == "张三"
    window.formats.setCurrentIndex(2)
    window._render()
    wait_for(app, lambda: window._request.state() == QProcess.ProcessState.NotRunning)
    assert (completed.output_dir / "speakers.srt").is_file()
    assert "张三" in (completed.output_dir / "speakers.srt").read_text(encoding="utf-8")


def test_failed_cli_job_retries_via_submit_before_retry(
    completed: pipeline.ProcessResult, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = pipeline._open_database(window.settings["work"] / "meetingflow.db")
    database.execute("UPDATE jobs SET status='failed' WHERE id=?", (completed.job_id,))
    database.commit()
    database.close()
    window.selected_id = completed.job_id
    window._reload()
    requests: list[tuple[str, dict[str, object], bool]] = []
    monkeypatch.setattr(window, "_launch", lambda operation, payload, agent=False: requests.append((operation, payload, agent)))
    window._retry()
    assert requests == [("submit", {"source": str(window.jobs[0].source)}, True)]
    assert window.retry_button.isVisible()
    assert not window.rename_button.isEnabled()


def test_new_task_can_return_to_the_same_history_item(
    completed: pipeline.ProcessResult, window: MainWindow
) -> None:
    window.history.setCurrentRow(0)
    assert window.selected_id == completed.job_id
    window._new()
    assert window.drop_card.isVisible()
    window.history.setCurrentRow(0)
    assert window.selected_id == completed.job_id
    assert not window.drop_card.isVisible()
    assert "合成测试内容" in window.preview.toPlainText()


def test_environment_check_failure_is_visible_and_window_stays_responsive(app: QApplication, window: MainWindow) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    dialog.show()
    dialog.settings_tabs.setCurrentIndex(1)
    dialog.check_button.click()
    dialog.setWindowTitle("仍可操作")
    assert not dialog.storage_page.isEnabled()
    assert not dialog.actions.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    wait_for(app, lambda: dialog._setup_process.state() == QProcess.ProcessState.NotRunning, timeout=45)
    assert dialog.windowTitle() == "仍可操作"
    assert {check["key"] for check in dialog._checks} == {"directories", "ffmpeg", "python", "sensevoice", "vad", "speaker"}
    assert "待补齐" in dialog.setup_summary.text()
    assert dialog.download_button.isVisible()
    assert dialog.storage_page.isEnabled()
    dialog.close()


def test_model_download_preserves_existing_directory_and_uses_current_paths(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    original = window.settings["models"]["sensevoice_dir"]
    previous = (original / "manifest.json").read_bytes()
    requests: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(dialog, "_start_setup", lambda operation, command=None, roles=None: requests.append((operation, roles)))
    dialog._download_models(["sensevoice"])
    assert requests == [("models", ["sensevoice"])]
    target = dialog.settings()["models"]["sensevoice_dir"]
    assert target != original
    assert target.parent == original.parent
    assert target.name == original.name + ".downloaded"
    assert not target.exists()
    assert (original / "manifest.json").read_bytes() == previous
    assert pipeline.load_settings(window.config)["models"]["sensevoice_dir"] == original


def test_download_command_uses_draft_config_and_selected_role(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    target = tmp_path / "模型 含空格" / "自定义目录"
    dialog.fields["sensevoice_dir"].setText(str(target))
    commands: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(dialog._setup_process, "start", lambda program, arguments: commands.append((program, arguments)))
    monkeypatch.setattr(dialog._setup_process, "write", lambda _data: 0)
    monkeypatch.setattr(dialog._setup_process, "closeWriteChannel", lambda: None)
    dialog._start_setup("models", roles=["sensevoice"])
    arguments = commands[0][1]
    assert "prepare-models.py" in arguments[1]
    assert arguments[-2:] == ["--roles", "sensevoice"]
    snapshot = Path(arguments[arguments.index("--config") + 1])
    assert pipeline.load_settings(snapshot)["models"]["sensevoice_dir"] == target
    assert pipeline.load_settings(window.config)["models"]["sensevoice_dir"] != target
    assert not target.exists()
    assert dialog._setup_temporary is not None
    dialog._setup_temporary.cleanup()
    dialog._setup_busy(False)


def test_environment_check_does_not_persist_draft_configuration(
    app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    dialog = SettingsDialog(window.settings, window.config, window)
    original = window.config.read_bytes()
    target = tmp_path / "draft-work"
    dialog.fields["work"].setText(str(target))
    dialog._start_setup("create-directories")
    wait_for(app, lambda: target.is_dir())
    wait_for(app, lambda: dialog._setup_process.state() == QProcess.ProcessState.NotRunning and bool(dialog._checks), timeout=45)
    assert window.config.read_bytes() == original
    assert target.is_dir()
    dialog.close()


def test_submit_runs_worker_and_reports_failure_without_changing_source(
    app: QApplication, window: MainWindow, config: Path
) -> None:
    source = config.parent / "无效但已写入完成.wav"
    original = b"synthetic-invalid-audio"
    source.write_bytes(original)
    window._choose_source(str(source))
    window._submit()
    window.setWindowTitle("提交期间仍可操作")
    wait_for(app, lambda: window._request.state() == QProcess.ProcessState.NotRunning)
    wait_for(app, lambda: any(job.status == "failed" for job in read_jobs(window.settings)))
    window._reload()
    assert window.windowTitle() == "提交期间仍可操作"
    assert source.read_bytes() == original
    assert window.jobs[0].status == "failed"
    assert window.retry_button.isVisible()
    assert window.log_button.isVisible()
    assert (window.settings["work"] / "jobs" / window.jobs[0].job_id / "run.jsonl").is_file()


def test_active_tasks_prevent_directory_switches(window: MainWindow) -> None:
    database = pipeline._open_database(window.settings["work"] / "meetingflow.db")
    database.execute(
        "INSERT INTO jobs (id, source_path, output_dir, status) VALUES (?, ?, ?, 'queued')",
        ("a" * 64, "synthetic.wav", str(window.settings["output"] / "queued")),
    )
    database.commit()
    database.close()
    window._reload()
    assert not window.settings_button.isEnabled()
    assert window.start_button.text() == "加入处理队列"


def test_submit_uses_json_not_shell_interpolation(window: MainWindow, config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = config.parent / "会议 $(echo test).wav"
    source.write_bytes(b"synthetic-audio")
    window._choose_source(str(source))
    requests: list[dict[str, object]] = []
    monkeypatch.setattr(window, "_launch", lambda _operation, payload, agent=False: requests.append(payload))
    window._submit()
    assert requests == [{"source": str(source.resolve())}]


def test_desktop_entrypoints_are_available() -> None:
    import subprocess

    result = subprocess.run([sys.executable, "-m", "meetingflow.desktop", "--help"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    assert "--config" in result.stdout
    root = Path(__file__).resolve().parents[1]
    assert "meetingflow-gui" in (root / "run.command").read_text(encoding="utf-8")
    assert "meetingflow-gui" in (root / "run-ui.bat").read_text(encoding="utf-8")
