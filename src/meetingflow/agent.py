from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from .pipeline import Settings, _is_stale_lock, _log, _open_database, _output_dir, load_settings, process, wait_until_stable

SCHEMA_VERSION = 1
_JOB_ID_LENGTH = 64
_GPU_BUSY_MESSAGE = "另一个 MeetingFlow 正在运行，请等待其完成或关闭后重试。"
_QUEUED = "queued"
_RUNNING = "running"
_SUCCEEDED = "succeeded"
_FAILED = "failed"
_PROCESSING_FAILED = "PROCESSING_FAILED"
_SOURCE_CHANGED = "SOURCE_CHANGED"


class AgentFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class AgentRequest(TypedDict, total=False):
    schema_version: int
    operation: str
    source: str
    job_id: str


def run_agent(config_path: Path | None) -> int:
    operation = "unknown"
    try:
        request = _read_request()
        operation = request["operation"]
        settings = load_settings(config_path)
        _recover_interrupted_jobs(settings)
        warning = None
        if operation == "submit":
            job_id = _submit(Path(request["source"]), settings)
            warning = _ensure_worker(config_path, settings)
        elif operation == "status":
            job_id = request["job_id"]
            warning = _ensure_worker(config_path, settings)
        else:
            job_id = _retry(request["job_id"], settings)
            warning = _ensure_worker(config_path, settings)
        _write_response({"schema_version": 1, "ok": True, "operation": operation, "job": _job_payload(job_id, settings, warning)})
        return 0
    except AgentFailure as error:
        _write_response(
            {
                "schema_version": 1,
                "ok": False,
                "operation": operation,
                "error": {"code": error.code, "message": error.message, "retryable": error.retryable},
            }
        )
        return 2
    except (OSError, ValueError) as error:
        _write_response(
            {
                "schema_version": 1,
                "ok": False,
                "operation": operation,
                "error": {"code": "CONFIG_INVALID", "message": str(error), "retryable": False},
            }
        )
        return 2
    except Exception:
        logging.getLogger(__name__).exception("Agent 接口处理失败")
        _write_response(
            {
                "schema_version": 1,
                "ok": False,
                "operation": operation,
                "error": {"code": "INTERNAL_ERROR", "message": "MeetingFlow 内部错误。", "retryable": True},
            }
        )
        return 2


def run_worker(config_path: Path | None) -> int:
    try:
        settings = load_settings(config_path)
        return _run_worker(config_path, settings)
    except Exception:
        logging.getLogger(__name__).exception("Agent worker 运行失败")
        return 1


def _run_worker(config_path: Path | None, settings: Settings) -> int:
    lock = settings["work"] / ".agent-worker.lock"
    descriptor = _acquire_worker_lock(lock)
    if descriptor is None:
        return 0
    try:
        while _has_queued_jobs(settings):
            while _lock_active(settings["work"] / ".gpu.lock"):
                time.sleep(1)
            job = _next_queued_job(settings)
            if job is None:
                continue
            _run_queued_job(job[0], Path(job[1]), settings)
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)
    # 覆盖 worker 准备退出时 submit 已看到旧锁、因而没有启动新 worker 的窗口。
    if _has_queued_jobs(settings):
        _ensure_worker(config_path, settings)
    return 0


def _read_request() -> AgentRequest:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        raise AgentFailure("INVALID_JSON", "stdin 不是合法 JSON。") from error
    if not isinstance(value, dict):
        raise AgentFailure("INVALID_REQUEST", "请求必须是 JSON 对象。")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise AgentFailure("UNSUPPORTED_SCHEMA_VERSION", "仅支持 schema_version=1。")
    operation = value.get("operation")
    allowed = {"submit": {"schema_version", "operation", "source"}, "status": {"schema_version", "operation", "job_id"}, "retry": {"schema_version", "operation", "job_id"}}
    if operation not in allowed or set(value) != allowed[operation]:
        raise AgentFailure("INVALID_REQUEST", "请求字段与 operation 不匹配。")
    if operation == "submit":
        if not isinstance(value.get("source"), str) or not value["source"].strip():
            raise AgentFailure("INVALID_REQUEST", "source 必须是非空字符串。")
    elif not isinstance(value.get("job_id"), str) or not _valid_job_id(value["job_id"]):
        raise AgentFailure("INVALID_REQUEST", "job_id 必须是完整的 64 位 SHA-256。")
    return value  # type: ignore[return-value]


def _submit(source: Path, settings: Settings) -> str:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise AgentFailure("SOURCE_NOT_FOUND", f"找不到输入文件：{source}")
    try:
        wait_until_stable(source)
    except (OSError, ValueError) as error:
        raise AgentFailure("SOURCE_NOT_READY", str(error), retryable=True) from error
    job_id = _sha256(source)
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute("SELECT status, output_dir FROM jobs WHERE id = ?", (job_id,)).fetchone()
        now = datetime.now(UTC).isoformat()
        output_dir = Path(row[1]) if row is not None else _output_dir(settings["output"], source, job_id)
        result_missing = row is not None and row[0] == _SUCCEEDED and not (output_dir / "result.json").is_file()
        database.execute(
            "INSERT INTO jobs (id, source_path, output_dir, status, submitted_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET source_path=excluded.source_path, submitted_at=COALESCE(jobs.submitted_at, excluded.submitted_at), "
            "status=CASE WHEN jobs.status='succeeded' AND ? THEN 'queued' ELSE jobs.status END, "
            "updated_at=CASE WHEN jobs.status='succeeded' AND ? THEN excluded.updated_at ELSE jobs.updated_at END",
            (job_id, str(source), str(output_dir), now, now, result_missing, result_missing),
        )
        database.commit()
    finally:
        database.close()
    return job_id


def _retry(job_id: str, settings: Settings) -> str:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute("SELECT status FROM jobs WHERE id = ? AND submitted_at IS NOT NULL", (job_id,)).fetchone()
        if row is None:
            raise AgentFailure("JOB_NOT_FOUND", "未找到 Agent 任务。")
        if row[0] != _FAILED:
            raise AgentFailure("JOB_NOT_FAILED", "只有失败任务可以重试。")
        database.execute(
            "UPDATE jobs SET status='queued', updated_at=?, error_code=NULL, error_message=NULL WHERE id=?",
            (datetime.now(UTC).isoformat(), job_id),
        )
        database.commit()
    finally:
        database.close()
    return job_id


def _job_payload(job_id: str, settings: Settings, warning: dict[str, str] | None = None) -> dict[str, object]:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute(
            "SELECT status, output_dir, submitted_at, updated_at, error_code, error_message FROM jobs WHERE id = ? AND submitted_at IS NOT NULL",
            (job_id,),
        ).fetchone()
    finally:
        database.close()
    if row is None:
        raise AgentFailure("JOB_NOT_FOUND", "未找到 Agent 任务。")
    result = Path(row[1]) / "result.json"
    payload: dict[str, object] = {
        "job_id": job_id,
        "status": row[0],
        "submitted_at": row[2],
        "updated_at": row[3],
        "result_path": str(result.resolve()) if row[0] == _SUCCEEDED and result.is_file() else None,
    }
    if row[0] == _FAILED:
        payload["error"] = {"code": row[4] or _PROCESSING_FAILED, "message": row[5] or "处理失败。", "retryable": True}
    if warning is not None:
        payload["warning"] = warning
    return payload


def _ensure_worker(config_path: Path | None, settings: Settings) -> dict[str, str] | None:
    if not _has_queued_jobs(settings) or _lock_active(settings["work"] / ".agent-worker.lock"):
        return None
    command = [sys.executable, "-m", "meetingflow"]
    if config_path is not None:
        command.extend(("--config", str(config_path)))
    command.append("--agent-worker")
    try:
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
    except OSError:
        return {"code": "WORKER_START_FAILED", "message": "后台处理进程启动失败，下次 Agent 调用会重试。"}
    return None


def _recover_interrupted_jobs(settings: Settings) -> None:
    lock = settings["work"] / ".agent-worker.lock"
    descriptor = _acquire_worker_lock(lock)
    if descriptor is None:
        return
    try:
        if _lock_active(settings["work"] / ".gpu.lock"):
            return
        database = _open_database(settings["work"] / "meetingflow.db")
        try:
            database.execute(
                "UPDATE jobs SET status='queued', updated_at=? WHERE status='running' AND submitted_at IS NOT NULL",
                (datetime.now(UTC).isoformat(),),
            )
            database.commit()
        finally:
            database.close()
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def _next_queued_job(settings: Settings) -> tuple[str, str] | None:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute(
            "SELECT id, source_path FROM jobs WHERE status='queued' AND submitted_at IS NOT NULL ORDER BY submitted_at, id LIMIT 1"
        ).fetchone()
        if row is not None:
            database.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), row[0]))
            database.commit()
            return str(row[0]), str(row[1])
        return None
    finally:
        database.close()


def _run_queued_job(job_id: str, source: Path, settings: Settings) -> None:
    code = _PROCESSING_FAILED
    message = "处理失败。"
    failure_traceback: str | None = None
    try:
        if not source.is_file() or _sha256(source) != job_id:
            raise AgentFailure(_SOURCE_CHANGED, "源文件在提交后发生变化。")
        while _lock_active(settings["work"] / ".gpu.lock"):
            time.sleep(1)
        while True:
            try:
                process(source, settings)
                return
            except ValueError as error:
                if str(error) != _GPU_BUSY_MESSAGE:
                    raise
                time.sleep(1)
    except AgentFailure as error:
        code, message = error.code, error.message
        failure_traceback = traceback.format_exc()
    except Exception:
        logging.getLogger(__name__).exception("Agent 任务处理失败")
        failure_traceback = traceback.format_exc()
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        database.execute(
            "UPDATE jobs SET status='failed', updated_at=?, error_code=?, error_message=? WHERE id=?",
            (datetime.now(UTC).isoformat(), code, message, job_id),
        )
        database.commit()
    finally:
        database.close()
    artifact_dir = settings["work"] / "jobs" / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _log(artifact_dir, "job_failed", job_id=job_id, error_code=code, error_message=message, traceback=failure_traceback)


def _has_queued_jobs(settings: Settings) -> bool:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        return database.execute("SELECT 1 FROM jobs WHERE status='queued' AND submitted_at IS NOT NULL LIMIT 1").fetchone() is not None
    finally:
        database.close()


def _lock_active(path: Path) -> bool:
    if not path.exists():
        return False
    if _is_stale_lock(path):
        path.unlink(missing_ok=True)
        return False
    return True


def _acquire_worker_lock(path: Path) -> int | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if not _is_stale_lock(path):
            return None
        path.unlink(missing_ok=True)
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    return descriptor


def _valid_job_id(value: str) -> bool:
    return len(value) == _JOB_ID_LENGTH and all(character in "0123456789abcdef" for character in value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_response(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
