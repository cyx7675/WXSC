@echo off
rem ============================================================
rem   Wireless Telemetry Host - One-Click Build
rem   Double click this file to rebuild the exe from sources.
rem
rem   Optional arguments (drag this file into a cmd window and
rem   append one of these):
rem
rem     --check-only    run checks only, no build (about 2 seconds)
rem     --quick         skip checks, build only
rem     --no-explorer   do not open the output folder when done
rem
rem   Full log  : tools\_build_log.txt
rem   NOTE: this file is kept pure-ASCII on purpose. Windows cmd
rem   reads .bat in the local codepage (GBK on Chinese Windows),
rem   so any UTF-8 text here would turn into mojibake and break
rem   parsing. All user-facing messages are printed by
rem   build_web.py instead (in Chinese, UTF-8).
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   Python: %PY%
echo.
echo   Tip: append --check-only to run checks only (2 seconds).
echo.

"%PY%" build_web.py %*

if errorlevel 1 (
  echo.
  echo   BUILD FAILED. See the message above.
  echo   Full log: tools\_build_log.txt
  echo.
  pause
  exit /b 1
)

echo.
echo   Done. Press any key to close.
pause >nul
