from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TypedDict


class AudioProbe(TypedDict):
    format_name: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_rate: int | None
    max_volume_db: float | None
    warnings: list[str]


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
    max_volume = _max_volume(source)
    warnings: list[str] = []
    if duration < 1:
        warnings.append("录音时长不足 1 秒")
    if max_volume is None:
        warnings.append("未检测到有效音量，录音可能静音")
    elif max_volume >= -0.1:
        warnings.append("峰值接近 0 dB，录音可能削波")
    return {"format_name": str(payload["format"].get("format_name", "unknown")), "duration_seconds": duration, "sample_rate": sample_rate, "channels": channels, "bit_rate": _as_int(payload["format"].get("bit_rate")), "max_volume_db": max_volume, "warnings": warnings}


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _max_volume(source: Path) -> float | None:
    result = subprocess.run(["ffmpeg", "-v", "info", "-i", str(source), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", check=False)
    match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", result.stderr)
    return None if match is None or match.group(1) == "-inf" else float(match.group(1))
