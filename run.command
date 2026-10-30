#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUTF8=1
if ! command -v uv >/dev/null 2>&1; then
    print "未找到 uv。请安装 uv 后，在项目目录运行 uv sync。"
    read "?按 Enter 关闭窗口…"
    exit 1
fi
uv run --no-sync meetingflow-gui "$@"
status_code=$?
if (( status_code != 0 )); then
    print "启动失败。首次运行请在项目目录执行 uv sync。"
    read "?按 Enter 关闭窗口…"
fi
exit "$status_code"
