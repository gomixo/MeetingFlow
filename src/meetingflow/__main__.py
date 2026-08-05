from __future__ import annotations

import argparse
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

from .pipeline import (
    ProcessResult,
    Settings,
    completed_jobs,
    job_speakers,
    load_settings,
    output_formats,
    process,
    rename_speaker,
    render,
    retry,
    save_output_formats,
    wait_until_stable,
)

_MEDIA_SUFFIXES = {".aac", ".flac", ".m4a", ".mka", ".mkv", ".mp3", ".mp4", ".wav", ".webm"}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    warnings.filterwarnings("ignore", message=".*TensorFloat-32.*", category=UserWarning)
    warnings.filterwarnings(
        "ignore",
        message="Passing `gradient_checkpointing` to a config initialization is deprecated.*",
        category=UserWarning,
        module=r"transformers\.configuration_utils",
    )
    parser = argparse.ArgumentParser(prog="meetingflow", description="本地会议音频转写")
    parser.add_argument("--config", type=Path, help="TOML 配置文件路径")
    commands = parser.add_subparsers(dest="command")
    command = commands.add_parser("process", help="处理一个已完成写入的音频或视频文件")
    command.add_argument("source", type=Path)
    command.add_argument("--force", action="store_true", help="即使已成功处理也重新执行")
    render_command = commands.add_parser("render", help="应用 speaker-map.toml 重新生成发言人转写")
    render_command.add_argument("job_id", help="任务 ID 或其不含歧义的前缀")
    retry_command = commands.add_parser("retry", help="从指定阶段重新处理失败任务")
    retry_command.add_argument("job_id", help="任务 ID 或其不含歧义的前缀")
    retry_command.add_argument("--from", dest="from_stage", choices=("probe", "normalize", "transcribe", "diarize"), required=True)
    arguments = parser.parse_args()
    try:
        settings = load_settings(arguments.config)
        if arguments.command is None:
            _menu(settings)
        elif arguments.command == "process":
            result = process(arguments.source, settings, start_stage="probe" if arguments.force else None)
            print(f"任务已成功处理，已跳过：{result.output_dir}" if result.skipped else f"处理完成：{result.output_dir}")
        elif arguments.command == "render":
            print(f"重新渲染完成：{render(arguments.job_id, settings)}")
        elif arguments.command == "retry":
            result = retry(arguments.job_id, arguments.from_stage, settings)
            print(f"重试完成：{result.output_dir}")
        return 0
    except Exception:
        logging.getLogger(__name__).exception("处理失败")
        print("处理失败。请查看 Work/jobs 中的 run.jsonl 和控制台错误。", file=sys.stderr)
        return 1


def _menu(settings: Settings) -> None:
    while True:
        formats = "+".join(item.upper() for item in output_formats(settings))
        print(
            f"\nMeetingFlow（当前输出：{formats}）\n1. 转写 Inbox 中最新文件\n2. 转写拖入或粘贴的文件\n3. 修改发言人姓名\n4. 设置输出格式\n0. 退出"
        )
        choice = input("请选择：").strip()
        if choice == "0":
            return
        try:
            if choice == "1":
                source = _latest_media(settings["inbox"])
                print(f"已选择：{source}")
                print("正在确认文件已写入完成...")
                wait_until_stable(source)
                _print_process(process(source, settings))
            elif choice == "2":
                value = input("请拖入文件或粘贴完整路径：").strip()
                _print_process(process(_input_path(value), settings))
            elif choice == "3":
                _rename_menu(settings)
            elif choice == "4":
                _formats_menu(settings)
            else:
                print("无效选项，请输入 0—4。", file=sys.stderr)
        except (OSError, ValueError) as error:
            print(f"操作失败：{error}", file=sys.stderr)
        except Exception:
            logging.getLogger(__name__).exception("菜单操作失败")
            print("处理失败。请查看 Work/jobs 中的 run.jsonl 和控制台错误。", file=sys.stderr)


def _latest_media(inbox: Path) -> Path:
    if not inbox.is_dir():
        raise ValueError(f"Inbox 文件夹不存在：{inbox}")
    files = [path for path in inbox.iterdir() if path.is_file() and path.suffix.lower() in _MEDIA_SUFFIXES]
    if not files:
        raise ValueError(f"Inbox 中没有可处理的音频或视频：{inbox}")
    return max(files, key=lambda path: path.stat().st_mtime_ns)


def _input_path(value: str) -> Path:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    if not value:
        raise ValueError("没有输入文件路径")
    return Path(value)


def _rename_menu(settings: Settings) -> None:
    while True:
        jobs = completed_jobs(settings)
        if not jobs:
            raise ValueError("还没有成功处理的任务")
        print("\n请选择任务：")
        for index, job in enumerate(jobs, 1):
            date = datetime.fromtimestamp(job.modified_at).strftime("%Y-%m-%d %H:%M") if job.modified_at else "日期未知"
            print(f"{index}. [{date}] {job.source.name} ({job.job_id[:8]})")
        print("0. 返回主菜单")
        choice = input("任务编号：").strip()
        if choice == "0":
            return
        try:
            job = jobs[_number(choice, len(jobs)) - 1]
        except ValueError as error:
            print(f"操作失败：{error}", file=sys.stderr)
            continue
        _speaker_menu(job.job_id, settings)


def _speaker_menu(job_id: str, settings: Settings) -> None:
    while True:
        try:
            speakers = job_speakers(job_id, settings)
        except (OSError, ValueError) as error:
            print(f"操作失败：{error}", file=sys.stderr)
            return
        if not speakers:
            print("操作失败：该任务没有识别到发言人", file=sys.stderr)
            return
        print("\n请选择发言人：")
        for index, (label, name) in enumerate(speakers, 1):
            print(f"{index}. {name} ({label})")
        print("0. 返回任务列表")
        choice = input("发言人编号：").strip()
        if choice == "0":
            return
        try:
            label = speakers[_number(choice, len(speakers)) - 1][0]
            name = input("请输入姓名（支持中文）：")
            print(f"姓名已更新：{rename_speaker(job_id, label, name, settings)}")
        except (OSError, ValueError) as error:
            print(f"操作失败：{error}", file=sys.stderr)


def _formats_menu(settings: Settings) -> None:
    print("\n1. Markdown\n2. SRT\n3. Markdown + SRT")
    choices = {"1": ("md",), "2": ("srt",), "3": ("md", "srt")}
    choice = input("请选择：").strip()
    if choice not in choices:
        raise ValueError("输出格式选项必须是 1、2 或 3")
    save_output_formats(settings, choices[choice])
    print("输出格式设置已保存。")


def _number(value: str, maximum: int) -> int:
    try:
        number = int(value.strip())
    except ValueError as error:
        raise ValueError("请输入有效编号") from error
    if not 1 <= number <= maximum:
        raise ValueError(f"编号必须在 1—{maximum} 之间")
    return number


def _print_process(result: ProcessResult) -> None:
    print(f"任务已成功处理，已跳过：{result.output_dir}" if result.skipped else f"处理完成：{result.output_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
