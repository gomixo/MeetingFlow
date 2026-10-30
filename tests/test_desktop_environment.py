from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from meetingflow import desktop_environment as environment
from meetingflow.desktop_data import settings_for_root


def test_checks_report_all_missing_components_without_stopping_at_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for_root(tmp_path / "Meetings")
    monkeypatch.setattr(environment.shutil, "which", lambda _name: None)
    monkeypatch.setattr(environment, "_register_dll_directories", lambda: None)

    def missing_module(name: str, package: str | None = None) -> types.ModuleType:
        raise ModuleNotFoundError(f"缺少 {name}")

    monkeypatch.setattr(environment.importlib, "import_module", missing_module)
    checks = {check["key"]: check for check in environment.check_environment(settings)}
    assert set(checks) == set(environment.CHECK_TITLES)
    assert checks["ffmpeg"]["action"] == "ffmpeg"
    assert checks["python"]["action"] == "python"
    assert checks["directories"]["action"] == "create"
    assert all(checks[role]["action"] == "model" for role in environment.MODEL_FIELDS)
    assert not settings["work"].exists()


def test_ffmpeg_checks_both_executables_and_models_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for_root(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: f"/fake/{name}")
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        if command[0].endswith("ffprobe"):
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(environment.subprocess, "run", run)
    monkeypatch.setattr(environment, "_register_dll_directories", lambda: None)
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(environment.importlib, "import_module", lambda _name: torch)
    roles: list[str] = []

    def verify(_settings: object, *, only_role: str) -> None:
        roles.append(only_role)

    monkeypatch.setattr(environment, "verify_models", verify)
    checks = {check["key"]: check for check in environment.check_environment(settings)}
    assert [command[0] for command in commands] == ["/fake/ffmpeg", "/fake/ffprobe"]
    assert "ffprobe" in checks["ffmpeg"]["message"]
    assert checks["python"]["status"] == "ready"
    assert roles == ["sensevoice", "vad", "speaker"]
    assert all(checks[role]["status"] == "ready" for role in roles)


def test_install_commands_use_fixed_package_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(environment.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(environment.sys, "platform", "darwin")
    assert environment.ffmpeg_install_command() == ["/tools/brew", "install", "ffmpeg"]
    monkeypatch.setattr(environment.sys, "platform", "win32")
    command = environment.ffmpeg_install_command()
    assert command is not None
    assert command[:6] == ["/tools/winget", "install", "--id", "Gyan.FFmpeg", "--exact", "--source"]
    assert "--disable-interactivity" in command
    monkeypatch.setattr(environment.shutil, "which", lambda _name: None)
    assert environment.ffmpeg_install_command() is None


def test_create_directories_preserves_existing_files(tmp_path: Path) -> None:
    settings = settings_for_root(tmp_path)
    settings["inbox"].mkdir()
    source = settings["inbox"] / "录音.wav"
    source.write_bytes(b"source-audio")
    environment.create_directories(settings)
    assert source.read_bytes() == b"source-audio"
    assert all(settings[key].is_dir() for key in ("inbox", "work", "output"))
