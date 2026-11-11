@echo off
chcp 65001 >nul
title DeepSeek Web Proxy :18088
set "PROJECT=E:\project\codex_desktop\workplace\DeepSeekWebProxy"
set "PYW=C:\Users\admin\.agent-reach-venv\Scripts\pythonw.exe"
set "DEEPSEEK_HOST=127.0.0.1"
set "DEEPSEEK_PORT=18088"
set "DEEPSEEK_WATCH_LOCK_PORT=18089"
set "DEEPSEEK_PYTHONW=%PYW%"
set "DEEPSEEK_WEB_PROXY_NO_BROWSER=1"
set "DEEPSEEK_WEB_PROXY_QUIET=1"
set "NO_PROXY=127.0.0.1,localhost"
set "PYTHONWARNINGS=ignore"
cd /d "%PROJECT%"
if not exist "%PYW%" set "PYW=C:\Users\admin\.agent-reach-venv\Scripts\python.exe"
start "" /min "%PYW%" "%PROJECT%\_desktop_start.py"
exit /b 0
