"""SenseVoiceSmall + FSMN-VAD + CAM++ 单次本地语音分析。

一个模块承载全部模型链路：启动前清单校验、串行加载三个本地模型一次运行、
以及从原生分析产物派生段级转写与说话人轮次。不引入通用模型抽象。
"""

from __future__ import annotations

import gc
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import TypedDict

ANALYSIS_FORMAT = "sensevoice-analysis-v1"
DERIVE_FORMAT = "sensevoice-derive-v1"

# 冻结参数（见目标架构交接文档）：任何改动都必须重新通过固定三场景回归与零网络探针。
AUTOMODEL_OPTIONS: dict[str, object] = {
    "vad_kwargs": {"max_single_segment_time": 15000},
    "spk_mode": "vad_segment",
    "device": "cuda:0",
    "disable_update": True,
    "trust_remote_code": False,
}
GENERATE_OPTIONS: dict[str, object] = {
    "language": "zh",
    "use_itn": True,
    "batch_size_s": 60,
    "merge_vad": True,
    "merge_length_s": 10,
}

# 信任锚点：三个版本化模型目录 manifest.json 的规范化 SHA-256（见研究文档冻结 commit 与权重哈希）。
# manifest_hashes() 只把清单内容纳入转写指纹，无法防止“清单+文件一起被替换”；verify_models() 以本常量锁定版本。
FROZEN_MANIFEST_HASHES: dict[str, str] = {
    "sensevoice": "30a155e57ed3b59f2fde45746e5dc20eaa03d410747ef14071f9481f53935fc2",
    "vad": "945028ecf1f721765b0a5d5cce4f3c4a85ee5a191477dbd88686b4cfd1626674",
    "speaker": "647df6a5368efc281936415f6b08d65e7ac5e97352e80d27d231bffefdc7b63b",
}


class AnalysisSettings(TypedDict):
    sensevoice_dir: Path
    vad_dir: Path
    spk_dir: Path


class SpeakerSegment(TypedDict):
    start: float
    end: float
    speaker: str


def manifest_hashes(settings: AnalysisSettings) -> dict[str, str]:
    """三个模型目录 manifest.json 的内容哈希，用于转写指纹；清单缺失直接失败。"""
    hashes: dict[str, str] = {}
    for role, directory in _model_dirs(settings):
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            raise ValueError(f"模型清单缺失：{manifest}。请按 README 准备版本化模型目录。")
        hashes[role] = _sha256(manifest)
    return hashes


def verify_models(settings: AnalysisSettings) -> None:
    """加载模型前按冻结锚点 + 清单逐文件校验，缺失或不匹配直接失败，禁止回退在线模型。

    信任根是 FROZEN_MANIFEST_HASHES：目录 manifest.json 的规范化哈希必须等于冻结常量，
    防止“清单与文件一起被替换”；随后再按清单校验每个文件字节数与 SHA-256，并拒绝路径穿越与目录多余文件。
    """
    errors: list[str] = []
    for role, directory in _model_dirs(settings):
        directory_abs = directory.resolve()
        if not directory.is_dir():
            errors.append(f"模型目录不存在：{directory}")
            continue
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            errors.append(f"模型清单缺失：{manifest_path}")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"模型清单损坏：{manifest_path}")
            continue
        if not isinstance(manifest, dict):
            errors.append(f"模型清单格式异常：{manifest_path}")
            continue
        # 锚点校验：目录清单必须对应 Wayfinder 冻结版本，否则整个目录不可信。
        actual_hash = _manifest_hash(manifest)
        expected_hash = FROZEN_MANIFEST_HASHES.get(role)
        if actual_hash != expected_hash:
            errors.append(f"模型清单与冻结版本不符（{role}）：期望 {expected_hash}，实际 {actual_hash}")
            continue  # 锚点失败时文件级校验失去意义
        files = manifest.get("files")
        if not isinstance(files, list):
            errors.append(f"模型清单缺少 files 列表：{manifest_path}")
            continue
        declared: set[Path] = set()
        for entry in files:
            if not isinstance(entry, dict) or not {"path", "bytes", "sha256"} <= entry.keys():
                errors.append(f"模型清单条目格式异常：{manifest_path}")
                continue
            target = directory / str(entry["path"])
            # 拒绝路径穿越：解析后必须仍在该模型目录内。
            if directory_abs not in target.resolve().parents and target.resolve() != directory_abs:
                errors.append(f"模型清单路径越界：{entry['path']}")
                continue
            if not target.is_file():
                errors.append(f"模型文件缺失：{target}")
                continue
            if target.stat().st_size != entry["bytes"]:
                errors.append(f"模型文件字节数不匹配：{target}")
                continue
            if _sha256(target) != entry["sha256"]:
                errors.append(f"模型文件 SHA-256 不匹配：{target}")
            declared.add(target.resolve())
        # 目录完整性：仅排除模型目录根部的 manifest.json，其他同名文件应作为未声明文件被拒绝。
        manifest_path = directory_abs / "manifest.json"
        for extra in directory_abs.rglob("*"):
            if extra.is_file() and extra.resolve() != manifest_path.resolve() and extra not in declared:
                errors.append(f"模型目录存在未声明文件：{extra}")
    if errors:
        raise ValueError("模型校验失败：\n- " + "\n- ".join(errors))


def _manifest_hash(manifest: dict[str, object]) -> str:
    """清单规范化哈希：丢弃易变时间戳、按键排序、紧凑序列化后取 SHA-256，保证跨平台可复现。"""
    normalized = {key: value for key, value in manifest.items() if key != "generated_at"}
    canon = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def analyze(audio_path: Path, settings: AnalysisSettings) -> dict[str, object]:
    """串行加载三个本地模型，一次运行得到文字、VAD 时间与说话人段，返回原生分析产物。"""
    verify_models(settings)
    _register_dll_directories()
    import torch

    if not torch.cuda.is_available():
        raise ValueError("未检测到 CUDA GPU。当前方案按 RTX 4060 冻结，不支持 CPU 回退。")
    options = {**AUTOMODEL_OPTIONS, "vad_kwargs": dict(AUTOMODEL_OPTIONS["vad_kwargs"])}  # type: ignore[dict-item]
    with _quiet_funasr_output():
        from funasr import AutoModel
        from funasr.auto import auto_model as funasr_auto_model
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        original_tqdm = funasr_auto_model.tqdm
        funasr_auto_model.tqdm = _SingleLineProgress
        _progress("语音分析", 0)
        succeeded = False
        try:
            model = AutoModel(
                model=str(settings["sensevoice_dir"]),
                vad_model=str(settings["vad_dir"]),
                spk_model=str(settings["spk_dir"]),
                **options,  # type: ignore[arg-type]
            )
            try:
                result: object = model.generate(input=str(audio_path), **GENERATE_OPTIONS)  # type: ignore[arg-type]
                succeeded = True
            finally:
                del model
                gc.collect()
                torch.cuda.empty_cache()
        finally:
            funasr_auto_model.tqdm = original_tqdm
            if succeeded:
                _progress("语音分析", 100, finish=True)
            else:
                print(file=sys.stderr)
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise RuntimeError("SenseVoice 未返回预期结果")
    item = result[0]
    sentence_info = item.get("sentence_info")
    if not isinstance(sentence_info, list):
        raise RuntimeError("SenseVoice 未返回说话人分段")
    sentences: list[dict[str, object]] = []
    malformed: list[int] = []
    for index, entry in enumerate(sentence_info):
        if not isinstance(entry, dict) or not {"start", "end", "spk"} <= entry.keys():
            malformed.append(index)
            continue
        sentences.append(
            {
                "start": int(entry["start"]),
                "end": int(entry["end"]),
                "speaker": f"SPEAKER_{int(entry['spk']):02d}",
                "text": rich_transcription_postprocess(str(entry.get("sentence", ""))).strip(),
            }
        )
    if malformed:
        raise RuntimeError(f"SenseVoice 返回了格式异常的说话人分段（索引 {malformed[:5]}），已中止避免静默丢段。")
    full_text = "".join(str(sentence["text"]) for sentence in sentences)
    # 全空结果视为失败（无法用文字信号区分"整段遗漏"，后者需在人工核听中覆盖）。
    if not full_text.strip():
        raise RuntimeError("SenseVoice 未产出任何文本内容。请检查录音或重试。")
    review_flags = _detect_repetition_flags(full_text)
    return {
        "format": ANALYSIS_FORMAT,
        "text": str(item.get("text", "")),
        "sentence_info": sentence_info,
        "sentences": sentences,
        "review_flags": review_flags,
    }


class _FunASRTerminalFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return False
        return record.getMessage() not in {
            "Missing punc_model, which is required by spk_model.",
            "No timestamp found in ASR result. Speaker diarization relies on timestamps.",
        }


class _SingleLineProgress:
    def __init__(self, *, total: int, **_kwargs: object) -> None:
        self.total = max(total, 1)
        self.current = 0

    def update(self, amount: int = 1) -> None:
        self.current = min(self.total, self.current + amount)
        _progress("语音分析", self.current * 100 / self.total)

    def set_description(self, _description: str) -> None:
        return


def _progress(stage: str, percent: float, *, finish: bool = False) -> None:
    value = max(0.0, min(100.0, percent))
    width = 20
    filled = round(width * value / 100)
    print(
        f"\r  {stage:<10} │{'■' * filled}{'·' * (width - filled)}│ {value:5.1f}%",
        end="\n" if finish else "",
        file=sys.stderr,
        flush=True,
    )


@contextmanager
def _quiet_funasr_output() -> Iterator[None]:
    root = logging.getLogger()
    terminal_filter = _FunASRTerminalFilter()
    handlers = list(root.handlers)
    temporary_handler: logging.Handler | None = None
    if not handlers:
        temporary_handler = logging.StreamHandler()
        root.addHandler(temporary_handler)
        handlers.append(temporary_handler)
    for handler in handlers:
        handler.addFilter(terminal_filter)
    try:
        with redirect_stdout(io.StringIO()):
            yield
    finally:
        for handler in handlers:
            handler.removeFilter(terminal_filter)
        if temporary_handler is not None:
            root.removeHandler(temporary_handler)


def _detect_repetition_flags(text: str) -> list[str]:
    """检测重复风险标记——不作为硬失败，留待人工核听确认。

    仅凭转录文本无法区分模型陷入循环与参会人真实口吃/强调/列表式发言；
    命中时返回 ["repetition"]，由 pipeline 在 run.jsonl 中记录 reproduction_flagged 事件。
    """
    flags: list[str] = []
    compact = re.sub(r"\s|\W", "", text, flags=re.UNICODE)
    if re.search(r"(.)\1{9,}", compact):
        flags.append("repetition")
        return flags
    if re.search(r"(.{2,8})\1{5,}", compact):
        flags.append("repetition")
    return flags


def derive_transcript(analysis: dict[str, object]) -> dict[str, object]:
    """派生段级转写：相邻同说话人合并为主要发言轮次，不提供词级时间戳。

    review_flags 透传：人工核听提示行通过此处传递到 transcript.raw.json，由 render 读取。
    """
    raw_flags = analysis.get("review_flags", [])
    flags: list[str] = list(raw_flags) if isinstance(raw_flags, list) else []
    return {
        "language": str(GENERATE_OPTIONS["language"]),
        "segments": [
            {"start": round(int(turn["start"]) / 1000, 3), "end": round(int(turn["end"]) / 1000, 3), "text": str(turn["text"])}
            for turn in _turns(analysis)
        ],
        "review_flags": flags,
    }


def derive_speakers(analysis: dict[str, object]) -> list[SpeakerSegment]:
    """派生说话人轮次段，与段级转写使用同一合并结果。"""
    return [
        {"start": round(int(turn["start"]) / 1000, 3), "end": round(int(turn["end"]) / 1000, 3), "speaker": str(turn["speaker"])}
        for turn in _turns(analysis)
    ]


def _turns(analysis: dict[str, object]) -> list[dict[str, object]]:
    sentences = analysis.get("sentences")
    if not isinstance(sentences, list):
        raise ValueError("原生分析产物缺少 sentences。请执行 retry <job-id> --from transcribe 重新生成。")
    turns: list[dict[str, object]] = []
    malformed: list[int] = []
    for index, sentence in enumerate(sentences):
        if not isinstance(sentence, dict):
            malformed.append(index)
            continue
        # 严格校验：start/end 数值 + 时间窗口合法、speaker 非空、text 为字符串（空字符串允许——VAD 静默短段）。
        try:
            start = (
                int(sentence["start"])
                if "start" in sentence
                else (int(sentence.get("start", 0)) if sentence.get("start") is not None else None)
            )
            end = int(sentence["end"]) if "end" in sentence else (int(sentence.get("end", 0)) if sentence.get("end") is not None else None)
        except (TypeError, ValueError):
            malformed.append(index)
            continue
        if start is None or end is None or start < 0 or end <= start:
            malformed.append(index)
            continue
        speaker = sentence.get("speaker", "")
        if not isinstance(speaker, str) or not speaker:
            malformed.append(index)
            continue
        text = sentence.get("text", "")
        if not isinstance(text, str):
            malformed.append(index)
            continue
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["end"] = end
            turns[-1]["text"] = str(turns[-1]["text"]) + text
        else:
            turns.append({"start": start, "end": end, "speaker": speaker, "text": text})
    if malformed:
        raise RuntimeError(f"原生分析产物包含畸形说话人分段（索引 {malformed[:5]}），请执行 retry <job-id> --from transcribe 重新生成。")
    return turns


def _model_dirs(settings: AnalysisSettings) -> list[tuple[str, Path]]:
    return [("sensevoice", settings["sensevoice_dir"]), ("vad", settings["vad_dir"]), ("speaker", settings["spk_dir"])]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_DLL_DIRECTORIES: list[object] = []


def _register_dll_directories() -> None:
    if os.name != "nt" or _DLL_DIRECTORIES:
        return
    directories: list[Path] = [Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"]
    if ffmpeg := shutil.which("ffmpeg"):
        directories.append(Path(ffmpeg).parent)
    for directory in directories:
        if directory.is_dir():
            _DLL_DIRECTORIES.append(os.add_dll_directory(str(directory)))
