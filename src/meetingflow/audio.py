from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict


class AudioProbe(TypedDict):
    format_name: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_rate: int | None
    warnings: list[str]


def ensure_ffmpeg_available() -> None:
    """启动时确认 ffmpeg 与 ffprobe 可用，避免运行中途才因缺工具失败。"""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise ValueError(f"未找到可执行程序：{', '.join(missing)}。请确认 FFmpeg 已安装并在 PATH 中。")


def probe_audio(source: Path) -> AudioProbe:
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise ValueError(f"无法读取媒体文件：{result.stderr.strip() or 'ffprobe 未提供错误详情'}")
    try:
        payload = json.loads(result.stdout)
        stream = next(item for item in payload["streams"] if item.get("codec_type") == "audio")
        duration, sample_rate, channels = float(payload["format"]["duration"]), int(stream["sample_rate"]), int(stream["channels"])
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise ValueError("媒体文件没有可用的音频流或媒体信息不完整") from error
    if duration <= 0 or sample_rate <= 0 or channels <= 0:
        raise ValueError("媒体文件的时长、采样率或声道数异常")
    warnings: list[str] = []
    if duration < 1:
        warnings.append("录音时长不足 1 秒")
    return {
        "format_name": str(payload["format"].get("format_name", "unknown")),
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "bit_rate": _as_int(payload["format"].get("bit_rate")),
        "warnings": warnings,
    }


def normalize_audio(source: Path, destination: Path) -> tuple[Path, float | None]:
    """将源文件流式转换为 16kHz 单声道 float32 WAV，并在同一次 FFmpeg 调用中检测音量。

    返回最终 WAV 路径与 max_volume（dB）；真正静音返回 None，解码失败抛出明确异常。
    流式写盘替代 capture_output，避免长会议整段 PCM 进入内存。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_f32le",
        "-af",
        "volumedetect",
        "-f",
        "wav",
        str(temporary),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        detail = result.stderr.strip() or "FFmpeg 未提供错误详情"
        raise ValueError(f"无法生成标准化音频，FFmpeg 解码失败：{detail}")
    match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", result.stderr)
    if match is None:
        temporary.unlink(missing_ok=True)
        raise ValueError("FFmpeg 未输出 max_volume，标准化音频生成失败")
    max_volume = None if match.group(1) == "-inf" else float(match.group(1))
    temporary.replace(destination)
    return destination, max_volume


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
