from __future__ import annotations

import os
from pathlib import Path

import pytest

from meetingflow import pipeline


def test_file_lock_acquires_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"

    with pipeline._file_lock(lock_path, "busy"):
        assert lock_path.is_file()
        assert lock_path.read_text(encoding="ascii") == str(os.getpid())

    assert not lock_path.is_file()


def test_file_lock_rejects_when_held_by_live_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "held.lock"
    lock_path.write_text("99999", encoding="ascii")
    monkeypatch.setattr(pipeline, "_pid_exists", lambda pid: True)

    with pytest.raises(ValueError, match="另一个"), pipeline._file_lock(lock_path, "另一个进程占用"):
        pass


def test_file_lock_reclaims_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "stale.lock"
    lock_path.write_text("99999", encoding="ascii")
    monkeypatch.setattr(pipeline, "_pid_exists", lambda pid: False)

    with pipeline._file_lock(lock_path, "busy"):
        assert lock_path.read_text(encoding="ascii") == str(os.getpid())

    assert not lock_path.is_file()


def test_process_rejects_when_global_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = pipeline.load_settings(None)
    settings["inbox"] = tmp_path / "inbox"
    settings["work"] = tmp_path / "work"
    settings["output"] = tmp_path / "output"
    settings["work"].mkdir(parents=True)
    (settings["work"] / ".gpu.lock").write_text("99999", encoding="ascii")
    monkeypatch.setattr(pipeline, "_pid_exists", lambda pid: True)

    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    with pytest.raises(ValueError, match="另一个 MeetingFlow 正在运行"):
        pipeline.process(source, settings)


def test_empty_recent_lock_not_treated_as_stale(tmp_path: Path) -> None:
    lock_path = tmp_path / "recent.lock"
    lock_path.write_bytes(b"")
    # 刚创建的空锁在窗口内视为占用，不回收
    assert pipeline._is_stale_lock(lock_path) is False


def test_empty_old_lock_treated_as_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "old.lock"
    lock_path.write_bytes(b"")
    mtime = lock_path.stat().st_mtime
    monkeypatch.setattr(pipeline.time, "time", lambda: mtime + 100)
    assert pipeline._is_stale_lock(lock_path) is True
