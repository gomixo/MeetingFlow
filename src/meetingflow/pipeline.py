from __future__ import annotations

import hashlib
import importlib.metadata
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

from .analyze import (
    ANALYSIS_FORMAT,
    AUTOMODEL_OPTIONS,
    DERIVE_FORMAT,
    GENERATE_OPTIONS,
    AnalysisSettings,
    SpeakerSegment,
    analyze,
    automodel_options,
    derive_speakers,
    derive_transcript,
    manifest_hashes,
)
from .audio import ensure_ffmpeg_available, normalize_audio, probe_audio
from .render import render_speakers_markdown, render_speakers_srt


class Settings(TypedDict):
    inbox: Path
    work: Path
    output: Path
    models: AnalysisSettings


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
    models = values.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("配置中的 models 必须是表")
    settings: Settings = {
        "inbox": _resolve_path(str(values.get("inbox", "D:/Meetings/Inbox")), base),
        "work": _resolve_path(str(values.get("work", "D:/Meetings/Work")), base),
        "output": _resolve_path(str(values.get("output", "D:/Meetings/Output")), base),
        "models": {
            "sensevoice_dir": _resolve_path(str(models.get("sensevoice", "D:/Meetings/Models/sensevoice-small-7bf4524")), base),
            "vad_dir": _resolve_path(str(models.get("vad", "D:/Meetings/Models/fsmn-vad-f9a8b82")), base),
            "spk_dir": _resolve_path(str(models.get("speaker", "D:/Meetings/Models/campplus-a045b2a")), base),
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


_STALE_LOCK_SECONDS: float = 5.0


def _is_stale_lock(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        # 空锁可能是刚创建尚未写 PID 的窗口；新锁视为占用，旧锁视为残留。
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        return age > _STALE_LOCK_SECONDS
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


def _transcription_fingerprint(settings: AnalysisSettings, options: dict[str, object] | None = None) -> str:
    effective_options = automodel_options() if options is None else options
    return _fingerprint(
        {
            "format": ANALYSIS_FORMAT,
            "funasr": _package_version("funasr"),
            "modelscope": _package_version("modelscope"),
            "manifests": manifest_hashes(settings),
            "automodel": effective_options,
            "generate": GENERATE_OPTIONS,
        }
    )


def _diarization_fingerprint() -> str:
    return _fingerprint({"format": DERIVE_FORMAT, "spk_mode": AUTOMODEL_OPTIONS["spk_mode"]})


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read_fingerprint(database: sqlite3.Connection, job_id: str, stage: str) -> str | None:
    row = database.execute("SELECT fingerprint FROM stage_fingerprints WHERE job_id = ? AND name = ?", (job_id, stage)).fetchone()
    return row[0] if row is not None else None


def _write_fingerprint(database: sqlite3.Connection, job_id: str, stage: str, fingerprint: str) -> None:
    database.execute(
        "INSERT INTO stage_fingerprints VALUES (?, ?, ?) ON CONFLICT(job_id, name) DO UPDATE SET fingerprint=excluded.fingerprint",
        (job_id, stage, fingerprint),
    )
    database.commit()


def _fingerprint_matches(database: sqlite3.Connection, job_id: str, stage: str, current: str) -> bool:
    stored = _read_fingerprint(database, job_id, stage)
    # 缺失指纹视为未验证，重跑该阶段，避免静默复用未知参数的旧结果。
    return stored is not None and stored == current


def _should_rerun(stage: str, start_stage: str | None, rerun_active: bool, artifact_exists: bool, fingerprint_match: bool) -> bool:
    if rerun_active:
        return True
    if start_stage is not None and _STAGE_ORDER[stage] >= _STAGE_ORDER[start_stage]:
        return True
    if not artifact_exists:
        return True
    return not fingerprint_match


def _all_fingerprints_match(
    database: sqlite3.Connection, job_id: str, settings: Settings, options: dict[str, object] | None = None
) -> bool:
    effective_options = automodel_options() if options is None else options
    return (
        _fingerprint_matches(database, job_id, "probe", _probe_fingerprint())
        and _fingerprint_matches(database, job_id, "normalize", _normalize_fingerprint())
        and _fingerprint_matches(database, job_id, "transcribe", _transcription_fingerprint(settings["models"], effective_options))
        and _fingerprint_matches(database, job_id, "diarize", _diarization_fingerprint())
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
    # 用单元素列表持有"当前阶段"，供 _run_full_pipeline 更新并由外层 except 读取以标记 stage=failed。
    stage_holder: list[str] = ["probe"]
    with _file_lock(settings["work"] / ".gpu.lock", "另一个 MeetingFlow 正在运行，请等待其完成或关闭后重试。"):
        options = automodel_options()
        database = _open_database(settings["work"] / "meetingflow.db")
        try:
            existing = database.execute("SELECT status, output_dir FROM jobs WHERE id = ?", (job_id,)).fetchone()
            output_dir = Path(existing[1]) if existing is not None else _output_dir(settings["output"], source, job_id)
            artifact_dir = _artifact_dir(job_id, output_dir, settings)
            reusable = all((artifact_dir / name).is_file() for name in ("transcript.raw.json", "speakers.json"))
            if (
                existing is not None
                and existing[0] == "succeeded"
                and start_stage is None
                and reusable
                and _all_fingerprints_match(database, job_id, settings, options)
            ):
                _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
                _write_public_result(job_id, source, artifact_dir, output_dir)
                _job(database, job_id, source, output_dir, "succeeded")
                _log(artifact_dir, "job_skipped", job_id=job_id)
                return ProcessResult(job_id, output_dir, True)
            output_dir.mkdir(parents=True, exist_ok=True)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            _job(database, job_id, source, output_dir, "running")
            _log(artifact_dir, "job_started", job_id=job_id)
            try:
                if start_stage == "diarize":
                    # retry --from diarize：仅基于已有原生分析重新派生与渲染，绝不重跑 GPU，也不动上游。
                    # 缺少分析产物或转录指纹与冻结不符时，要求从 transcribe 重试（模型已变更或产物缺失）。
                    # 该入口不依赖 FFmpeg：只读 JSON + 渲染。
                    stage_holder[0] = "diarize"
                    _read_analysis(artifact_dir)  # 缺失/格式异常直接抛出明确错误
                    transcribe_fp = _transcription_fingerprint(settings["models"], options)
                    if not _fingerprint_matches(database, job_id, "transcribe", transcribe_fp):
                        raise ValueError(
                            "原生分析产物与当前模型配置不符（转录指纹变更），retry --from diarize 无法重跑 GPU 模型。"
                            "请执行 retry <job-id> --from transcribe 重新生成分析产物。"
                        )
                    _run_diarize_derive(artifact_dir, output_dir, settings, database, job_id)
                else:
                    # 完整流水线：需要 FFmpeg（媒体探测 + 标准化）。
                    ensure_ffmpeg_available()
                    _run_full_pipeline(database, job_id, source, output_dir, artifact_dir, settings, start_stage, stage_holder, options)
                _cleanup_wav(artifact_dir)
                _write_public_result(job_id, source, artifact_dir, output_dir)
            except Exception:
                _stage(database, job_id, stage_holder[0], "failed")
                _job(database, job_id, source, output_dir, "failed")
                _log(artifact_dir, "job_failed", job_id=job_id, stage=stage_holder[0], traceback=traceback.format_exc())
                raise
            _job(database, job_id, source, output_dir, "succeeded")
            _log(artifact_dir, "job_succeeded", job_id=job_id)
            return ProcessResult(job_id, output_dir, False)
        finally:
            database.close()


def _run_full_pipeline(
    database: sqlite3.Connection,
    job_id: str,
    source: Path,
    output_dir: Path,
    artifact_dir: Path,
    settings: Settings,
    start_stage: str | None,
    stage_holder: list[str],
    options: dict[str, object],
) -> None:
    """完整四阶段流水线：probe → normalize → transcribe → diarize。stage_holder 报告当前阶段供外层失败收尾。"""
    rerun_active = False
    stage_holder[0] = "probe"
    source_path = artifact_dir / "source.json"
    started = time.monotonic()
    _stage(database, job_id, stage_holder[0], "running")
    probe_rerun = _should_rerun(
        stage_holder[0],
        start_stage,
        rerun_active,
        source_path.is_file(),
        _fingerprint_matches(database, job_id, stage_holder[0], _probe_fingerprint()),
    )
    if probe_rerun:
        rerun_active = True
        probe = probe_audio(source)
        _atomic_json(
            source_path,
            {
                "path": str(source),
                "sha256": job_id,
                "size": source.stat().st_size,
                "mtime": source.stat().st_mtime,
                "media": probe,
            },
        )
    else:
        print("阶段 0/4 · 复用媒体探测")
        probe = json.loads(source_path.read_text(encoding="utf-8")).get("media")
        if not isinstance(probe, dict):
            raise ValueError("媒体探测产物损坏。请执行 retry <job-id> --from probe 重新生成。")
    _write_fingerprint(database, job_id, stage_holder[0], _probe_fingerprint())
    _stage(database, job_id, stage_holder[0], "succeeded")
    _log(artifact_dir, "stage_succeeded", stage=stage_holder[0], elapsed_seconds=round(time.monotonic() - started, 3))
    wav_path = artifact_dir / "audio-16k-mono.wav"
    analysis_path = artifact_dir / "analysis.sensevoice.json"
    raw_path = artifact_dir / "transcript.raw.json"
    speakers_path = artifact_dir / "speakers.json"
    normalize_fp = _normalize_fingerprint()
    transcribe_fp = _transcription_fingerprint(settings["models"], options)
    stage_holder[0] = "normalize"
    started = time.monotonic()
    _stage(database, job_id, stage_holder[0], "running")
    normalize_fingerprint_match = _fingerprint_matches(database, job_id, stage_holder[0], normalize_fp)
    # 标准化契约变化属于转录输入变化，必须使语音分析及下游失效。
    transcribe_rerun = _should_rerun(
        "transcribe",
        start_stage,
        rerun_active or not normalize_fingerprint_match,
        analysis_path.is_file(),
        _fingerprint_matches(database, job_id, "transcribe", transcribe_fp),
    )
    normalize_rerun = _should_rerun(
        stage_holder[0],
        start_stage,
        rerun_active,
        wav_path.is_file(),
        normalize_fingerprint_match,
    )
    # 标准化 WAV 只被语音分析消费；分析可复用且 WAV 已清理时不重建。
    if normalize_rerun and (transcribe_rerun or wav_path.is_file()):
        rerun_active = True
        print("阶段 1/4 · 标准化音频")
        _, max_volume = normalize_audio(source, wav_path)
        _atomic_json(
            source_path,
            {
                "path": str(source),
                "sha256": job_id,
                "size": source.stat().st_size,
                "mtime": source.stat().st_mtime,
                "media": probe,
                "max_volume_db": max_volume,
            },
        )
    elif wav_path.is_file():
        print("阶段 1/4 · 复用已有标准化音频")
    else:
        print("阶段 1/4 · 跳过标准化（复用已有语音分析）")
    _write_fingerprint(database, job_id, stage_holder[0], normalize_fp)
    _stage(database, job_id, stage_holder[0], "succeeded")
    _log(artifact_dir, "stage_succeeded", stage=stage_holder[0], elapsed_seconds=round(time.monotonic() - started, 3))
    analysis_parameters = _analysis_parameters(settings["models"], options)
    stage_holder[0] = "transcribe"
    started = time.monotonic()
    _stage(database, job_id, stage_holder[0], "running", json.dumps(analysis_parameters, ensure_ascii=False))
    if _should_rerun(
        stage_holder[0],
        start_stage,
        rerun_active,
        analysis_path.is_file(),
        _fingerprint_matches(database, job_id, stage_holder[0], transcribe_fp),
    ):
        rerun_active = True
        print("阶段 2/4 · 语音分析（转写 + 说话人）")
        analysis = analyze(wav_path, settings["models"], options)
        _atomic_json(analysis_path, analysis)
        # 重复风险标记：analyze() 不写 run.jsonl，由 pipeline 在落盘后记录。
        for flag in list(analysis.get("review_flags", [])):
            _log(artifact_dir, "repetition_flagged", reason=flag)
    else:
        print("阶段 2/4 · 复用已有语音分析")
    _write_fingerprint(database, job_id, stage_holder[0], transcribe_fp)
    _stage(database, job_id, stage_holder[0], "succeeded")
    _log(
        artifact_dir,
        "stage_succeeded",
        stage=stage_holder[0],
        elapsed_seconds=round(time.monotonic() - started, 3),
        parameters=analysis_parameters,
    )
    stage_holder[0] = "diarize"
    started = time.monotonic()
    derive_fp = _diarization_fingerprint()
    _stage(database, job_id, stage_holder[0], "running", json.dumps({"format": DERIVE_FORMAT}, ensure_ascii=False))
    if _should_rerun(
        stage_holder[0],
        start_stage,
        rerun_active,
        raw_path.is_file() and speakers_path.is_file(),
        _fingerprint_matches(database, job_id, stage_holder[0], derive_fp),
    ):
        print("阶段 3/4 · 派生发言轮次")
        _derive_artifacts(artifact_dir)
    else:
        print("阶段 3/4 · 复用已有发言轮次")
    _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
    _write_fingerprint(database, job_id, stage_holder[0], derive_fp)
    _stage(database, job_id, stage_holder[0], "succeeded")
    _log(
        artifact_dir,
        "stage_succeeded",
        stage=stage_holder[0],
        elapsed_seconds=round(time.monotonic() - started, 3),
        parameters={"format": DERIVE_FORMAT},
    )


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
    if (artifact_dir / "source.json").is_file():
        _write_public_result(full_job_id, _job_source(full_job_id, settings), artifact_dir, output_dir)
    elif (output_dir / "result.json").is_file():
        raise ValueError("任务缺少 source.json，无法刷新 result.json")
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
    full_job_id, output_dir = _find_job(job_id, settings)
    artifact_dir = _artifact_dir(full_job_id, output_dir, settings)
    speakers = _read_speakers(artifact_dir)
    return list(_speaker_names(speakers, artifact_dir / "speaker-map.toml").items())


def rename_speaker(job_id: str, label: str, name: str, settings: Settings) -> Path:
    name = name.strip()
    if not name:
        raise ValueError("发言人姓名不能为空")
    full_job_id, output_dir = _find_job(job_id, settings)
    artifact_dir = _artifact_dir(full_job_id, output_dir, settings)
    speakers = _read_speakers(artifact_dir)
    names = _speaker_names(speakers, artifact_dir / "speaker-map.toml")
    if label not in names:
        raise ValueError("任务中不存在该发言人")
    names[label] = name
    _write_speaker_names(artifact_dir / "speaker-map.toml", names)
    _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
    if (artifact_dir / "source.json").is_file():
        _write_public_result(full_job_id, _job_source(full_job_id, settings), artifact_dir, output_dir)
    elif (output_dir / "result.json").is_file():
        raise ValueError("任务缺少 source.json，无法刷新 result.json")
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


def _job_source(job_id: str, settings: Settings) -> Path:
    database = _open_database(settings["work"] / "meetingflow.db")
    try:
        row = database.execute("SELECT source_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        database.close()
    if row is None:
        raise ValueError("未找到任务")
    return Path(row[0])


def _artifact_dir(job_id: str, output_dir: Path, settings: Settings) -> Path:
    return output_dir if (output_dir / "transcript.raw.json").is_file() else settings["work"] / "jobs" / job_id


def _read_speakers(artifact_dir: Path) -> list[SpeakerSegment]:
    with (artifact_dir / "speakers.json").open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("任务的发言人产物格式异常")
    return [item for item in payload["segments"] if isinstance(item, dict) and {"start", "end", "speaker"} <= item.keys()]


def _analysis_parameters(settings: AnalysisSettings, options: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "sensevoice_dir": str(settings["sensevoice_dir"]),
        "vad_dir": str(settings["vad_dir"]),
        "spk_dir": str(settings["spk_dir"]),
        "automodel": automodel_options() if options is None else options,
        "generate": GENERATE_OPTIONS,
    }


def _read_analysis(artifact_dir: Path) -> dict[str, object]:
    path = artifact_dir / "analysis.sensevoice.json"
    try:
        analysis = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("缺少原生分析产物。请执行 retry <job-id> --from transcribe 重新生成。") from error
    except json.JSONDecodeError as error:
        raise ValueError("原生分析产物损坏。请执行 retry <job-id> --from transcribe 重新生成。") from error
    if not isinstance(analysis, dict) or not isinstance(analysis.get("sentences"), list):
        raise ValueError("原生分析产物格式异常。请执行 retry <job-id> --from transcribe 重新生成。")
    return analysis


def _derive_artifacts(artifact_dir: Path) -> None:
    analysis = _read_analysis(artifact_dir)
    _atomic_json(artifact_dir / "transcript.raw.json", derive_transcript(analysis))
    speakers = derive_speakers(analysis)
    _speaker_names(speakers, artifact_dir / "speaker-map.toml")
    _atomic_json(artifact_dir / "speakers.json", {"segments": speakers})
    # 新链路不再生成词级产物；清除旧 aligned，避免渲染读到过期内容。
    (artifact_dir / "transcript.aligned.json").unlink(missing_ok=True)


def _run_diarize_derive(artifact_dir: Path, output_dir: Path, settings: Settings, database: sqlite3.Connection, job_id: str) -> None:
    """diarize 阶段：从原生分析派生段级转写与说话人轮次并渲染；无 GPU。

    用于 retry --from diarize 短路流程（必读分析→派生→渲染）。正常流水线仍以自身 stage 块执行，
    保留按指纹复用的优化（详见 process() 内 diarize 阶段）。
    """
    stage = "diarize"
    started = time.monotonic()
    derive_fp = _diarization_fingerprint()
    _stage(database, job_id, stage, "running", json.dumps({"format": DERIVE_FORMAT}, ensure_ascii=False))
    print("阶段 3/4 · 派生发言轮次")
    _derive_artifacts(artifact_dir)
    _render_outputs(artifact_dir, output_dir, output_formats(settings), include_existing=True)
    _write_fingerprint(database, job_id, stage, derive_fp)
    _stage(database, job_id, stage, "succeeded")
    _log(
        artifact_dir,
        "stage_succeeded",
        stage=stage,
        elapsed_seconds=round(time.monotonic() - started, 3),
        parameters={"format": DERIVE_FORMAT},
    )


def _cleanup_wav(artifact_dir: Path) -> None:
    """任务成功后删除标准化 WAV；需要重跑模型时从原始录音重新生成。删除失败不影响任务结果。"""
    try:
        (artifact_dir / "audio-16k-mono.wav").unlink(missing_ok=True)
    except OSError as error:
        _log(artifact_dir, "wav_cleanup_failed", error=str(error))


def _render_outputs(artifact_dir: Path, output_dir: Path, formats: tuple[str, ...], include_existing: bool = False) -> None:
    transcript = _load_transcript(artifact_dir)
    if not isinstance(transcript, dict):
        raise ValueError("任务的转写产物格式异常")
    speakers = _read_speakers(artifact_dir)
    names = _speaker_names(speakers, artifact_dir / "speaker-map.toml")
    selected = set(formats)
    if include_existing:
        selected.update(suffix for suffix in ("md", "srt") if (output_dir / f"speakers.{suffix}").is_file())
    output_dir.mkdir(parents=True, exist_ok=True)
    if "md" in selected:
        _atomic_text(output_dir / "speakers.md", render_speakers_markdown(transcript, speakers, names))
    if "srt" in selected:
        _atomic_text(output_dir / "speakers.srt", render_speakers_srt(transcript, speakers, names))


def _load_transcript(artifact_dir: Path) -> dict[str, object]:
    """新任务读段级 raw；旧任务的词级 aligned 若存在则优先读，保留旧任务读取能力。"""
    aligned = artifact_dir / "transcript.aligned.json"
    if aligned.is_file():
        with aligned.open("r", encoding="utf-8") as file:
            return json.load(file)
    with (artifact_dir / "transcript.raw.json").open("r", encoding="utf-8") as file:
        return json.load(file)


def _open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    # busy_timeout 让并发连接短暂等待而非立即抛 database is locked；
    # WAL 降低读写互斥，避免多进程读日志时阻塞写入。
    database.execute("PRAGMA busy_timeout = 5000")
    database.execute("PRAGMA journal_mode = WAL")
    database.execute(
        "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, source_path TEXT NOT NULL, output_dir TEXT NOT NULL, status TEXT NOT NULL)"
    )
    columns = {str(row[1]) for row in database.execute("PRAGMA table_info(jobs)")}
    for name in ("submitted_at", "updated_at", "error_code", "error_message"):
        if name not in columns:
            database.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")
    database.execute(
        "CREATE TABLE IF NOT EXISTS stages (job_id TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, parameters TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (job_id, name))"
    )
    database.execute(
        "CREATE TABLE IF NOT EXISTS stage_fingerprints (job_id TEXT NOT NULL, name TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY (job_id, name))"
    )
    database.commit()
    return database


def _job(database: sqlite3.Connection, job_id: str, source: Path, output_dir: Path, status: str) -> None:
    updated_at = datetime.now(UTC).isoformat()
    database.execute(
        "INSERT INTO jobs (id, source_path, output_dir, status, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET source_path=excluded.source_path, output_dir=excluded.output_dir, "
        "status=excluded.status, updated_at=excluded.updated_at, error_code=NULL, error_message=NULL",
        (job_id, str(source), str(output_dir), status, updated_at),
    )
    database.commit()


def _stage(database: sqlite3.Connection, job_id: str, name: str, status: str, parameters: str | None = None) -> None:
    database.execute(
        "INSERT INTO stages VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id, name) DO UPDATE SET status=excluded.status, parameters=COALESCE(excluded.parameters, stages.parameters), updated_at=excluded.updated_at",
        (job_id, name, status, parameters, datetime.now(UTC).isoformat()),
    )
    database.commit()


def _sha256(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
    lines = [
        "[speakers]",
        *(f"{json.dumps(label, ensure_ascii=False)} = {json.dumps(name, ensure_ascii=False)}" for label, name in names.items()),
        "",
    ]
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
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_public_result(job_id: str, source: Path, artifact_dir: Path, output_dir: Path) -> Path:
    source_payload = json.loads((artifact_dir / "source.json").read_text(encoding="utf-8"))
    transcript = json.loads((artifact_dir / "transcript.raw.json").read_text(encoding="utf-8"))
    speakers = _read_speakers(artifact_dir)
    names = _speaker_names(speakers, artifact_dir / "speaker-map.toml")
    segments = transcript.get("segments")
    if (
        not isinstance(source_payload, dict)
        or not isinstance(source_payload.get("size"), int)
        or not isinstance(source_payload.get("media"), dict)
        or not isinstance(transcript.get("language"), str)
        or not isinstance(transcript.get("review_flags", []), list)
        or not isinstance(segments, list)
        or len(segments) != len(speakers)
    ):
        raise ValueError("任务产物格式异常，无法生成 result.json")
    turns: list[dict[str, object]] = []
    for index, segment in enumerate(segments):
        speaker = speakers[index]
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("start"), (int, float))
            or not isinstance(segment.get("end"), (int, float))
            or not isinstance(segment.get("text"), str)
            or segment["start"] != speaker["start"]
            or segment["end"] != speaker["end"]
        ):
            raise ValueError("任务产物格式异常，无法生成 result.json")
        turns.append(
            {
                "start_seconds": segment.get("start"),
                "end_seconds": segment.get("end"),
                "speaker_id": speaker["speaker"],
                "speaker_name": names[speaker["speaker"]],
                "text": segment.get("text"),
            }
        )
    artifacts = {
        suffix: str(path.resolve())
        for suffix, path in (("markdown", output_dir / "speakers.md"), ("srt", output_dir / "speakers.srt"))
        if path.is_file()
    }
    path = output_dir / "result.json"
    _atomic_json(
        path,
        {
            "schema_version": 1,
            "job_id": job_id,
            "source": {
                "path": str(source.resolve()),
                "name": source.name,
                "sha256": job_id,
                "size_bytes": source_payload.get("size"),
            },
            "media": source_payload["media"],
            "language": transcript.get("language"),
            "review_flags": transcript.get("review_flags", []),
            "turns": turns,
            "artifacts": artifacts,
        },
    )
    return path


def _log(output_dir: Path, event: str, **fields: object) -> None:
    with (output_dir / "run.jsonl").open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields}, ensure_ascii=False) + "\n")
