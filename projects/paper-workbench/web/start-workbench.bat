@echo off
rem Paper Workbench Web launcher (start local workbench)
cd /d "%USERPROFILE%\.dsh\papers\workbench\web"
start "" http://127.0.0.1:8123
python server.py 8123
