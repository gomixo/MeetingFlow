from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from meetingflow import transcribe as transcribe_module


def _install_fake_whisperx(monkeypatch: pytest.MonkeyPatch, calls: dict[str, dict[str, object]]) -> None:
    class FakeModel:
        def transcribe(self, audio: str, **kwargs: object) -> dict[str, object]:
            calls["transcribe"] = dict(kwargs)
            return {"language": "zh", "segments": []}

    def fake_load_model(model: str, device: str, **kwargs: object) -> object:
        calls["load_model"] = dict(kwargs)
        return FakeModel()

    def fake_load_align_model(language_code: str, device: str) -> tuple[object, dict[str, object]]:
        return object(), {}

    def fake_align(
        segments: object, align_model: object, metadata: object, audio: str, device: str, return_char_alignments: bool
    ) -> dict[str, object]:
        return {"language": "zh", "segments": []}

    fake = types.ModuleType("whisperx")
    fake.load_model = fake_load_model
    fake.load_align_model = fake_load_align_model
    fake.align = fake_align
    monkeypatch.setitem(sys.modules, "whisperx", fake)

    fake_torch = types.ModuleType("torch")
    fake_cuda = types.ModuleType("torch.cuda")
    fake_cuda.is_available = lambda: False
    fake_cuda.empty_cache = lambda: None
    fake_torch.cuda = fake_cuda
    monkeypatch.setitem(sys.modules, "torch", fake_torch)


def _settings(**overrides: object) -> dict[str, object]:
    settings: dict[str, object] = {
        "model": "large-v3",
        "language": "zh",
        "compute_type": "int8_float16",
        "batch_size": 4,
        "repetition_penalty": 1.0,
        "no_repeat_ngram_size": 0,
        "chunk_size": 30,
    }
    settings.update(overrides)
    return settings


def test_asr_options_go_to_load_model_not_transcribe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    _install_fake_whisperx(monkeypatch, calls)
    monkeypatch.setattr(transcribe_module, "_register_dll_directories", lambda: None)
    monkeypatch.setattr(transcribe_module, "_ensure_punkt_tab", lambda: None)

    transcribe_module.transcribe(tmp_path / "audio.wav", _settings(repetition_penalty=1.1, no_repeat_ngram_size=3, chunk_size=20))

    assert calls["load_model"]["asr_options"] == {"repetition_penalty": 1.1, "no_repeat_ngram_size": 3}
    assert "asr_options" not in calls["transcribe"]
    assert calls["transcribe"]["chunk_size"] == 20


def test_default_settings_pass_none_asr_options_to_load_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    _install_fake_whisperx(monkeypatch, calls)
    monkeypatch.setattr(transcribe_module, "_register_dll_directories", lambda: None)
    monkeypatch.setattr(transcribe_module, "_ensure_punkt_tab", lambda: None)

    transcribe_module.transcribe(tmp_path / "audio.wav", _settings())

    assert calls["load_model"]["asr_options"] is None
    assert "asr_options" not in calls["transcribe"]
