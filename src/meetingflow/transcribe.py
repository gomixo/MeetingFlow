from __future__ import annotations

import gc
import os
import shutil
import sys
from pathlib import Path
from typing import TypedDict


class TranscriptionSettings(TypedDict):
    model: str
    language: str
    compute_type: str
    batch_size: int


_DLL_DIRECTORIES: list[object] = []


def transcribe(audio_path: Path, settings: TranscriptionSettings) -> dict[str, object]:
    _register_dll_directories()
    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("阶段 2/4 · 转写")
    _progress("转写", 0)
    model = whisperx.load_model(settings["model"], device, compute_type=settings["compute_type"], language=settings["language"])
    try:
        result = model.transcribe(str(audio_path), batch_size=settings["batch_size"], language=settings["language"], progress_callback=lambda percent: _progress("转写", percent))
        _ensure_punkt_tab()
        print("\n阶段 3/4 · 词级对齐")
        _progress("词级对齐", 0)
        align_model, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
        try:
            aligned = whisperx.align(result["segments"], align_model, metadata, str(audio_path), device, return_char_alignments=False)
            _progress("词级对齐", 100)
            return aligned
        finally:
            del align_model
    finally:
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()


def _ensure_punkt_tab() -> None:
    import nltk

    try:
        nltk.data.find("tokenizers/punkt_tab/english/")
    except LookupError:
        if not nltk.download("punkt_tab", quiet=True, raise_on_error=True):
            raise RuntimeError("无法下载 WhisperX 对齐所需的 punkt_tab 资源")


def _progress(stage: str, percent: float) -> None:
    value = max(0.0, min(100.0, percent))
    width = 20
    filled = round(width * value / 100)
    print(f"\r  {stage:<10} │{'■' * filled}{'·' * (width - filled)}│ {value:5.1f}%", end="\n" if value >= 100 else "", flush=True)


def _register_dll_directories() -> None:
    if os.name != "nt" or _DLL_DIRECTORIES:
        return
    directories: list[Path] = [Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"]
    if ffmpeg := shutil.which("ffmpeg"):
        directories.append(Path(ffmpeg).parent)
    for directory in directories:
        if directory.is_dir():
            _DLL_DIRECTORIES.append(os.add_dll_directory(str(directory)))
