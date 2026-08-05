"""零网络全流程探针：阻断 socket/DNS + 无 HF_TOKEN 后跑完整流水线。

把探针逻辑与一次性回归目录的脚本分离，便于作为发布门运行。
使用方法：
    uv run scripts/verify-offline.py --source <audio.wav> --work <work-dir> --output <output-dir> --config <cfg.toml>

退出码：
    0  探针通过（无网络尝试、流水线产出有效 artifact）
    1  出现网络尝试或流水线异常
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

EVIDENCE_REPORTS = {
    "docs/V2-project-review-wayfinder-offline.json",
    "docs/V2-project-review-wayfinder-three-scenario.json",
}


def _network_blocker(attempts: list[str], operation: str) -> Callable[..., object]:
    """创建会先记录操作名、再阻断网络调用的替代函数。"""

    def blocked(*args: object, **kwargs: object) -> object:
        attempts.append(operation)
        raise RuntimeError(f"离线验证阻止了网络访问：{operation}")

    return blocked


def _worktree_context() -> dict[str, object]:
    repository = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=repository).strip()
        exclusions = [f":(exclude){path}" for path in sorted(EVIDENCE_REPORTS)]
        status = subprocess.check_output(["git", "status", "--porcelain", "--", ".", *exclusions], text=True, cwd=repository)
        diff = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", ".", *exclusions], cwd=repository)
        untracked_output = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repository).decode(
            "utf-8"
        )
        digest = hashlib.sha256(diff)
        untracked = sorted(path for path in untracked_output.split("\0") if path and path not in EVIDENCE_REPORTS)
        for relative in untracked:
            path = repository / relative
            if path.is_file():
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
        return {
            "reviewed_commit": commit,
            "worktree_dirty": bool(status.strip()),
            "worktree_sha256": digest.hexdigest(),
            "untracked_files": untracked,
        }
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"reviewed_commit": "unknown", "worktree_dirty": True, "worktree_sha256": "unknown", "untracked_files": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="零网络全流程探针")
    parser.add_argument("--source", required=True, help="音频源文件")
    parser.add_argument("--work", required=True, help="Work 目录")
    parser.add_argument("--output", required=True, help="Output 目录")
    parser.add_argument("--config", required=True, help="meetingflow 配置 TOML")
    parser.add_argument("--report", default=None, help="探针 JSON 报告输出路径（默认 <output>/verify-offline.json）")
    args = parser.parse_args()

    network_attempts: list[str] = []
    socket.socket.connect = _network_blocker(network_attempts, "socket.connect")  # type: ignore[method-assign]
    socket.socket.connect_ex = _network_blocker(network_attempts, "socket.connect_ex")  # type: ignore[method-assign]
    socket.create_connection = _network_blocker(network_attempts, "socket.create_connection")  # type: ignore[assignment]
    socket.getaddrinfo = _network_blocker(network_attempts, "socket.getaddrinfo")  # type: ignore[assignment]
    for name in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "MODELSCOPE_TOKEN", "MODELSCOPE_API_TOKEN"):
        os.environ.pop(name, None)

    from meetingflow import pipeline

    settings = pipeline.load_settings(Path(args.config))
    settings["work"] = Path(args.work).resolve()
    settings["output"] = Path(args.output).resolve()

    success = True
    try:
        # 从 probe 强制重跑，禁止已有成功任务让零网络探针绕过模型加载。
        result = pipeline.process(Path(args.source).resolve(), settings, start_stage="probe")
        if result.skipped:
            raise RuntimeError("离线探针命中了历史缓存，未执行完整流水线")
        summary = {
            "success": True,
            "network_attempts": network_attempts,
            "skipped": result.skipped,
            "context": _worktree_context(),
            "output_dir": str(result.output_dir),
            "job_id": result.job_id[:8],
        }
    except Exception as error:
        success = False
        summary = {
            "success": False,
            "network_attempts": network_attempts,
            "context": _worktree_context(),
            "error": str(error),
        }

    report_path = Path(args.report) if args.report else (Path(args.output) / "verify-offline.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    if not success or network_attempts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
