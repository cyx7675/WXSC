@echo off
rem ============================================================
rem   Wireless Telemetry Host - Flicker Diagnosis (GPU backend)
rem
rem   Starts the app FROM SOURCE with a selectable graphics
rem   backend, so you can find one that does not flicker
rem   WITHOUT rebuilding the exe (takes seconds, not minutes).
rem
rem   Background: this machine is an Optimus laptop (NVIDIA
rem   discrete GPU renders, integrated GPU drives the display),
rem   so frames cross GPUs once. If that copy is not in sync
rem   with Qt's compositor, you get occasional flicker.
rem   Switching the graphics backend may avoid that path and
rem   give you both smoothness AND no flicker.
rem
rem   NOTE: this file is kept pure-ASCII on purpose. Windows cmd
rem   reads .bat in the local codepage (GBK on Chinese Windows),
rem   so any UTF-8 text here would turn into mojibake and break
rem   parsing. All user-facing messages are printed by
rem   tools\gl_probe.py instead (in Chinese, UTF-8).
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "PYTHONIOENCODING=utf-8"
"%PY%" "tools\gl_probe.py"

if errorlevel 1 (
  echo.
  echo   Probe exited with an error. See the message above.
  echo.
  pause
)
