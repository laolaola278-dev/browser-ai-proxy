@echo off
setlocal
title Browser AI Gateway - Start All
rem One-click launcher for the 4 bundled browser-AI proxies.
rem ASCII-only content to avoid CMD codepage issues.
set "ROOT=C:\Users\admin\Desktop\browser-ai-proxy"
set "NODE=E:\dsh-home\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%NODE%" set "NODE=node"
echo ============================================================
echo   Browser AI Gateway : 4 providers
echo   quark + yuanbao : inline Node (no process needed)
echo   deepseek-web + duck : local Python gateways (start on demand)
echo ============================================================
echo.
netstat -ano | findstr ":8976" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo [1/3] Starting unified gateway ...
  start "BrowserAI-Gateway" /min "%NODE%" "%ROOT%\server.mjs"
  ping -n 6 127.0.0.1 >nul 2>&1
) else (
  echo [1/3] Unified gateway already running.
)
echo [2/3] Starting local gateways via /api/groups/:id/start ...
curl.exe -s --noproxy "*" --max-time 8 -X POST "http://127.0.0.1:8976/api/groups/duck/start
echo.
curl.exe -s --noproxy "*" --max-time 8 -X POST "http://127.0.0.1:8976/api/groups/deepseek-web/start
echo.
echo [3/3] Provider status:
curl.exe -s --noproxy "*" --max-time 8 "http://127.0.0.1:8976/api/providers
echo.
echo Panel: http://127.0.0.1:8976/   OpenAI: http://127.0.0.1:8976/v1/chat/completions
if not "%BROWSER_AI_NOOPEN%"=="1" start "" "http://127.0.0.1:8976/"
if "%BROWSER_AI_NOPAUSE%"=="1" exit /b 0
echo.
echo Press any key to close this window (services keep running).
pause >nul
endlocal
