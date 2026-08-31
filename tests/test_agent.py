from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

from meetingflow import agent, pipeline


def _settings(tmp_path: Path) -> pipeline.Settings:
    settings = pipeline.load_settings(None)
    settings["inbox"] = tmp_path / "inbox"
    settings["work"] = tmp_path / "work"
    settings["output"] = tmp_path / "output"
    model_root = tmp_path / "models"
    for key in ("sensevoice_dir", "vad_dir", "spk_dir"):
        directory = model_root / key
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text('{"files":[]}', encoding="utf-8")
        settings["models"][key] = directory
    return settings


def test_request_contract_rejects_invalid_json_and_extra_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent.sys, "stdin", io.StringIO("{"))
    with pytest.raises(agent.AgentFailure, match="合法 JSON") as invalid:
        agent._read_request()
    assert invalid.value.code == "INVALID_JSON"

    monkeypatch.setattr(
        agent.sys,
        "stdin",
        io.StringIO(json.dumps({"schema_version": 1, "operation": "status", "job_id": "a" * 64, "extra": True})),
    )
    with pytest.raises(agent.AgentFailure) as extra:
        agent._read_request()
    assert extra.value.code == "INVALID_REQUEST"


def test_public_schemas_are_valid_json() -> None:
    root = Path(__file__).parents[1] / "schemas"
    schemas = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.schema.json"))]
    assert len(schemas) == 3
    assert all(schema["$schema"] == "https://json-schema.org/draft/2020-12/schema" for schema in schemas)
    response = next(schema for schema in schemas if schema["title"] == "MeetingFlow Agent response v1")
    assert response["oneOf"][0]["not"] == {"required": ["error"]}
    assert response["oneOf"][1]["not"] == {"required": ["job"]}


def test_agent_status_failed_is_protocol_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _settings(tmp_path)
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    job_id = "a" * 64
    try:
        database.execute(
            "INSERT INTO jobs (id, source_path, output_dir, status, submitted_at, updated_at, error_code, error_message) "
            "VALUES (?, ?, ?, 'failed', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 'PROCESSING_FAILED', '解码失败')",
            (job_id, str(tmp_path / "a.wav"), str(settings["output"] / "job")),
        )
        database.commit()
    finally:
        database.close()
    monkeypatch.setattr(agent, "load_settings", lambda _path: settings)
    monkeypatch.setattr(agent, "_ensure_worker", lambda _path, _settings: None)
    monkeypatch.setattr(agent.sys, "stdin", io.StringIO(json.dumps({"schema_version": 1, "operation": "status", "job_id": job_id})))

    assert agent.run_agent(None) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is True
    assert response["job"]["status"] == "failed"
    assert response["job"]["error"] == {"code": "PROCESSING_FAILED", "message": "解码失败", "retryable": True}


def test_database_migrates_existing_jobs_table(tmp_path: Path) -> None:
    path = tmp_path / "meetingflow.db"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, source_path TEXT NOT NULL, output_dir TEXT NOT NULL, status TEXT NOT NULL)")
        database.execute("INSERT INTO jobs VALUES ('old', 'source', 'output', 'succeeded')")

    database = pipeline._open_database(path)
    try:
        columns = {row[1] for row in database.execute("PRAGMA table_info(jobs)")}
        assert {"submitted_at", "updated_at", "error_code", "error_message"} <= columns
        assert database.execute("SELECT status FROM jobs WHERE id='old'").fetchone() == ("succeeded",)
    finally:
        database.close()


def test_submit_is_idempotent_and_failed_job_requires_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"audio")
    job_id = hashlib.sha256(b"audio").hexdigest()
    monkeypatch.setattr(agent, "wait_until_stable", lambda path: path)

    assert agent._submit(source, settings) == job_id
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        submitted_at = database.execute("SELECT submitted_at FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        database.execute("UPDATE jobs SET status='failed', error_code='PROCESSING_FAILED' WHERE id=?", (job_id,))
        database.commit()
    finally:
        database.close()

    assert agent._submit(source, settings) == job_id
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        assert database.execute("SELECT status, submitted_at FROM jobs WHERE id=?", (job_id,)).fetchone() == ("failed", submitted_at)
    finally:
        database.close()


def test_queue_is_fifo_and_retry_only_accepts_failed_jobs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute("INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES ('b', 'b.wav', 'b', 'queued', '2026-01-02')")
        database.execute("INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES ('a', 'a.wav', 'a', 'queued', '2026-01-01')")
        database.commit()
    finally:
        database.close()
    assert agent._next_queued_job(settings) == ("a", "a.wav")

    with pytest.raises(agent.AgentFailure) as running:
        agent._retry("a", settings)
    assert running.value.code == "JOB_NOT_FAILED"
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute("UPDATE jobs SET status='failed' WHERE id='a'")
        database.commit()
    finally:
        database.close()
    assert agent._retry("a", settings) == "a"


def test_worker_start_failure_returns_warning_and_keeps_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute("INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES ('a', 'a', 'a', 'queued', '2026-01-01')")
        database.commit()
    finally:
        database.close()
    monkeypatch.setattr(agent.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no process")))

    warning = agent._ensure_worker(None, settings)

    assert warning is not None and warning["code"] == "WORKER_START_FAILED"
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        assert database.execute("SELECT status FROM jobs WHERE id='a'").fetchone() == ("queued",)
    finally:
        database.close()


def test_worker_is_singleton_and_restarts_for_exit_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    lock = settings["work"] / ".agent-worker.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(str(agent.os.getpid()), encoding="ascii")
    assert agent._run_worker(None, settings) == 0

    lock.unlink()
    queued = iter([True, False, True])
    restarted: list[bool] = []
    monkeypatch.setattr(agent, "_has_queued_jobs", lambda _settings: next(queued))
    monkeypatch.setattr(agent, "_next_queued_job", lambda _settings: ("a", "a.wav"))
    monkeypatch.setattr(agent, "_run_queued_job", lambda *_args: None)
    monkeypatch.setattr(agent, "_ensure_worker", lambda _path, _settings: restarted.append(True))

    assert agent._run_worker(None, settings) == 0
    assert restarted == [True]
    assert not lock.exists()


def test_worker_leaves_job_queued_while_gpu_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute("INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES ('a', 'a.wav', 'a', 'queued', '2026-01-01')")
        database.commit()
    finally:
        database.close()
    checks = iter([True, False])
    observed: list[str] = []

    def fake_lock_active(path: Path) -> bool:
        if path.name != ".gpu.lock":
            return False
        busy = next(checks)
        database = pipeline._open_database(settings["work"] / "meetingflow.db")
        try:
            observed.append(database.execute("SELECT status FROM jobs WHERE id='a'").fetchone()[0])
        finally:
            database.close()
        return busy

    monkeypatch.setattr(agent, "_lock_active", fake_lock_active)
    monkeypatch.setattr(agent.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(agent, "_run_queued_job", lambda *_args: None)

    assert agent._run_worker(None, settings) == 0
    assert observed == ["queued", "queued"]


def test_recovery_does_not_requeue_while_gpu_is_active(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute(
            "INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES ('agent', 'a', 'a', 'running', '2026-01-01')"
        )
        database.commit()
    finally:
        database.close()
    gpu_lock = settings["work"] / ".gpu.lock"
    gpu_lock.write_text(str(agent.os.getpid()), encoding="ascii")

    agent._recover_interrupted_jobs(settings)

    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        assert database.execute("SELECT status FROM jobs WHERE id='agent'").fetchone() == ("running",)
    finally:
        database.close()


def test_recovery_only_requeues_agent_jobs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute(
            "INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES ('agent', 'a', 'a', 'running', '2026-01-01')"
        )
        database.execute("INSERT INTO jobs (id, source_path, output_dir, status) VALUES ('human', 'h', 'h', 'running')")
        database.commit()
    finally:
        database.close()

    agent._recover_interrupted_jobs(settings)

    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        assert database.execute("SELECT status FROM jobs WHERE id='agent'").fetchone() == ("queued",)
        assert database.execute("SELECT status FROM jobs WHERE id='human'").fetchone() == ("running",)
    finally:
        database.close()


def test_source_change_fails_without_processing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"changed")
    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute(
            "INSERT INTO jobs (id, source_path, output_dir, status, submitted_at) VALUES (?, ?, ?, 'running', '2026-01-01')",
            ("a" * 64, str(source), str(settings["output"] / "job")),
        )
        database.commit()
    finally:
        database.close()
    monkeypatch.setattr(agent, "process", lambda *_args: pytest.fail("process must not run"))

    agent._run_queued_job("a" * 64, source, settings)

    database = pipeline._open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute("SELECT status, error_code FROM jobs WHERE id = ?", ("a" * 64,)).fetchone()
        assert row == ("failed", "SOURCE_CHANGED")
    finally:
        database.close()
    log = (settings["work"] / "jobs" / ("a" * 64) / "run.jsonl").read_text(encoding="utf-8")
    assert '"traceback"' in log
    assert "AgentFailure" in log


def test_result_json_tracks_speaker_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "ensure_ffmpeg_available", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "probe_audio",
        lambda _source: {"format_name": "wav", "duration_seconds": 1.0, "sample_rate": 16000, "channels": 1, "bit_rate": None, "warnings": []},
    )
    monkeypatch.setattr(pipeline, "normalize_audio", lambda _source, destination: (destination.write_bytes(b"wav") and destination, -1.0))
    monkeypatch.setattr(
        pipeline,
        "analyze",
        lambda _source, _settings: {"sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": "你好"}]},
    )

    result = pipeline.process(source, settings)
    payload = json.loads((result.output_dir / "result.json").read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "job_id", "source", "media", "language", "review_flags", "turns", "artifacts"}
    assert set(payload["turns"][0]) == {"start_seconds", "end_seconds", "speaker_id", "speaker_name", "text"}
    assert payload["turns"][0]["speaker_name"] == "Speaker 1"
    pipeline.rename_speaker(result.job_id, "SPEAKER_00", "张三", settings)
    payload = json.loads((result.output_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["turns"][0]["speaker_name"] == "张三"


def test_result_json_ignores_legacy_aligned_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "ensure_ffmpeg_available", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "probe_audio",
        lambda _source: {"format_name": "wav", "duration_seconds": 1.0, "sample_rate": 16000, "channels": 1, "bit_rate": None, "warnings": []},
    )
    monkeypatch.setattr(pipeline, "normalize_audio", lambda _source, destination: (destination.write_bytes(b"wav") and destination, -1.0))
    monkeypatch.setattr(
        pipeline,
        "analyze",
        lambda _source, _settings: {"sentences": [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": "主要轮次"}]},
    )
    result = pipeline.process(source, settings)
    artifact_dir = settings["work"] / "jobs" / result.job_id
    (artifact_dir / "transcript.aligned.json").write_text(
        json.dumps({"segments": [{"start": 0, "end": 1, "text": "旧词级内容"}]}), encoding="utf-8"
    )

    pipeline.render(result.job_id, settings)

    payload = json.loads((result.output_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["turns"][0]["text"] == "主要轮次"
