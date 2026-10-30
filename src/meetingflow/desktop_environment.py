"""设置窗口的环境检查和安装入口。模型与任务内容不通过此模块上传。"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from typing import TypedDict

from .analyze import _register_dll_directories, automodel_options, verify_models
from .pipeline import Settings, validate_settings

MODEL_FIELDS = {"sensevoice": "sensevoice_dir", "vad": "vad_dir", "speaker": "spk_dir"}
CHECK_TITLES = {
    "directories": "存储目录", "ffmpeg": "FFmpeg / ffprobe", "python": "Python 推理环境",
    "sensevoice": "SenseVoiceSmall", "vad": "FSMN-VAD", "speaker": "CAM++",
}


class EnvironmentCheck(TypedDict):
    key: str
    title: str
    status: str
    message: str
    action: str


def check_environment(settings: Settings) -> list[EnvironmentCheck]:
    validate_settings(settings)
    checks: list[EnvironmentCheck] = []
    missing = [str(settings[key]) for key in ("inbox", "work", "output") if not settings[key].is_dir()]
    checks.append({"key": "directories", "title": CHECK_TITLES["directories"], "status": "missing" if missing else "ready",
                   "message": "尚未创建：" + "、".join(missing) if missing else "录音、任务和输出目录可用。", "action": "create" if missing else ""})
    missing_tools: list[str] = []
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if executable is None:
            missing_tools.append(name)
            continue
        try:
            subprocess.run([executable, "-version"], capture_output=True, check=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            missing_tools.append(name)
    checks.append({"key": "ffmpeg", "title": CHECK_TITLES["ffmpeg"], "status": "missing" if missing_tools else "ready",
                   "message": "缺失或无法启动：" + "、".join(missing_tools) if missing_tools else "音频探测与转换程序可用。",
                   "action": "ffmpeg" if missing_tools else ""})
    try:
        options = automodel_options()
        _register_dll_directories()
        torch = importlib.import_module("torch")
        importlib.import_module("torchaudio")
        importlib.import_module("funasr")
        importlib.import_module("modelscope")
        importlib.import_module("modelscope_hub")
        if options["device"] != "cpu" and not torch.cuda.is_available():
            cuda_build = getattr(getattr(torch, "version", None), "cuda", None)
            checks.append({"key": "python", "title": CHECK_TITLES["python"], "status": "error",
                           "message": "未检测到 CUDA，请确认 NVIDIA 显卡及驱动可用。" if cuda_build else "当前 Torch 未包含 CUDA，请同步项目锁定的依赖。",
                           "action": "driver" if cuda_build else "python"})
        else:
            checks.append({"key": "python", "title": CHECK_TITLES["python"], "status": "ready",
                           "message": f"Torch、torchaudio、FunASR 可导入，使用 {options['device']}。", "action": ""})
    except (ImportError, OSError, RuntimeError) as error:
        checks.append({"key": "python", "title": CHECK_TITLES["python"], "status": "error",
                       "message": f"推理依赖不可用：{error}", "action": "python"})
    except ValueError as error:
        checks.append({"key": "python", "title": CHECK_TITLES["python"], "status": "error", "message": str(error), "action": ""})
    for role in MODEL_FIELDS:
        try:
            verify_models(settings["models"], only_role=role)
            status, message, action = "ready", "冻结版本、清单与全部模型文件哈希匹配。", ""
        except (OSError, ValueError) as error:
            status, message, action = "missing", str(error), "model"
        checks.append({"key": role, "title": CHECK_TITLES[role], "status": status, "message": message, "action": action})
    return checks


def ffmpeg_install_command() -> list[str] | None:
    if sys.platform == "darwin" and (brew := shutil.which("brew")):
        return [brew, "install", "ffmpeg"]
    if sys.platform == "win32" and (winget := shutil.which("winget")):
        return [winget, "install", "--id", "Gyan.FFmpeg", "--exact", "--source", "winget", "--scope", "user",
                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
    return None


def create_directories(settings: Settings) -> None:
    validate_settings(settings)
    for key in ("inbox", "work", "output"):
        settings[key].mkdir(parents=True, exist_ok=True)
