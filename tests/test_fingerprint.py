from __future__ import annotations

import json
from pathlib import Path

import pytest

from meetingflow import audio, pipeline


def _settings(tmp_path: Path) -> pipeline.Settings:
    settings = pipeline.load_settings(None)
    settings["inbox"] = tmp_path / "inbox"
    settings["work"] = tmp_path / "work"
    settings["output"] = tmp_path / "output"
    settings["models"] = _fake_model_dirs(tmp_path)  # type: ignore[assignment]
    return settings


def _fake_model_dirs(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "models"
    manifest = {"model": "test", "revision": "test", "files": []}
    dirs: dict[str, Path] = {
        "sensevoice_dir": root / "sensevoice",
        "vad_dir": root / "vad",
        "spk_dir": root / "speaker",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return dirs


def _analysis(text: str = "你好世界") -> dict[str, object]:
    return {
        "format": "sensevoice-analysis-v1",
        "text": text,
        "sentence_info": [{"start": 0, "end": 1000, "spk": 0, "sentence": text}],
        "sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": text}],
    }


def _install_models(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> None:
    def probe(_source: Path) -> dict[str, object]:
        calls["probe"] += 1
        return {"format_name": "mp4", "duration_seconds": 2.0, "sample_rate": 48000, "channels": 2, "bit_rate": 128000, "warnings": []}

    def normalize(_source: Path, dest: Path) -> tuple[Path, float | None]:
        calls["normalize"] += 1
        dest.write_bytes(b"fake-wav")
        return dest, -3.0

    def analyze_fn(_source: Path, _settings: object) -> dict[str, object]:
        calls["analyze"] += 1
        return _analysis()

    monkeypatch.setattr(pipeline, "probe_audio", probe)
    monkeypatch.setattr(pipeline, "normalize_audio", normalize)
    monkeypatch.setattr(pipeline, "analyze", analyze_fn)


def test_transcription_manifest_change_reruns_transcribe_and_downstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "analyze": 1}

    # 改 manifest 内容 -> 转写指纹变 -> analyze 重跑；首次成功后 WAV 已删，分析需要输入故 normalize 重建，probe 仍复用
    manifest = settings["models"]["sensevoice_dir"] / "manifest.json"
    manifest.write_text(json.dumps({"model": "test", "revision": "changed", "files": []}, ensure_ascii=False), encoding="utf-8")
    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 2, "analyze": 2}


def test_normalize_fingerprint_change_reruns_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """标准化规则变化后必须重建 WAV 并重新分析，不能把旧分析标成兼容新规则。"""
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "analyze": 1}

    monkeypatch.setattr(pipeline, "_normalize_fingerprint", lambda: "changed-normalize-contract")
    pipeline.process(source, settings)

    assert calls == {"probe": 1, "normalize": 2, "analyze": 2}


def test_retry_from_diarize_reruns_only_diarize_without_series_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "analyze": 1}

    # retry --from diarize：仅派生重跑，不重新分析；标准化 WAV 已在首次成功后清理，下游无需音频故不重建
    pipeline.retry(result.job_id[:8], "diarize", settings)
    assert calls == {"probe": 1, "normalize": 1, "analyze": 1}
    artifact_dir = settings["work"] / "jobs" / result.job_id
    assert (artifact_dir / "transcript.raw.json").is_file()
    assert (artifact_dir / "speakers.json").is_file()


def test_retry_from_transcribe_reruns_transcribe_and_downstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    pipeline.retry(result.job_id[:8], "transcribe", settings)
    # 重跑 transcribe 需要 WAV：首次成功后已删除 -> normalize 必须重建 -> analyze 2 次
    assert calls == {"probe": 1, "normalize": 2, "analyze": 2}


def test_missing_diarize_artifact_reruns_derive_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    artifact_dir = settings["work"] / "jobs" / result.job_id
    (artifact_dir / "speakers.json").unlink()
    (artifact_dir / "transcript.raw.json").unlink()

    second = pipeline.process(source, settings)
    # 分析产物仍在、指纹匹配 -> 仅 diarize 重新派生，不重跑 analyze
    assert second.output_dir == result.output_dir
    assert calls["analyze"] == 1
    assert (artifact_dir / "transcript.raw.json").is_file()
    assert (artifact_dir / "speakers.json").is_file()


def test_retry_from_diarize_fails_when_analysis_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """retry --from diarize 在缺少原生分析产物时必须明确失败并提示 --from transcribe，绝不重跑 GPU。"""
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    artifact_dir = settings["work"] / "jobs" / result.job_id
    (artifact_dir / "analysis.sensevoice.json").unlink()

    with pytest.raises(ValueError, match="--from transcribe"):
        pipeline.retry(result.job_id[:8], "diarize", settings)
    # 失败路径：GPU 分析没有被触发
    assert calls["analyze"] == 1


def test_retry_from_diarize_fails_when_transcribe_fingerprint_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """retry --from diarize 在分析产物存在但转录指纹与冻结不符时同样不得重跑 GPU。"""
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    # 改 sensevoice manifest 内容：转录指纹变化
    manifest = settings["models"]["sensevoice_dir"] / "manifest.json"
    manifest.write_text(json.dumps({"model": "test", "revision": "changed", "files": []}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="--from transcribe"):
        pipeline.retry(result.job_id[:8], "diarize", settings)
    assert calls["analyze"] == 1


def test_diarize_retry_failure_marks_job_and_stage_failed_and_logs_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """retry --from diarize 在缺少分析产物时：DB 任务=failed、diarize 阶段=failed、run.jsonl 含 job_failed + traceback、analyze() 未被调用。"""
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    artifact_dir = settings["work"] / "jobs" / result.job_id
    (artifact_dir / "analysis.sensevoice.json").unlink()
    job_id = result.job_id[:8]

    with pytest.raises(ValueError, match="--from transcribe"):
        pipeline.retry(job_id, "diarize", settings)

    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute("SELECT status FROM jobs WHERE id = ?", (result.job_id,)).fetchone()
        assert row[0] == "failed"
        stage_row = database.execute("SELECT status FROM stages WHERE job_id = ? AND name = 'diarize'", (result.job_id,)).fetchone()
        assert stage_row[0] == "failed"
    finally:
        database.close()

    log_lines = (artifact_dir / "run.jsonl").read_text(encoding="utf-8").splitlines()
    failed_events = [line for line in log_lines if '"event": "job_failed"' in line]
    assert failed_events, "run.jsonl 必须记录 job_failed 事件"
    last_failed = json.loads(failed_events[-1])
    assert last_failed["stage"] == "diarize"
    assert "traceback" in last_failed and "ValueError" in last_failed["traceback"]
    # analyze() 不得被调用
    assert calls["analyze"] == 1


def test_diarize_retry_succeeds_without_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """retry --from diarize 只读 JSON+渲染，不依赖 FFmpeg：将其置为不可用也应成功。"""
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    job_id = result.job_id[:8]
    # FFmpeg 不可用，diarize 重试路径不得触发 ensure_ffmpeg_available
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)

    second = pipeline.retry(job_id, "diarize", settings)
    assert second.output_dir == result.output_dir
    assert calls["analyze"] == 1  # GPU 未被再次加载


def test_repetition_flag_writes_run_jsonl_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """analyze() 返回 review_flags 后，pipeline 在 run.jsonl 写 repetition_flagged 事件。"""
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}

    def analyze_with_flag(_source: Path, _settings: object) -> dict[str, object]:
        calls["analyze"] += 1
        return _analysis_with_flag()

    monkeypatch.setattr(
        pipeline,
        "probe_audio",
        lambda _: {"format_name": "mp4", "duration_seconds": 2.0, "sample_rate": 48000, "channels": 2, "bit_rate": 128000, "warnings": []},
    )

    def fake_normalize(_source: Path, dest: Path) -> tuple[Path, float | None]:
        dest.write_bytes(b"fake-wav")
        return dest, -3.0

    monkeypatch.setattr(pipeline, "normalize_audio", fake_normalize)
    monkeypatch.setattr(pipeline, "analyze", analyze_with_flag)

    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")
    result = pipeline.process(source, settings)

    log_lines = (settings["work"] / "jobs" / result.job_id / "run.jsonl").read_text(encoding="utf-8").splitlines()
    flag_events = [line for line in log_lines if '"event": "repetition_flagged"' in line]
    assert flag_events, "analyze 返回 review_flags 后必须写 repetition_flagged 事件"
    payload = json.loads(flag_events[-1])
    assert payload["reason"] == "repetition"


def _analysis_with_flag() -> dict[str, object]:
    text = "啊" * 15
    return {
        "format": "sensevoice-analysis-v1",
        "text": text,
        "sentence_info": [{"start": 0, "end": 1000, "spk": 0, "sentence": text}],
        "sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": text}],
        "review_flags": ["repetition"],
    }


def test_legacy_job_without_fingerprint_is_not_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "analyze": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    # 清除指纹，模拟无指纹旧任务
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    database.execute("DELETE FROM stage_fingerprints WHERE job_id = ?", (result.job_id,))
    database.commit()
    database.close()

    for key in calls:
        calls[key] = 0
    pipeline.process(source, settings)
    # 无指纹视为未验证，转写与派生必须重跑
    assert calls["analyze"] == 1
