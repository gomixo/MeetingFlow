"""桌面入口的配置与只读任务快照，不加载语音模型。"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .pipeline import Settings, _atomic_text, validate_settings

MODEL_DIRECTORIES = {
    "sensevoice_dir": "sensevoice-small-7bf4524",
    "vad_dir": "fsmn-vad-f9a8b82",
    "spk_dir": "campplus-a045b2a",
}
STATUS_LABELS = {"queued": "等待处理", "running": "处理中", "succeeded": "已完成", "failed": "处理失败"}
STAGE_LABELS = {"probe": "检查音频", "normalize": "标准化音频", "transcribe": "语音分析", "diarize": "生成对话稿"}


@dataclass(frozen=True)
class DesktopJob:
    job_id: str
    source: Path
    output_dir: Path
    status: str
    stage: str
    error: str
    updated_at: str


def default_settings() -> Settings:
    return settings_for_root(Path.home() / "Meetings")


def settings_for_root(root: Path) -> Settings:
    root = root.expanduser().resolve()
    return {
        "inbox": root / "Inbox", "work": root / "Work", "output": root / "Output",
        "models": {
            "sensevoice_dir": root / "Models" / MODEL_DIRECTORIES["sensevoice_dir"],
            "vad_dir": root / "Models" / MODEL_DIRECTORIES["vad_dir"],
            "spk_dir": root / "Models" / MODEL_DIRECTORIES["spk_dir"],
        },
    }


def save_settings(path: Path, settings: Settings) -> None:
    validate_settings(settings)
    lines = [f"{key} = {json.dumps(str(settings[key]), ensure_ascii=False)}" for key in ("inbox", "work", "output")]
    lines.extend(["", "[models]"])
    for key, field in (("sensevoice_dir", "sensevoice"), ("vad_dir", "vad"), ("spk_dir", "speaker")):
        lines.append(f"{field} = {json.dumps(str(settings['models'][key]), ensure_ascii=False)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, "\n".join(lines) + "\n")


def read_jobs(settings: Settings) -> list[DesktopJob]:
    path = settings["work"] / "meetingflow.db"
    if not path.is_file():
        return []
    database = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 200").fetchall()
        jobs: list[DesktopJob] = []
        for row in rows:
            stage = database.execute(
                "SELECT name FROM stages WHERE job_id=? ORDER BY updated_at DESC, rowid DESC LIMIT 1", (row["id"],)
            ).fetchone()
            values = dict(row)
            jobs.append(DesktopJob(
                row["id"], Path(row["source_path"]), Path(row["output_dir"]), row["status"],
                str(stage[0]) if stage else "", str(values.get("error_message") or ""), str(values.get("updated_at") or ""),
            ))
        return jobs
    finally:
        database.close()


def configure_path() -> None:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        directories = [str(Path(local) / "Microsoft" / "WinGet" / "Links")] if local else []
    else:
        directories = [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys([*directories, *os.environ.get("PATH", "").split(os.pathsep)]))
