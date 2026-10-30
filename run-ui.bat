@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
uv run --no-sync meetingflow-gui %*
if errorlevel 1 (
    echo 启动失败。首次运行请在项目目录执行 uv sync，并确认 uv 已加入 PATH。
    pause
)
