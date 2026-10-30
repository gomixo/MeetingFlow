"""下载三个冻结的本地模型目录并生成确定性 manifest.json，然后调用 verify_models 校验。

唯一目的：在新机器上一次性把 `D:/Meetings/Models/<role>-<commit>/` 准备到位，满足
`analyze.py` 中 FROZEN_MANIFEST_HASHES 信任锚点。运行后日常推理完全离线。

使用方法：
    uv run scripts/prepare-models.py

可选参数：
    --root D:/MeetingFlow/Models    自定义根目录（默认 D:/Meetings/Models）

退出码：
    0  全部成功
    1  任一模型下载或校验失败
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TypedDict

MODELS = (
    {
        "role": "sensevoice",
        "dir_name": "sensevoice-small-7bf4524",
        "model_id": "iic/SenseVoiceSmall",
        "revision": "7bf452403abd7353a300cd760f7adae7701c92c1",
    },
    {
        "role": "vad",
        "dir_name": "fsmn-vad-f9a8b82",
        "model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "revision": "f9a8b8274674755d925277e27063869038d41515",
    },
    {
        "role": "speaker",
        "dir_name": "campplus-a045b2a",
        "model_id": "iic/speech_campplus_sv_zh-cn_16k-common",
        "revision": "a045b2afcaa9c3049c98a9215a2bc274407ab237",
    },
)


class FrozenFile(TypedDict):
    path: str
    bytes: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download(model_id: str, revision: str, target: Path) -> None:
    from modelscope import snapshot_download

    snapshot_download(
        model_id=model_id,
        revision=revision,
        local_dir=str(target),
        local_files_only=False,
    )


def _remote_files(model_id: str, revision: str) -> list[FrozenFile]:
    """读取固定 commit 的仓库文件清单，避免把下载器本地元数据纳入模型 manifest。"""
    from modelscope_hub.api import HubApi

    files: list[FrozenFile] = []
    for entry in HubApi().list_repo_files(model_id, repo_type="model", revision=revision, recursive=True):
        if entry.type == "tree":
            continue
        if entry.size is None or entry.sha256 is None:
            raise ValueError(f"远程文件缺少字节数或 SHA-256：{entry.path}")
        files.append({"path": entry.path, "bytes": entry.size, "sha256": entry.sha256})
    # 旧锚点由 WindowsPath 顺序生成，用 casefold 显式固定该顺序，避免 macOS 将 README.md 排到小写文件之前。
    return sorted(files, key=lambda item: item["path"].casefold())


def _canonical_manifest(directory: Path, model_id: str, revision: str, frozen_files: list[FrozenFile]) -> dict[str, object]:
    files: list[FrozenFile] = []
    for entry in frozen_files:
        path = directory / entry["path"]
        if not path.is_file():
            raise ValueError(f"下载缺少冻结文件：{entry['path']}")
        actual_bytes = path.stat().st_size
        if actual_bytes != entry["bytes"]:
            raise ValueError(f"下载文件字节数不匹配：{entry['path']}，期望 {entry['bytes']}，实际 {actual_bytes}")
        actual_hash = _sha256(path)
        if actual_hash != entry["sha256"]:
            raise ValueError(f"下载文件 SHA-256 不匹配：{entry['path']}")
        files.append(dict(entry))
    return {
        "model": model_id,
        "revision": revision,
        "files": files,
    }


def _remove_unlisted_files(directory: Path, frozen_files: list[FrozenFile]) -> None:
    """只保留固定 commit 的仓库文件，清理失败重试或下载器留下的本地元数据。"""
    declared = {(directory / entry["path"]).resolve() for entry in frozen_files}
    for path in directory.rglob("*"):
        if path.is_file() and path.resolve() not in declared:
            path.unlink()
    for path in sorted((path for path in directory.rglob("*") if path.is_dir()), reverse=True):
        with suppress(OSError):
            path.rmdir()


def _write_manifest(directory: Path, payload: dict[str, object]) -> Path:
    manifest_path = directory / "manifest.json"
    content = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".manifest.json.", text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
        Path(temporary).replace(manifest_path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return manifest_path


def _manifest_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_model(
    root: Path,
    spec: dict[str, str],
    downloader: Callable[[str, str, Path], None] = _download,
) -> Path:
    """在固定 staging 目录下载并校验，成功后原子发布；已有正式目录只读返回。"""
    target = root / spec["dir_name"]
    if target.exists():
        return target

    staging = root / f".{spec['dir_name']}.preparing"
    staging.mkdir(parents=True, exist_ok=True)
    downloader(spec["model_id"], spec["revision"], staging)
    frozen_files = _remote_files(spec["model_id"], spec["revision"])
    manifest = _canonical_manifest(staging, spec["model_id"], spec["revision"], frozen_files)

    from meetingflow.analyze import FROZEN_MANIFEST_HASHES

    actual = _manifest_digest(manifest)
    expected = FROZEN_MANIFEST_HASHES[spec["role"]]
    if actual != expected:
        raise ValueError(f"下载内容与冻结锚点不符（{spec['role']}）：期望 {expected}，实际 {actual}；暂存目录保留在 {staging}")
    _remove_unlisted_files(staging, frozen_files)
    _write_manifest(staging, manifest)
    staging.replace(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并校验三个冻结模型目录")
    locations = parser.add_mutually_exclusive_group()
    locations.add_argument("--root", help="模型根目录（默认 D:/Meetings/Models）")
    locations.add_argument("--config", type=Path, help="按 TOML 中各模型的目录下载")
    parser.add_argument("--roles", nargs="+", choices=("sensevoice", "vad", "speaker"), help="只准备指定模型")
    args = parser.parse_args()

    from meetingflow.analyze import verify_models
    from meetingflow.pipeline import load_settings

    root = Path(args.root or "D:/Meetings/Models").expanduser().resolve()
    try:
        settings = load_settings(args.config)["models"] if args.config else {
            "sensevoice_dir": root / MODELS[0]["dir_name"], "vad_dir": root / MODELS[1]["dir_name"], "spk_dir": root / MODELS[2]["dir_name"],
        }
    except (OSError, ValueError) as error:
        print(f"无法读取模型目录配置：{error}", file=sys.stderr)
        return 1
    fields = {"sensevoice": "sensevoice_dir", "vad": "vad_dir", "speaker": "spk_dir"}
    selected = [spec for spec in MODELS if args.roles is None or spec["role"] in args.roles]

    failures = 0
    for spec in selected:
        target = settings[fields[spec["role"]]]
        spec = {**spec, "dir_name": target.name}
        print(f"\n[{spec['role']}] {spec['model_id']} @ {spec['revision'][:7]} → {target}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                print("  正式目录已存在，仅在最后执行只读校验")
            else:
                print("  下载中（首次需要联网）...")
            prepared = _prepare_model(target.parent, spec)
            print(f"  已准备：{prepared}")
        except Exception as error:
            print(f"  失败: {error}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"\n{failures} 个模型失败", file=sys.stderr)
        return 1

    print("\n调用 verify_models 校验...")
    try:
        for spec in selected:
            verify_models(settings, only_role=spec["role"])
    except Exception as error:
        print(f"  校验失败: {error}", file=sys.stderr)
        return 1
    print("  校验通过")

    print("\n准备完成。日常推理无需联网：")
    for spec in selected:
        print(f"  - {settings[fields[spec['role']]]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
