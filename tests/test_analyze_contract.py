from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from meetingflow import analyze as analyze_module
from meetingflow.analyze import manifest_hashes, verify_models


def _install_fake_funasr(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, dict[str, object]], sentence_info: list | None = None, full_text: str = "raw-text"
) -> None:
    class FakeAutoModel:
        def __init__(self, **kwargs: object) -> None:
            calls["automodel"] = dict(kwargs)

        def generate(self, **kwargs: object) -> list[dict[str, object]]:
            calls["generate"] = dict(kwargs)
            return [
                {
                    "text": full_text,
                    "sentence_info": sentence_info
                    if sentence_info is not None
                    else [{"start": 0, "end": 1000, "spk": 0, "sentence": "<|t|>你好"}],
                }
            ]

    funasr = types.ModuleType("funasr")
    funasr.AutoModel = FakeAutoModel  # type: ignore[attr-defined]
    postprocess = types.ModuleType("funasr.utils.postprocess_utils")
    postprocess.rich_transcription_postprocess = lambda s: s.replace("<|t|>", "")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "funasr", funasr)
    monkeypatch.setitem(sys.modules, "funasr.utils.postprocess_utils", postprocess)

    fake_torch = types.ModuleType("torch")
    fake_cuda = types.ModuleType("torch.cuda")
    fake_cuda.is_available = lambda: True  # type: ignore[attr-defined]
    fake_cuda.empty_cache = lambda: None  # type: ignore[attr-defined]
    fake_torch.cuda = fake_cuda  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(analyze_module, "_register_dll_directories", lambda: None)


def _canonical_manifest_hash(directory: Path) -> str:
    """与 verify_models 相同的规范化（丢弃 generated_at / 排序键 / 紧凑序列化），计算目录清单哈希。"""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    normalized = {key: value for key, value in manifest.items() if key != "generated_at"}
    canon = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _install_fake_anchors(monkeypatch: pytest.MonkeyPatch, dirs: dict[str, Path]) -> None:
    """让三个伪造清单的规范化哈希与 FROZEN_MANIFEST_HASHES 对齐，绕过版本冻结门检，按文件/路径/完整性逐项校验。"""
    monkeypatch.setattr(
        analyze_module,
        "FROZEN_MANIFEST_HASHES",
        {
            "sensevoice": _canonical_manifest_hash(dirs["sensevoice_dir"]),
            "vad": _canonical_manifest_hash(dirs["vad_dir"]),
            "speaker": _canonical_manifest_hash(dirs["spk_dir"]),
        },
    )


def _analysis_settings(tmp_path: Path) -> dict[str, Path]:
    manifest = {"model": "test", "revision": "test", "files": []}
    dirs: dict[str, Path] = {
        "sensevoice_dir": tmp_path / "sensevoice",
        "vad_dir": tmp_path / "vad",
        "spk_dir": tmp_path / "speaker",
    }
    for directory in dirs.values():
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dirs


def test_analyze_passes_local_dirs_and_frozen_automodel_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    _install_fake_funasr(monkeypatch, calls)
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    analyze_module.analyze(wav, settings)  # type: ignore[arg-type]

    assert calls["automodel"]["model"] == str(settings["sensevoice_dir"])
    assert calls["automodel"]["vad_model"] == str(settings["vad_dir"])
    assert calls["automodel"]["spk_model"] == str(settings["spk_dir"])
    assert calls["automodel"]["spk_mode"] == "vad_segment"
    assert calls["automodel"]["device"] == "cuda:0"
    assert calls["automodel"]["disable_update"] is True
    assert calls["automodel"]["trust_remote_code"] is False
    assert calls["automodel"]["vad_kwargs"] == {"max_single_segment_time": 15000}


def test_analyze_passes_frozen_generate_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    _install_fake_funasr(monkeypatch, calls)
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    analyze_module.analyze(wav, settings)  # type: ignore[arg-type]

    assert calls["generate"]["input"] == str(wav)
    assert calls["generate"]["language"] == "zh"
    assert calls["generate"]["use_itn"] is True
    assert calls["generate"]["batch_size_s"] == 60
    assert calls["generate"]["merge_vad"] is True
    assert calls["generate"]["merge_length_s"] == 10


def test_analyze_stores_cleaned_sentences_and_native_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    _install_fake_funasr(monkeypatch, calls)
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    artifact = analyze_module.analyze(wav, settings)  # type: ignore[arg-type]

    assert artifact["format"] == "sensevoice-analysis-v1"
    assert artifact["text"] == "raw-text"
    assert artifact["sentence_info"] == [{"start": 0, "end": 1000, "spk": 0, "sentence": "<|t|>你好"}]
    assert artifact["sentences"] == [{"start": 0, "end": 1000, "speaker": "SPEAKER_00", "text": "你好"}]


def test_analyze_raises_when_models_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_funasr(monkeypatch, {})
    settings = {"sensevoice_dir": tmp_path / "missing", "vad_dir": tmp_path / "missing2", "spk_dir": tmp_path / "missing3"}  # type: ignore[dict-item]

    with pytest.raises(ValueError, match="模型校验失败"):
        analyze_module.analyze(tmp_path / "a.wav", settings)


def test_analyze_raises_on_malformed_sentence_info(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    bad_sentence_info = [
        {"start": 0, "end": 1000, "spk": 0, "sentence": "正常"},  # OK
        {"start": 0, "end": 1000},  # 缺 spk
        "not a dict",
    ]
    _install_fake_funasr(monkeypatch, calls, sentence_info=bad_sentence_info, full_text="x正常y")
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    with pytest.raises(RuntimeError, match="格式异常"):
        analyze_module.analyze(wav, settings)  # type: ignore[arg-type]


def test_analyze_raises_on_empty_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, dict[str, object]] = {}
    # sentence_info 存在但所有句子清洗后为空
    empty_sentence_info = [{"start": 0, "end": 1000, "spk": 0, "sentence": "<|t|>"}]
    _install_fake_funasr(monkeypatch, calls, sentence_info=empty_sentence_info, full_text="")
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    with pytest.raises(RuntimeError, match="未产出任何文本内容"):
        analyze_module.analyze(wav, settings)  # type: ignore[arg-type]


def test_analyze_records_repetition_flag_character(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """单字符重复 ≥10：仅记 review_flags，不作为硬失败。"""
    calls: dict[str, dict[str, object]] = {}
    repeated = "啊" * 15
    sentence_info = [{"start": 0, "end": 1000, "spk": 0, "sentence": f"<|t|>{repeated}"}]
    _install_fake_funasr(monkeypatch, calls, sentence_info=sentence_info, full_text=repeated)
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    artifact = analyze_module.analyze(wav, settings)  # type: ignore[arg-type]

    assert artifact["review_flags"] == ["repetition"]
    assert analyze_module.derive_transcript(artifact)["review_flags"] == ["repetition"]


def test_analyze_records_repetition_flag_phrase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """短语分支：2-8 字短语重复 ≥6 次。"""
    calls: dict[str, dict[str, object]] = {}
    repeated = "可以" * 6
    sentence_info = [{"start": 0, "end": 1000, "spk": 0, "sentence": f"<|t|>{repeated}"}]
    _install_fake_funasr(monkeypatch, calls, sentence_info=sentence_info, full_text=repeated)
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    artifact = analyze_module.analyze(wav, settings)  # type: ignore[arg-type]

    assert artifact["review_flags"] == ["repetition"]


def test_analyze_no_repetition_flag_for_normal_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """正常文本不应产生 review_flags。"""
    calls: dict[str, dict[str, object]] = {}
    _install_fake_funasr(monkeypatch, calls)
    settings = _analysis_settings(tmp_path)
    _install_fake_anchors(monkeypatch, settings)  # type: ignore[arg-type]
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"data")

    artifact = analyze_module.analyze(wav, settings)  # type: ignore[arg-type]

    assert artifact["review_flags"] == []


def test_manifest_hashes_missing_manifest_raises(tmp_path: Path) -> None:
    settings = {"sensevoice_dir": tmp_path / "missing", "vad_dir": tmp_path / "missing2", "spk_dir": tmp_path / "missing3"}  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="模型清单缺失"):
        manifest_hashes(settings)


def test_manifest_hashes_returns_stable_per_role_hashes(tmp_path: Path) -> None:
    settings = _analysis_settings(tmp_path)
    first = manifest_hashes(settings)
    second = manifest_hashes(settings)
    assert set(first) == {"sensevoice", "vad", "speaker"}
    assert first == second  # 同内容稳定
    assert len(first["sensevoice"]) == 64  # sha256 十六进制


def test_verify_models_passes_for_matching_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = _dir_with_file(tmp_path, "sensevoice", b"weights")
    _install_fake_anchors(monkeypatch, {"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})
    verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def test_verify_models_rejects_anchor_mismatch(tmp_path: Path) -> None:
    """不 monkeypatch 锚点 → 伪造清单必然与冻结常量不符。"""
    directory = _dir_with_file(tmp_path, "sensevoice", b"weights")
    with pytest.raises(ValueError, match="与冻结版本不符"):
        verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def test_verify_models_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清单中包含 ../ 逃逸目录的文件路径必须被拒绝。"""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "evil.bin").write_bytes(b"evil")
    content = b"weights"
    directory = _dir_with_file(tmp_path, "sensevoice", content)
    # 在清单中插入 ../outside/evil.bin
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"].append({"path": "../outside/evil.bin", "bytes": 4, "sha256": hashlib.sha256(b"evil").hexdigest()})
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _install_fake_anchors(monkeypatch, {"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})

    with pytest.raises(ValueError, match="路径越界"):
        verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def test_verify_models_rejects_extra_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清单未声明的文件出现在目录中必须被拒绝（目录完整性）。"""
    directory = _dir_with_file(tmp_path, "sensevoice", b"weights")
    (directory / "extra.bin").write_bytes(b"extra")
    _install_fake_anchors(monkeypatch, {"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})

    with pytest.raises(ValueError, match="未声明文件"):
        verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def test_verify_models_raises_on_size_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = _dir_with_file(tmp_path, "sensevoice", b"weights")
    _install_fake_anchors(monkeypatch, {"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})
    (directory / "weights.bin").write_bytes(b"changed-length")
    with pytest.raises(ValueError, match="字节数不匹配"):
        verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def test_verify_models_raises_on_hash_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = _dir_with_file(tmp_path, "sensevoice", b"weights")
    _install_fake_anchors(monkeypatch, {"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})
    (directory / "weights.bin").write_bytes(b"WEIGHTS")  # 同长度不同内容
    with pytest.raises(ValueError, match="SHA-256 不匹配"):
        verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def test_verify_models_raises_on_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = _dir_with_file(tmp_path, "sensevoice", b"weights")
    _install_fake_anchors(monkeypatch, {"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})
    (directory / "weights.bin").unlink()
    with pytest.raises(ValueError, match="模型文件缺失"):
        verify_models({"sensevoice_dir": directory, "vad_dir": directory, "spk_dir": directory})  # type: ignore[arg-type]


def _dir_with_file(tmp_path: Path, name: str, content: bytes) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "weights.bin").write_bytes(content)
    manifest = {
        "model": name,
        "revision": "r",
        "files": [{"path": "weights.bin", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory
