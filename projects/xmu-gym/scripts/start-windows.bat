@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo 还没安装依赖：先运行 scripts\setup-windows.ps1
  exit /b 1
)
".venv\Scripts\python.exe" -m app.server %*
