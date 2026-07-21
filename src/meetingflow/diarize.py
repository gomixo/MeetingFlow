from __future__ import annotations

import gc
import os
import subprocess
from pathlib import Path
from typing import TypedDict

from .transcribe import _register_dll_directories


class DiarizationSettings(TypedDict):
    min_speakers: int | None
    max_speakers: int | None


class SpeakerSegment(TypedDict):
    start: float
    end: float
    speaker: str


def diarize(audio_path: Path, settings: DiarizationSettings) -> list[SpeakerSegment]:
    """用本地 pyannote 模型为时间段标记发言人。"""
    _register_dll_directories()
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise ValueError("未设置 HF_TOKEN。请在 Hugging Face 接受 speaker-diarization-community-1 条款后设置该环境变量。")
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
    try:
        options = {key: value for key, value in settings.items() if value is not None}
        print("阶段 4/4 · 说话人识别")
        # 复用 normalize 阶段的 16kHz 单声道 WAV；FFmpeg 解码到 float32，
        # torch.frombuffer 零拷贝 view，避免 torchaudio 后端缺失和 bytearray 双副本。
        waveform = _load_waveform(audio_path, torch)
        output = pipeline({"waveform": waveform, "sample_rate": 16000}, hook=_progress_hook, **options)
        annotation = output.speaker_diarization
        return [
            {"start": round(float(turn.start), 3), "end": round(float(turn.end), 3), "speaker": str(label)}
            for turn, _, label in annotation.itertracks(yield_label=True)
        ]
    finally:
        del pipeline
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _load_waveform(audio_path: Path, torch: object) -> object:
    """用 FFmpeg 解码标准化 WAV 为 float32 张量；torch.frombuffer 零拷贝，避免整段 PCM 双副本。"""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or "FFmpeg 未提供错误详情"
        raise ValueError(f"无法为说话人识别解码音频：{detail}")
    return torch.frombuffer(result.stdout, dtype=torch.float32).reshape(1, -1)


def _progress_hook(step_name: str, _artifact: object, file: object = None, total: int | None = None, completed: int | None = None) -> None:
    if total is None or completed is None or total <= 0:
        return
    from .transcribe import _progress

    labels = {"segmentation": "语音分段", "speaker_counting": "统计说话人", "embeddings": "提取声纹", "clustering": "聚类"}
    _progress(labels.get(step_name, "说话人识别"), completed * 100 / total)
