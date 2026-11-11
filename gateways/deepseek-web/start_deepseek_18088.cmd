@echo off
rem Portable Windows launcher: auto-detect Python instead of a hardcoded path.
setlocal
set "DEEPSEEK_PORT=18088"
set "DEEPSEEK_HOST=127.0.0.1"
set "PY=%DEEPSEEK_PYTHONW%"
if not defined PY (
  for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do if not defined PY set "PY=%%i"
)
if not defined PY set "PY=python.exe"
start "" /min "%PY%" "%~dp0_watch_deepseek_18088.py"
endlocal
exit /b 0
