@echo off
setlocal
title Duck2API Gateway

rem ============================================================
rem  Duck2API Gateway launcher (extracted from duck.apk)
rem  Double-click to start. Close window / Ctrl+C to stop.
rem  Keep this file ASCII-only to avoid CMD codepage issues.
rem ============================================================
set "HOST=127.0.0.1"
set "PORT=7749"
set "KEY="
set "EXTRA=--tools emulated"
set "APPDIR=E:\hds(js)\look\duck-apk"
set "PYEXE=C:\Users\admin\.agent-reach-venv\Scripts\python.exe"
set "SCRIPT=duck2api_proxy.py"

echo ============================================================
echo   Duck2API Gateway
echo   Base URL : http://%HOST%:%PORT%/v1
echo   API Key  : %KEY%
echo              empty = no auth, any key works
echo ============================================================
echo.

netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [i] Port %PORT% is already in use - gateway is probably running.
  echo     Health check:
  "%SystemRoot%\System32\curl.exe" -s --noproxy "*" --max-time 5 "http://%HOST%:%PORT%/health"
  echo.
  echo     Nothing to do. If you want to restart it, stop the
  echo     existing process on port %PORT% first.
  goto :done
)

if not exist "%PYEXE%" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [X] Python not found. Edit PYEXE in this script.
    goto :done
  )
  set "PYEXE=python"
)

cd /d "%APPDIR%"
if errorlevel 1 (
  echo [X] Cannot enter APPDIR. Edit APPDIR in this script.
  goto :done
)
if not exist "%SCRIPT%" (
  echo [X] Missing target file: %SCRIPT%
  goto :done
)

echo [i] Starting gateway ... press Ctrl+C to stop.
echo.
rem EXTRA: --tools off is recommended for agent GUI clients with many MCP tools.
rem Change to --tools emulated only if you want prompt-based tool-call emulation.
set "ARGS=--host %HOST% --port %PORT% %EXTRA%"
if not "%KEY%"=="" set "ARGS=%ARGS% --key %KEY%"
"%PYEXE%" "%SCRIPT%" %ARGS%
echo.
echo [i] Gateway stopped. Exit code: %errorlevel%

:done
echo.
if "%DUCK_NOPAUSE%"=="1" exit /b 0
echo Press any key to close this window ...
pause >nul
endlocal
