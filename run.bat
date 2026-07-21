@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "MEETINGFLOW_SOURCE=%~1"
start "MeetingFlow" powershell.exe -NoExit -NoProfile -Command "$env:HF_TOKEN = [Environment]::GetEnvironmentVariable('HF_TOKEN', 'User'); $env:PYTHONUTF8 = '1'; Set-Location -LiteralPath '%~dp0'; $source = $env:MEETINGFLOW_SOURCE; $config = Join-Path (Get-Location) 'config\meetingflow.toml'; $configArgs = if (Test-Path -LiteralPath $config) { @('--config', $config) } else { @() }; if ([string]::IsNullOrWhiteSpace($source)) { & uv run --no-sync meetingflow @configArgs } else { & uv run --no-sync meetingflow @configArgs process $source }"
