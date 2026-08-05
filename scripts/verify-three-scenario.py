"""固定三场景自动回归：在独立验收环境跑新流水线，产出自动指标摘要。

使用方法：
    uv run scripts/verify-three-scenario.py --config <cfg.toml> --report <out.json>

退出码：
    0  全部完成
    1  流程异常
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROTO_DIR = Path(r"D:\Workspace\meeting-flow\.scratch\meeting-transcription-selection\prototype\benchmark-outputs")
BENCHMARK_DIR = Path(r"D:\Workspace\meeting-flow\.scratch\meeting-transcription-selection\prototype\benchmark-audio")

SCENES = {
    "normal": {"title": "日常场景（完整会议）", "wav": BENCHMARK_DIR / "normal.wav"},
    "multi": {"title": "多人压力场景（01:30:00–01:40:00）", "wav": BENCHMARK_DIR / "multi.wav"},
    "difficult": {"title": "困难场景（01:30:00–01:38:55）", "wav": BENCHMARK_DIR / "difficult.wav"},
}

TIMESTAMP_RE = re.compile(r"^\[[^\]]*\]\s*")
NAME_RE = re.compile(r"^(Speaker\s+\d+|未知发言人)\s*[：:]\s*")
MIN_TEXT_SIMILARITY = 0.95
EVIDENCE_REPORTS = {
    "docs/V2-project-review-wayfinder-offline.json",
    "docs/V2-project-review-wayfinder-three-scenario.json",
}


def _extract_rows(markdown: str) -> list[str]:
    rows: list[str] = []
    for line in markdown.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        stripped = TIMESTAMP_RE.sub("", line.strip())
        stripped = NAME_RE.sub("", stripped)
        text = stripped.strip()
        if text:
            rows.append(text)
    return rows


def _diff_counts(proto_text: str, new_text: str) -> dict[str, int]:
    """最长公共前缀 + 后缀，统计相对原型的字符增删量。"""
    p, n = list(proto_text), list(new_text)
    prefix = 0
    while prefix < len(p) and prefix < len(n) and p[prefix] == n[prefix]:
        prefix += 1
    suffix = 0
    while suffix < len(p) - prefix and suffix < len(n) - prefix and p[len(p) - 1 - suffix] == n[len(n) - 1 - suffix]:
        suffix += 1
    return {
        "proto_chars": len(p),
        "new_chars": len(n),
        "char_delta": len(n) - len(p),
        "common_prefix": prefix,
        "common_suffix": suffix,
        "modified_zone": len(n) - prefix - suffix,
        "similarity_ppm": round(difflib.SequenceMatcher(None, proto_text, new_text, autojunk=False).ratio() * 1_000_000),
    }


def _scene_passes(summary: dict[str, object]) -> bool:
    """自动发布门：产物非空、无风险标记、与冻结原型的字符相似度不低于 95%。"""
    diff = summary.get("diff_vs_proto")
    if not isinstance(diff, dict):
        return False
    similarity = diff.get("similarity")
    if similarity is None and isinstance(diff.get("similarity_ppm"), int):
        similarity = int(diff["similarity_ppm"]) / 1_000_000
    return (
        int(summary.get("new_chars", 0)) > 0
        and int(summary.get("new_paragraphs", 0)) > 0
        and summary.get("review_flags") == []
        and isinstance(similarity, (int, float))
        and float(similarity) >= MIN_TEXT_SIMILARITY
    )


def _job_dir_for(work_root: Path, job_id_prefix: str) -> Path | None:
    """在 Work/jobs/ 中按 8 字符前缀定位任务目录。"""
    candidates = list((work_root / "jobs").glob(f"{job_id_prefix}*"))
    if len(candidates) != 1:
        return None
    return candidates[0]


def _run_meetingflow(config: Path, source: Path, output_root: Path) -> Path:
    """强制运行当前 worktree 的 meetingflow；通过文件系统定位输出目录以避免 stdout 编码陷阱。"""
    cmd = _meetingflow_command(config, source)
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    result = subprocess.run(cmd, capture_output=True, timeout=1800, env=environment)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        raise RuntimeError(f"meetingflow 退出码 {result.returncode}：stderr={stderr[:500]}\nstdout={stdout[:500]}")
    # 找最新创建的、与源文件 stem 匹配的输出目录
    candidates = sorted(
        [p for p in output_root.iterdir() if p.is_dir() and source.stem in p.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"meetingflow 成功但未找到输出目录（source={source.name}）")
    return candidates[0]


def _meetingflow_command(config: Path, source: Path) -> list[str]:
    """固定使用 --force，禁止验收命中历史任务缓存。"""
    return ["uv", "run", "meetingflow", "--config", str(config), "process", str(source), "--force"]


def _read_review_flags(work_root: Path, job_id8: str) -> list[str]:
    """从 transcript.raw.json 读取 review_flags（如果存在）。"""
    job_dir = _job_dir_for(work_root, job_id8)
    if job_dir is None:
        raise RuntimeError(f"找不到唯一任务目录：{job_id8}")
    raw = job_dir / "transcript.raw.json"
    if not raw.is_file():
        raise RuntimeError(f"缺少验收转写产物：{raw}")
    payload = json.loads(raw.read_text(encoding="utf-8"))
    flags = payload.get("review_flags", [])
    return list(flags) if isinstance(flags, list) else []


def _build_context() -> dict[str, object]:
    """捕获 HEAD 与未提交工作树内容指纹，避免把旧提交误记为被测实现。"""
    import importlib.metadata

    from meetingflow.analyze import AUTOMODEL_OPTIONS, FROZEN_MANIFEST_HASHES, GENERATE_OPTIONS

    repository = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=repository).strip()
        status = subprocess.check_output(["git", "status", "--porcelain"], text=True, cwd=repository)
        diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=repository)
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
        worktree_sha256 = digest.hexdigest()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "unknown"
        status = "unknown"
        untracked = []
        worktree_sha256 = "unknown"
    try:
        funasr = importlib.metadata.version("funasr")
        modelscope = importlib.metadata.version("modelscope")
    except importlib.metadata.PackageNotFoundError:
        funasr = modelscope = "missing"
    return {
        "reviewed_commit": commit,
        "worktree_dirty": bool(status.strip()),
        "worktree_sha256": worktree_sha256,
        "untracked_files": untracked,
        "funasr": funasr,
        "modelscope": modelscope,
        "frozen_manifest_anchors": FROZEN_MANIFEST_HASHES,
        "frozen_automodel_options": AUTOMODEL_OPTIONS,
        "frozen_generate_options": GENERATE_OPTIONS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="固定三场景自动回归")
    parser.add_argument("--config", required=True, help="meetingflow 配置 TOML")
    parser.add_argument("--report", required=True, help="输出 JSON 报告路径")
    args = parser.parse_args()

    config = Path(args.config).resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "auto_check": "三场景自动指标（不包含人工核听；整段遗漏/幻觉需人耳判定）",
        "context": _build_context(),
        "scenes": {},
    }

    from meetingflow.pipeline import load_settings

    settings = load_settings(config)
    output_root = settings["output"]
    work_root = settings["work"]
    all_passed = True
    for scene, scene_cfg in SCENES.items():
        audio_bytes = scene_cfg["wav"].read_bytes()
        audio_sha = hashlib.sha256(audio_bytes).hexdigest()
        proto_md = (PROTO_DIR / f"{scene}-sensevoice.md").read_text(encoding="utf-8")
        proto_rows = _extract_rows(proto_md)
        proto_text = "".join(proto_rows)

        output_dir = _run_meetingflow(config, scene_cfg["wav"], output_root)
        md = (output_dir / "speakers.md").read_text(encoding="utf-8")
        new_rows = _extract_rows(md)
        new_text = "".join(new_rows)
        # 提取新流水线输出中的 review_flags 与说话人轮次数
        # job_id 来自 output_dir 命名规则 YYYY-MM-DD_<scene>_<job8>
        parts = output_dir.name.split("_")
        job_id8 = parts[-1] if len(parts) >= 3 else ""
        review_flags = _read_review_flags(work_root, job_id8)
        job_dir = _job_dir_for(work_root, job_id8)

        scene_summary = {
            "scene": scene,
            "title": scene_cfg["title"],
            "audio_sha256": audio_sha,
            "audio_bytes": len(audio_bytes),
            "proto_chars": len(proto_text),
            "proto_paragraphs": len(proto_rows),
            "new_chars": len(new_text),
            "new_paragraphs": len(new_rows),
            "diff_vs_proto": _diff_counts(proto_text, new_text),
            "output_dir": str(output_dir),
        }
        scene_summary["review_flags"] = review_flags
        if job_dir is not None:
            speakers_json = job_dir / "speakers.json"
            if speakers_json.is_file():
                speakers_payload = json.loads(speakers_json.read_text(encoding="utf-8"))
                segments = speakers_payload.get("segments", [])
                scene_summary["speaker_count"] = len({seg.get("speaker") for seg in segments if isinstance(seg, dict)})
                scene_summary["speaker_segments_count"] = len(segments)
        scene_summary["passed"] = _scene_passes(scene_summary)
        all_passed = all_passed and bool(scene_summary["passed"])
        summary["scenes"][scene] = scene_summary  # type: ignore[index]

    summary["passed"] = all_passed
    summary["minimum_text_similarity"] = MIN_TEXT_SIMILARITY
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"报告：{report_path}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
