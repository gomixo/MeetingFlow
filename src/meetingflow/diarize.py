from __future__ import annotations

import gc
import os
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
    import torchaudio
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
    try:
        options = {key: value for key, value in settings.items() if value is not None}
        print("阶段 4/4 · 说话人识别")
        # 复用 normalize 阶段的 16kHz 单声道 WAV，避免再次 capture_output 整段 PCM 入内存。
        waveform, sample_rate = torchaudio.load(str(audio_path))
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, hook=_progress_hook, **options)
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


def _progress_hook(step_name: str, _artifact: object, file: object = None, total: int | None = None, completed: int | None = None) -> None:
    if total is None or completed is None or total <= 0:
        return
    from .transcribe import _progress

    labels = {"segmentation": "语音分段", "speaker_counting": "统计说话人", "embeddings": "提取声纹", "clustering": "聚类"}
    _progress(labels.get(step_name, "说话人识别"), completed * 100 / total)
