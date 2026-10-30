"""桌面窗口的耗时校验和渲染操作，独立进程执行。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path

from .desktop_environment import check_environment, create_directories
from .pipeline import job_speakers, load_settings, rename_speaker, render


def main() -> int:
    parser = argparse.ArgumentParser(description="MeetingFlow 桌面操作")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("operation", choices=("check", "create-directories", "rename", "render"))
    arguments = parser.parse_args()
    try:
        settings = load_settings(arguments.config)
        request: object = json.load(sys.stdin)
        if not isinstance(request, dict) or any(not isinstance(value, str) for value in request.values()):
            raise ValueError("桌面请求格式无效")
        if arguments.operation == "check":
            with redirect_stdout(sys.stderr):
                checks = check_environment(settings)
            print(json.dumps({"ok": True, "checks": checks}, ensure_ascii=False))
            return 0
        elif arguments.operation == "create-directories":
            create_directories(settings)
        elif arguments.operation == "rename":
            rename_speaker(request["job_id"], request["speaker"], request["name"], settings)
        else:
            render(request["job_id"], settings)
        speakers = job_speakers(request["job_id"], settings) if "job_id" in request else []
        print(json.dumps({"ok": True, "speakers": speakers}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": {"message": str(error)}}, ensure_ascii=False))
        return 1
    except Exception:
        logging.getLogger(__name__).exception("桌面操作失败")
        print(json.dumps({"ok": False, "error": {"message": "桌面操作失败，请查看任务日志。"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
