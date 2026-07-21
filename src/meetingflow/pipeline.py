from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import tomllib
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from .audio import normalize_audio, probe_audio
from .diarize import DiarizationSettings, SpeakerSegment, diarize
from .render import render_speakers_markdown, render_speakers_srt
from .transcribe import TranscriptionSettings, transcribe


class Settings(TypedDict):
    inbox: Path
    work: Path
    output: Path
    transcription: TranscriptionSettings
    diarization: DiarizationSettings


@dataclass(frozen=True)
class ProcessResult:
    job_id: str
    output_dir: Path
    skipped: bool


@dataclass(frozen=True)
class JobSummary:
    job_id: str
    source: Path
    output_dir: Path
    modified_at: float


def load_settings(path: Path | None) -> Settings:
    values: dict[str, object] = {}
    if path is not None:
        with path.open("rb") as file:
            values = tomllib.load(file)
    base = path.resolve().parent if path is not None else None
    section = values.get("transcription", {})
    diarization = values.get("diarization", {})
    if not isinstance(section, dict):
        raise ValueError("配置中的 transcription 必须是表")
    if not isinstance(diarization, dict):
        raise ValueError("配置中的 diarization 必须是表")
    settings: Settings = {
        "inbox": _resolve_path(str(values.get("inbox", "D:/Meetings/Inbox")), base),
        "work": _resolve_path(str(values.get("work", "D:/Meetings/Work")), base),
        "output": _resolve_path(str(values.get("output", "D:/Meetings/Output")), base),
        "transcription": {
            "model": str(section.get("model", "large-v3")),
            "language": str(section.get("language", "zh")),
            "compute_type": str(section.get("compute_type", "int8_float16")),
            "batch_size": int(section.get("batch_size", 4)),
        },
        "diarization": {
            "min_speakers": _optional_int(diarization.get("min_speakers")),
            "max_speakers": _optional_int(diarization.get("max_speakers")),
        },
    }
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    """在模型加载前一次性收集并抛出所有配置问题，避免运行很久后才失败。"""
    errors: list[str] = []
    inbox, work, output = settings["inbox"], settings["work"], settings["output"]
    for first, first_name, second, second_name in (
        (inbox, "inbox", work, "work"),
        (inbox, "inbox", output, "output"),
        (work, "work", output, "output"),
    ):
        if _is_same_or_ancestor(first, second) or _is_same_or_ancestor(second, first):
            errors.append(f"{first_name} 与 {second_name} 不能互相包含")
    transcription = settings["transcription"]
    if not transcription["model"]:
        errors.append("transcription.model 不能为空")
    if not transcription["language"]:
        errors.append("transcription.language 不能为空")
    if transcription["batch_size"] <= 0:
        errors.append("transcription.batch_size 必须大于 0")
    diarization = settings["diarization"]
    min_speakers, max_speakers = diarization["min_speakers"], diarization["max_speakers"]
    if min_speakers is not None and min_speakers <= 0:
        errors.append("diarization.min_speakers 必须大于 0")
    if max_speakers is not None and max_speakers <= 0:
        errors.append("diarization.max_speakers 必须大于 0")
    if min_speakers is not None and max_speakers is not None and max_speakers < min_speakers:
        errors.append("diarization.max_speakers 不能小于 min_speakers")
    if errors:
        raise ValueError("配置无效：\n- " + "\n- ".join(errors))


def _resolve_path(raw: str, base: Path | None) -> Path:
    """绝对路径原样返回；相对路径相对配置文件目录解析；无配置文件时相对当前工作目录。"""
    path = Path(raw).expanduser()
    if path.is_absolute() or base is None:
        return path
    return (base / path).resolve()


def _is_same_or_ancestor(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@contextmanager
def _file_lock(path: Path, busy_message: str) -> Iterator[None]:
    """独占文件锁：O_EXCL 创建，进程崩溃残留时按 PID 检测回收。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = _acquire_lock_file(path, busy_message)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _acquire_lock_file(path: Path, busy_message: str) -> int:
    try:
        return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pass
    if _is_stale_lock(path):
        path.unlink(missing_ok=True)
        try:
            return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise ValueError(busy_message) from error
    raise ValueError(busy_message)


def _is_stale_lock(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return True
    return not _pid_exists(pid)


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        return _pid_exists_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_exists_windows(pid: int) -> bool:
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


_STAGE_ORDER: dict[str, int] = {"probe": 0, "normalize": 1, "transcribe": 2, "diarize": 3}


def _probe_fingerprint() -> str:
    return "probe-v1"


def _normalize_fingerprint() -> str:
    return _fingerprint({"codec": "pcm_f32le", "sample_rate": 16000, "channels": 1})


def _transcription_fingerprint(settings: TranscriptionSettings) -> str:
    return _fingerprint({"model": settings["model"], "language": settings["language"], "compute_type": settings["compute_type"], "batch_size": settings["batch_size"]})


def _diarization_fingerprint(settings: DiarizationSettings) -> str:
    return _fingerprint({"model": "pyannote/speaker-diarization-community-1", "min_speakers": settings["min_speakers"], "max_speakers": settings["max_speakers"]})


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read_fingerprint(database: sqlite3.Connection, job_id: str, stage: str) -> str | None:
    row = database.execute("SELECT fingerprint FROM stage_fingerprints WHERE job_id = ? AND name = ?", (job_id, stage)).fetchone()
    return row[0] if row is not None else None


def _write_fingerprint(database: sqlite3.Connection, job_id: str, stage: str, fingerprint: str) -> None:
    database.execute("INSERT INTO stage_fingerprints VALUES (?, ?, ?) ON CONFLICT(job_id, name) DO UPDATE SET fingerprint=excluded.fingerprint", (job_id, stage, fingerprint))
    database.commit()


def _fingerprint_matches(database: sqlite3.Connection, job_id: str, stage: str, current: str) -> bool:
    stored = _read_fingerprint(database, job_id, stage)
    # stored 为 None 表示旧任务未记录指纹，视为兼容复用，不破坏已成功任务。
    return stored is None or stored == current


def _should_rerun(stage: str, start_stage: str | None, rerun_active: bool, artifact_exists: bool, fingerprint_match: bool) -> bool:
    if rerun_active:
        return True
    if start_stage is not None and _STAGE_ORDER[stage] >= _STAGE_ORDER[start_stage]:
        return True
    if not artifact_exists:
        return True
    if not fingerprint_match:
        return True
    return False


def _all_fingerprints_match(database: sqlite3.Connection, job_id: str, settings: Settings) -> bool:
    return (
        _fingerprint_matches(database, job_id, "probe", _probe_fingerprint())
        and _fingerprint_matches(database, job_id, "normalize", _normalize_fingerprint())
        and _fingerprint_matches(database, job_id, "transcribe", _transcription_fingerprint(settings["transcription"]))
        and _fingerprint_matches(database, job_id, "diarize", _diarization_fingerprint(settings["diarization"]))
    )


def process(source: Path, settings: Settings, start_stage: str | None = None) -> ProcessResult:
    if start_stage is not None and start_stage not in _STAGE_ORDER:
        raise ValueError(f"start_stage 只能是 {', '.join(_STAGE_ORDER)} 之一")
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"找不到输入文件：{source}")
    print(f"\n开始处理：{source.name}")
    job_id = _sha256(source)
    settings["work"].mkdir(parents=True, exist_ok=True)
    with _file_lock(settings["work"] / ".gpu.lock", "另一个 MeetingFlow 正在运行，请等待其完成或关闭后重试。"), _file_lock(settings["work"] / f".lock-{job_id}", "该任务正由另一个 MeetingFlow 处理。"):
        database = _open_database(settings["work"] / "meetingflow.db")
        try:
            existing = database.execute("SELECT status, output_dir FROM jobs WHERE id = ?", (job_id,)).fetchone()
            output_dir = Path(existing[1]) if existing is not None else _output_dir(settings["output"], source, job_id)
            artifact_dir = _artifact_dir(job_id, output_dir, settings)
            reusable = all((artifact_dir / name).is_file() for name in ("transcript.raw.json", "speakers.json"))
            if existing is not None and existing[0] == "succeeded" and start_stage is None and reusable and _all_fingerprints_match(database, job_id, settings):
                _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
                _job(database, job_id, source, output_dir, "succeeded")
                _log(artifact_dir, "job_skipped", job_id=job_id)
                return ProcessResult(job_id, output_dir, True)
            output_dir.mkdir(parents=True, exist_ok=True)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            _job(database, job_id, source, output_dir, "running")
            _log(artifact_dir, "job_started", job_id=job_id)
            rerun_active = False
            stage = "probe"
            try:
                source_path = artifact_dir / "source.json"
                started = time.monotonic(); _stage(database, job_id, stage, "running")
                if _should_rerun(stage, start_stage, rerun_active, source_path.is_file(), _fingerprint_matches(database, job_id, stage, _probe_fingerprint())):
                    rerun_active = True
                    probe = probe_audio(source)
                    _atomic_json(source_path, {"path": str(source), "sha256": job_id, "size": source.stat().st_size, "mtime": source.stat().st_mtime, "media": probe})
                else:
                    print("阶段 0/4 · 复用媒体探测")
                    probe = json.loads(source_path.read_text(encoding="utf-8")).get("media")
                    if not isinstance(probe, dict):
                        raise ValueError("媒体探测产物损坏。请执行 retry <job-id> --from probe 重新生成。")
                _write_fingerprint(database, job_id, stage, _probe_fingerprint())
                _stage(database, job_id, stage, "succeeded"); _log(artifact_dir, "stage_succeeded", stage=stage, elapsed_seconds=round(time.monotonic() - started, 3))
                wav_path = artifact_dir / "audio-16k-mono.wav"
                stage = "normalize"; started = time.monotonic(); _stage(database, job_id, stage, "running")
                if _should_rerun(stage, start_stage, rerun_active, wav_path.is_file(), _fingerprint_matches(database, job_id, stage, _normalize_fingerprint())):
                    rerun_active = True
                    print("阶段 1/4 · 标准化音频")
                    _, max_volume = normalize_audio(source, wav_path)
                    _atomic_json(source_path, {"path": str(source), "sha256": job_id, "size": source.stat().st_size, "mtime": source.stat().st_mtime, "media": probe, "max_volume_db": max_volume})
                else:
                    print("阶段 1/4 · 复用已有标准化音频")
                _write_fingerprint(database, job_id, stage, _normalize_fingerprint())
                _stage(database, job_id, stage, "succeeded"); _log(artifact_dir, "stage_succeeded", stage=stage, elapsed_seconds=round(time.monotonic() - started, 3))
                raw_path = artifact_dir / "transcript.raw.json"
                stage = "transcribe"; started = time.monotonic(); _stage(database, job_id, stage, "running", json.dumps(settings["transcription"], ensure_ascii=False))
                if _should_rerun(stage, start_stage, rerun_active, raw_path.is_file(), _fingerprint_matches(database, job_id, stage, _transcription_fingerprint(settings["transcription"]))):
                    rerun_active = True
                    transcript = transcribe(wav_path, settings["transcription"])
                else:
                    print("阶段 2/4 · 复用已有转写")
                    try:
                        transcript = json.loads(raw_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError as error:
                        raise ValueError("转写产物损坏。请执行 retry <job-id> --from transcribe 重新生成。") from error
                _atomic_json(raw_path, transcript)
                _write_fingerprint(database, job_id, stage, _transcription_fingerprint(settings["transcription"]))
                _stage(database, job_id, stage, "succeeded"); _log(artifact_dir, "stage_succeeded", stage=stage, elapsed_seconds=round(time.monotonic() - started, 3), parameters=settings["transcription"])
                speakers_path = artifact_dir / "speakers.json"
                stage = "diarize"; started = time.monotonic(); _stage(database, job_id, stage, "running", json.dumps(settings["diarization"], ensure_ascii=False))
                if _should_rerun(stage, start_stage, rerun_active, speakers_path.is_file(), _fingerprint_matches(database, job_id, stage, _diarization_fingerprint(settings["diarization"]))):
                    rerun_active = True
                    speakers = diarize(wav_path, settings["diarization"])
                    _speaker_names(speakers, artifact_dir / "speaker-map.toml")
                    _atomic_json(speakers_path, {"segments": speakers})
                else:
                    print("阶段 3/4 · 复用已有说话人识别")
                _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
                _write_fingerprint(database, job_id, stage, _diarization_fingerprint(settings["diarization"]))
                _stage(database, job_id, stage, "succeeded"); _log(artifact_dir, "stage_succeeded", stage=stage, elapsed_seconds=round(time.monotonic() - started, 3), parameters=settings["diarization"])
            except Exception:
                _stage(database, job_id, stage, "failed"); _job(database, job_id, source, output_dir, "failed"); _log(artifact_dir, "job_failed", job_id=job_id, stage=stage, traceback=traceback.format_exc())
                raise
            _job(database, job_id, source, output_dir, "succeeded"); _log(artifact_dir, "job_succeeded", job_id=job_id)
            return ProcessResult(job_id, output_dir, False)
        finally:
            database.close()


def wait_until_stable(source: Path, *, checks: int = 3, interval: float = 1.0, timeout: float = 60.0) -> Path:
    """连续检查文件大小和修改时间是否稳定，避免处理 OBS 仍在写入的文件。"""
    deadline = time.monotonic() + timeout
    previous = _stat_tuple(source)
    stable_count = 0
    while time.monotonic() < deadline:
        time.sleep(interval)
        current = _stat_tuple(source)
        if current == previous:
            stable_count += 1
            if stable_count >= checks:
                _assert_not_writable(source)
                return source
        else:
            stable_count = 0
        previous = current
    raise ValueError(f"文件仍在变化，可能仍在录制：{source}")


def _stat_tuple(source: Path) -> tuple[int, float]:
    try:
        stat = source.stat()
    except OSError as error:
        raise ValueError(f"无法读取文件状态：{source}") from error
    return stat.st_size, stat.st_mtime


def _assert_not_writable(source: Path) -> None:
    # Windows 下若 OBS 仍持有写句柄，以写方式打开会触发共享冲突；其他平台跳过该检查。
    if os.name != "nt":
        return
    try:
        descriptor = os.open(str(source), os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
    except OSError as error:
        raise ValueError(f"文件被其他进程占用，可能仍在录制：{source}") from error
    os.close(descriptor)


def render(job_id: str, settings: Settings) -> Path:
    """应用 speaker-map.toml，不重新运行任何模型。"""
    full_job_id, output_dir = _find_job(job_id, settings)
    artifact_dir = _artifact_dir(full_job_id, output_dir, settings)
    _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
    _log(artifact_dir, "render_succeeded")
    return output_dir


def completed_jobs(settings: Settings) -> list[JobSummary]:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        rows = database.execute("SELECT id, source_path, output_dir FROM jobs WHERE status = 'succeeded'").fetchall()
    finally:
        database.close()
    jobs = [JobSummary(row[0], Path(row[1]), Path(row[2]), _modified_at(Path(row[1]), Path(row[2]))) for row in rows]
    return sorted(jobs, key=lambda job: job.modified_at, reverse=True)


def job_speakers(job_id: str, settings: Settings) -> list[tuple[str, str]]:
    full_job_id, output_dir = _find_job(job_id, settings); artifact_dir = _artifact_dir(full_job_id, output_dir, settings)
    speakers = _read_speakers(artifact_dir)
    return list(_speaker_names(speakers, artifact_dir / "speaker-map.toml").items())


def rename_speaker(job_id: str, label: str, name: str, settings: Settings) -> Path:
    name = name.strip()
    if not name:
        raise ValueError("发言人姓名不能为空")
    full_job_id, output_dir = _find_job(job_id, settings); artifact_dir = _artifact_dir(full_job_id, output_dir, settings)
    speakers = _read_speakers(artifact_dir); names = _speaker_names(speakers, artifact_dir / "speaker-map.toml")
    if label not in names:
        raise ValueError("任务中不存在该发言人")
    names[label] = name
    _write_speaker_names(artifact_dir / "speaker-map.toml", names)
    _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
    _log(artifact_dir, "speaker_renamed", speaker=label)
    return output_dir


def output_formats(settings: Settings) -> tuple[str, ...]:
    path = settings["work"] / "preferences.json"
    if not path.is_file():
        return ("md",)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("输出偏好文件损坏，请删除 Work/preferences.json 后重试") from error
    formats = payload.get("output_formats") if isinstance(payload, dict) else None
    if not isinstance(formats, list) or not formats or any(item not in {"md", "srt"} for item in formats):
        raise ValueError("输出偏好文件中的 output_formats 格式异常")
    return tuple(dict.fromkeys(formats))


def save_output_formats(settings: Settings, formats: tuple[str, ...]) -> None:
    if not formats or any(item not in {"md", "srt"} for item in formats):
        raise ValueError("输出格式只能是 md、srt 或两者")
    settings["work"].mkdir(parents=True, exist_ok=True)
    _atomic_json(settings["work"] / "preferences.json", {"output_formats": list(dict.fromkeys(formats))})


def retry(job_id: str, from_stage: str, settings: Settings) -> ProcessResult:
    """从指定阶段重新处理失败任务；该阶段及下游重跑，上游复用。"""
    if from_stage not in _STAGE_ORDER:
        raise ValueError("--from 只能是 probe、normalize、transcribe 或 diarize")
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        rows = database.execute("SELECT source_path FROM jobs WHERE id LIKE ?", (f"{job_id}%",)).fetchall()
    finally:
        database.close()
    if len(rows) != 1:
        raise ValueError("未找到唯一任务，请提供完整的 job-id")
    return process(Path(rows[0][0]), settings, start_stage=from_stage)


def _find_job(job_id: str, settings: Settings) -> tuple[str, Path]:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        rows = database.execute("SELECT id, output_dir FROM jobs WHERE id LIKE ? AND status = 'succeeded'", (f"{job_id}%",)).fetchall()
    finally:
        database.close()
    if len(rows) != 1:
        raise ValueError("未找到唯一的成功任务")
    return str(rows[0][0]), Path(rows[0][1])


def _artifact_dir(job_id: str, output_dir: Path, settings: Settings) -> Path:
    return output_dir if (output_dir / "transcript.raw.json").is_file() else settings["work"] / "jobs" / job_id


def _read_speakers(artifact_dir: Path) -> list[SpeakerSegment]:
    with (artifact_dir / "speakers.json").open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("任务的发言人产物格式异常")
    return [item for item in payload["segments"] if isinstance(item, dict) and {"start", "end", "speaker"} <= item.keys()]


def _render_outputs(artifact_dir: Path, output_dir: Path, formats: tuple[str, ...], include_existing: bool = False) -> None:
    with (artifact_dir / "transcript.raw.json").open("r", encoding="utf-8") as file:
        transcript = json.load(file)
    if not isinstance(transcript, dict):
        raise ValueError("任务的转写产物格式异常")
    speakers = _read_speakers(artifact_dir); names = _speaker_names(speakers, artifact_dir / "speaker-map.toml")
    selected = set(formats)
    if include_existing:
        selected.update(suffix for suffix in ("md", "srt") if (output_dir / f"speakers.{suffix}").is_file())
    output_dir.mkdir(parents=True, exist_ok=True)
    if "md" in selected:
        _atomic_text(output_dir / "speakers.md", render_speakers_markdown(transcript, speakers, names))
    if "srt" in selected:
        _atomic_text(output_dir / "speakers.srt", render_speakers_srt(transcript, speakers, names))


def _open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    # busy_timeout 让并发连接短暂等待而非立即抛 database is locked；
    # WAL 降低读写互斥，避免多进程读日志时阻塞写入。
    database.execute("PRAGMA busy_timeout = 5000")
    database.execute("PRAGMA journal_mode = WAL")
    database.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, source_path TEXT NOT NULL, output_dir TEXT NOT NULL, status TEXT NOT NULL)")
    database.execute("CREATE TABLE IF NOT EXISTS stages (job_id TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, parameters TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (job_id, name))")
    database.execute("CREATE TABLE IF NOT EXISTS stage_fingerprints (job_id TEXT NOT NULL, name TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY (job_id, name))")
    database.commit(); return database


def _job(database: sqlite3.Connection, job_id: str, source: Path, output_dir: Path, status: str) -> None:
    database.execute("INSERT INTO jobs VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET source_path=excluded.source_path, output_dir=excluded.output_dir, status=excluded.status", (job_id, str(source), str(output_dir), status)); database.commit()


def _stage(database: sqlite3.Connection, job_id: str, name: str, status: str, parameters: str | None = None) -> None:
    database.execute("INSERT INTO stages VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id, name) DO UPDATE SET status=excluded.status, parameters=COALESCE(excluded.parameters, stages.parameters), updated_at=excluded.updated_at", (job_id, name, status, parameters, datetime.now(UTC).isoformat())); database.commit()


def _sha256(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as file:
        while chunk := file.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _speaker_names(speakers: list[SpeakerSegment], path: Path) -> dict[str, str]:
    labels = sorted({segment["speaker"] for segment in speakers})
    if path.is_file():
        with path.open("rb") as file:
            values = tomllib.load(file)
        names = values.get("speakers", {})
        if not isinstance(names, dict):
            raise ValueError("speaker-map.toml 中的 speakers 必须是表")
        return {label: str(names.get(label, f"Speaker {index}")) for index, label in enumerate(labels, 1)}
    names = {label: f"Speaker {index}" for index, label in enumerate(labels, 1)}
    _write_speaker_names(path, names)
    return names


def _write_speaker_names(path: Path, names: dict[str, str]) -> None:
    lines = ["[speakers]", *(f"{json.dumps(label, ensure_ascii=False)} = {json.dumps(name, ensure_ascii=False)}" for label, name in names.items()), ""]
    _atomic_text(path, "\n".join(lines))


def _modified_at(source: Path, output_dir: Path) -> float:
    try:
        return source.stat().st_mtime
    except OSError:
        try:
            return output_dir.stat().st_mtime
        except OSError:
            return 0.0


def _output_dir(root: Path, source: Path, job_id: str) -> Path:
    name = re.sub(r'[<>:"/\\|?*]', "_", source.stem).strip(". ") or "meeting"
    return root / f"{datetime.now():%Y-%m-%d}_{name}_{job_id[:8]}"


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _atomic_text(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file: file.write(content)
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True); raise


def _log(output_dir: Path, event: str, **fields: object) -> None:
    with (output_dir / "run.jsonl").open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields}, ensure_ascii=False) + "\n")
