from __future__ import annotations

from pathlib import Path

import pytest

from meetingflow import pipeline


def _settings(tmp_path: Path) -> pipeline.Settings:
    settings = pipeline.load_settings(None)
    settings["inbox"] = tmp_path / "inbox"
    settings["work"] = tmp_path / "work"
    settings["output"] = tmp_path / "output"
    return settings


def _install_models(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> None:
    def probe(_source: Path) -> dict[str, object]:
        calls["probe"] += 1
        return {"format_name": "mp4", "duration_seconds": 2.0, "sample_rate": 48000, "channels": 2, "bit_rate": 128000, "warnings": []}

    def normalize(_source: Path, dest: Path) -> tuple[Path, float | None]:
        calls["normalize"] += 1
        dest.write_bytes(b"fake-wav")
        return dest, -3.0

    def transcribe(_source: Path, _settings: object) -> dict[str, object]:
        calls["transcribe"] += 1
        return {"language": "zh", "segments": [{"start": 0.0, "end": 1.0, "text": "你好世界"}]}

    def diarize(_source: Path, _settings: object) -> list[dict[str, object]]:
        calls["diarize"] += 1
        return [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    monkeypatch.setattr(pipeline, "probe_audio", probe)
    monkeypatch.setattr(pipeline, "normalize_audio", normalize)
    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "diarize", diarize)


def test_transcription_fingerprint_change_reruns_transcribe_and_downstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "transcribe": 0, "diarize": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 1, "diarize": 1}

    # 改模型 -> transcribe 指纹变 -> transcribe + diarize 重跑，probe + normalize 复用
    settings["transcription"]["model"] = "large-v2"
    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 2, "diarize": 2}


def test_diarization_fingerprint_change_reruns_only_diarize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "transcribe": 0, "diarize": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 1, "diarize": 1}

    # 改说话人数 -> diarize 指纹变 -> 只 diarize 重跑
    settings["diarization"]["min_speakers"] = 2
    settings["diarization"]["max_speakers"] = 3
    pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 1, "diarize": 2}


def test_retry_from_diarize_keeps_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "transcribe": 0, "diarize": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 1, "diarize": 1}

    # retry --from diarize：保留 probe+normalize+transcribe，只重跑 diarize
    pipeline.retry(result.job_id, "diarize", settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 1, "diarize": 2}


def test_retry_from_transcribe_reruns_transcribe_and_diarize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls = {"probe": 0, "normalize": 0, "transcribe": 0, "diarize": 0}
    _install_models(monkeypatch, calls)
    source = tmp_path / "meeting.mp4"
    source.write_bytes(b"data")

    result = pipeline.process(source, settings)
    pipeline.retry(result.job_id, "transcribe", settings)
    assert calls == {"probe": 1, "normalize": 1, "transcribe": 2, "diarize": 2}
