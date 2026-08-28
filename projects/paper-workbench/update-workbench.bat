@echo off
setlocal
cd /d %~dp0
for /f "delims=" %%R in ('git rev-parse --show-toplevel 2^>nul') do set REPO_ROOT=%%R
if not defined REPO_ROOT (echo 请在 Git 工作区内运行此脚本。 & exit /b 1)
set STAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%-%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%
set BACKUP=backups\%STAMP%
if not exist "%BACKUP%" mkdir "%BACKUP%"
if exist app_config.json copy /Y app_config.json "%BACKUP%\app_config.json" >nul
if exist data xcopy /E /I /Y data "%BACKUP%\data" >nul
for %%F in (.dsh_workbench_session .dsh_workbench_session.lock .dsh_workbench_pool.json .last-project) do if exist %%F copy /Y %%F "%BACKUP%\" >nul
cd /d "%REPO_ROOT%"
git fetch --tags origin
if errorlevel 1 exit /b 1
git pull --ff-only
cd /d %~dp0
if exist .venv\Scripts\pip.exe .venv\Scripts\pip.exe install -r requirements.txt
echo Paper Workbench 更新完成，备份目录: %BACKUP%
