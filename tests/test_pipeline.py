from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import types
from pathlib import Path

import pytest

from meetingflow import audio, pipeline
from meetingflow.__main__ import _input_path, _latest_media, _menu, _rename_menu
from meetingflow.audio import probe_audio


def _settings(tmp_path: Path) -> pipeline.Settings:
    settings = pipeline.load_settings(None)
    settings["inbox"] = tmp_path / "inbox"
    settings["work"] = tmp_path / "work"
    settings["output"] = tmp_path / "output"
    return settings


def _models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "probe_audio", lambda _: {"format_name": "mp4", "duration_seconds": 2.0, "sample_rate": 48000, "channels": 2, "bit_rate": 128000, "warnings": []})
    monkeypatch.setattr(pipeline, "normalize_audio", _fake_normalize)
    monkeypatch.setattr(pipeline, "transcribe", lambda _source, _settings: {"language": "zh", "segments": [{"start": 0.0, "end": 1.25, "text": " 你好，世界。 "}]})
    monkeypatch.setattr(pipeline, "diarize", lambda _source, _settings: [{"start": 0.0, "end": 1.25, "speaker": "SPEAKER_00"}])


def _fake_normalize(_source: Path, destination: Path) -> tuple[Path, float | None]:
    destination.write_bytes(b"fake-wav")
    return destination, -3.0


def test_process_keeps_only_selected_outputs_and_internal_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "会议 录音.mp4"; source.write_bytes(b"synthetic-audio")
    settings = _settings(tmp_path); _models(monkeypatch)
    result = pipeline.process(source, settings)
    artifact_dir = settings["work"] / "jobs" / result.job_id

    assert not result.skipped
    assert {path.name for path in result.output_dir.iterdir()} == {"speakers.md"}
    assert {path.name for path in artifact_dir.iterdir()} == {"run.jsonl", "source.json", "audio-16k-mono.wav", "speaker-map.toml", "speakers.json", "transcript.raw.json"}
    assert "Speaker 1: 你好，世界。" in (result.output_dir / "speakers.md").read_text(encoding="utf-8")
    assert pipeline.process(source, settings).skipped

    moved = tmp_path / "已归档.mp4"; source.rename(moved)
    assert pipeline.process(moved, settings).skipped
    assert pipeline.completed_jobs(settings)[0].source == moved.resolve()


def test_formats_and_chinese_speaker_name_rerender_markdown_and_srt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "中文会议.mp4"; source.write_bytes(b"another-audio")
    settings = _settings(tmp_path); _models(monkeypatch)
    pipeline.save_output_formats(settings, ("srt",))
    result = pipeline.process(source, settings)

    assert pipeline.output_formats(settings) == ("srt",)
    assert not (result.output_dir / "speakers.md").exists()
    assert "Speaker 1: 你好，世界。" in (result.output_dir / "speakers.srt").read_text(encoding="utf-8")
    pipeline.save_output_formats(settings, ("md", "srt"))
    pipeline.rename_speaker(result.job_id[:8], "SPEAKER_00", "张三", settings)
    assert "张三: 你好，世界。" in (result.output_dir / "speakers.md").read_text(encoding="utf-8")
    assert "张三: 你好，世界。" in (result.output_dir / "speakers.srt").read_text(encoding="utf-8")
    mapping = settings["work"] / "jobs" / result.job_id / "speaker-map.toml"
    assert '"SPEAKER_00" = "张三"' in mapping.read_text(encoding="utf-8")


def test_force_refreshes_existing_formats_and_missing_raw_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "重跑会议.mp4"; source.write_bytes(b"retry-audio")
    settings = _settings(tmp_path); _models(monkeypatch)
    result = pipeline.process(source, settings)
    pipeline.save_output_formats(settings, ("srt",))
    monkeypatch.setattr(pipeline, "transcribe", lambda _source, _settings: {"segments": [{"start": 0.0, "end": 1.0, "text": "更新内容"}]})

    pipeline.process(source, settings, force=True)
    assert "更新内容" in (result.output_dir / "speakers.md").read_text(encoding="utf-8")
    assert "更新内容" in (result.output_dir / "speakers.srt").read_text(encoding="utf-8")

    raw_path = settings["work"] / "jobs" / result.job_id / "transcript.raw.json"
    raw_path.unlink()
    assert not pipeline.process(source, settings).skipped
    assert raw_path.is_file()


def test_render_supports_legacy_flat_job_with_id_prefix(tmp_path: Path) -> None:
    settings = _settings(tmp_path); settings["work"].mkdir(parents=True)
    output_dir = settings["output"] / "legacy"; output_dir.mkdir(parents=True)
    job_id = "a" * 64; source = tmp_path / "旧会议.mp4"; source.write_bytes(b"old")
    (output_dir / "transcript.raw.json").write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "旧内容"}]}, ensure_ascii=False), encoding="utf-8")
    (output_dir / "speakers.json").write_text(json.dumps({"segments": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]}), encoding="utf-8")
    (output_dir / "speaker-map.toml").write_text('[speakers]\n"SPEAKER_00" = "李四"\n', encoding="utf-8")
    with sqlite3.connect(settings["work"] / "meetingflow.db") as database:
        database.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, source_path TEXT NOT NULL, output_dir TEXT NOT NULL, status TEXT NOT NULL)")
        database.execute("INSERT INTO jobs VALUES (?, ?, ?, 'succeeded')", (job_id, str(source), str(output_dir)))

    assert pipeline.render(job_id[:8], settings) == output_dir
    assert "李四: 旧内容" in (output_dir / "speakers.md").read_text(encoding="utf-8")


def test_latest_media_and_dragged_path(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"; inbox.mkdir()
    older = inbox / "旧.mp3"; older.write_bytes(b"1")
    ignored = inbox / "说明.txt"; ignored.write_text("newer", encoding="utf-8")
    newer = inbox / "新.mka"; newer.write_bytes(b"2")
    older.touch(); newer.touch()
    assert _latest_media(inbox) == newer
    assert _input_path(f'"{newer}"') == newer
    with pytest.raises(ValueError, match="没有输入"):
        _input_path("")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="Inbox 中没有"):
        _latest_media(tmp_path / "empty")


def test_menu_rejects_invalid_choice_and_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    choices = iter(["9", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(choices))
    _menu(_settings(tmp_path))
    assert "无效选项" in capsys.readouterr().err


def test_rename_menu_stays_in_submenus_until_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "多人会议.mp4"; source.write_bytes(b"speakers-audio")
    settings = _settings(tmp_path); _models(monkeypatch)
    monkeypatch.setattr(pipeline, "diarize", lambda _source, _settings: [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ])
    result = pipeline.process(source, settings)
    choices = iter(["x", "1", "9", "1", "", "1", "张三", "2", "李四", "0", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(choices))

    _rename_menu(settings)

    output = capsys.readouterr()
    assert output.out.count("请选择任务") == 3
    assert "张三 (SPEAKER_00)" in output.out
    assert "李四 (SPEAKER_01)" in output.out
    assert output.err.count("操作失败") == 3
    mapping = settings["work"] / "jobs" / result.job_id / "speaker-map.toml"
    assert '"SPEAKER_00" = "张三"' in mapping.read_text(encoding="utf-8")
    assert '"SPEAKER_01" = "李四"' in mapping.read_text(encoding="utf-8")


def test_run_bat_passes_local_config_when_present() -> None:
    script = (Path(__file__).parents[1] / "run.bat").read_text(encoding="utf-8")
    assert "config\\meetingflow.toml" in script
    assert "meetingflow @configArgs" in script


def test_probe_audio_reads_format_without_separate_volume(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.1", str(source)], check=True)
    probe = probe_audio(source)
    assert probe["sample_rate"] == 44100
    assert "max_volume_db" not in probe  # 音量检测已合并到 normalize 阶段
    assert "录音时长不足 1 秒" in probe["warnings"]


def test_open_database_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    database = pipeline._open_database(tmp_path / "meetingflow.db")
    try:
        busy_timeout = database.execute("PRAGMA busy_timeout").fetchone()[0]
        journal_mode = database.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        database.close()

    assert busy_timeout == 5000
    assert journal_mode == "wal"


def test_load_settings_resolves_relative_paths_against_config_dir(tmp_path: Path) -> None:
    config = tmp_path / "meetingflow.toml"
    config.write_text('inbox = "Inbox"\nwork = "Work"\noutput = "Output"\n', encoding="utf-8")

    settings = pipeline.load_settings(config)

    assert settings["inbox"] == (tmp_path / "Inbox").resolve()
    assert settings["work"] == (tmp_path / "Work").resolve()
    assert settings["output"] == (tmp_path / "Output").resolve()


def test_validate_settings_rejects_overlap_and_bad_values(tmp_path: Path) -> None:
    base = pipeline.load_settings(None)
    base["inbox"] = tmp_path / "inbox"
    base["work"] = tmp_path / "work"
    base["output"] = tmp_path / "output"

    overlap = {**base, "work": base["inbox"]}
    with pytest.raises(ValueError, match="互相包含"):
        pipeline.validate_settings(overlap)

    bad_batch = {**base, "transcription": {**base["transcription"], "batch_size": 0}}
    with pytest.raises(ValueError, match="batch_size"):
        pipeline.validate_settings(bad_batch)

    bad_speakers = {**base, "diarization": {"min_speakers": 4, "max_speakers": 2}}
    with pytest.raises(ValueError, match="max_speakers 不能小于"):
        pipeline.validate_settings(bad_speakers)

    bad_model = {**base, "transcription": {**base["transcription"], "model": ""}}
    with pytest.raises(ValueError, match="model 不能为空"):
        pipeline.validate_settings(bad_model)


def test_validate_settings_collects_multiple_errors(tmp_path: Path) -> None:
    base = pipeline.load_settings(None)
    base["inbox"] = tmp_path / "inbox"
    base["work"] = tmp_path / "inbox"  # 与 inbox 重叠
    base["output"] = tmp_path / "output"
    base["transcription"] = {**base["transcription"], "batch_size": -1, "model": ""}
    base["diarization"] = {"min_speakers": 5, "max_speakers": 2}

    with pytest.raises(ValueError) as exc_info:
        pipeline.validate_settings(base)

    message = str(exc_info.value)
    assert "互相包含" in message
    assert "batch_size" in message
    assert "model 不能为空" in message
    assert "max_speakers 不能小于" in message


def test_normalize_audio_raises_on_ffmpeg_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "fake.mp4"
    source.write_bytes(b"x")
    destination = tmp_path / "out.wav"

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="Invalid data found")

    monkeypatch.setattr(audio.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="解码失败"):
        audio.normalize_audio(source, destination)
    assert not destination.exists()


def test_ensure_ffmpeg_available_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="未找到"):
        audio.ensure_ffmpeg_available()


def test_ensure_ffmpeg_available_passes_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    audio.ensure_ffmpeg_available()


def test_wait_until_stable_returns_for_stable_file(tmp_path: Path) -> None:
    source = tmp_path / "stable.wav"
    source.write_bytes(b"data")

    result = pipeline.wait_until_stable(source, checks=2, interval=0.001, timeout=5)

    assert result == source


def test_wait_until_stable_raises_when_file_keeps_changing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "growing.wav"
    source.write_bytes(b"data")
    counter = {"n": 0}

    def fake_stat(path: object, **_kwargs: object) -> types.SimpleNamespace:
        counter["n"] += 1
        return types.SimpleNamespace(st_size=counter["n"], st_mtime=1.0)

    monkeypatch.setattr(os, "stat", fake_stat)

    with pytest.raises(ValueError, match="仍在变化"):
        pipeline.wait_until_stable(source, checks=2, interval=0.001, timeout=0.05)
