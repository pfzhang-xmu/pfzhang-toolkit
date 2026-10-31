#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$PythonExe = $null
$PythonArgs = @()
foreach ($candidate in @("py -3.12", "python")) {
    $parts = $candidate.Split(" ")
    $command = Get-Command $parts[0] -ErrorAction SilentlyContinue
    if ($null -eq $command) { continue }
    $launcherArgs = @()
    if ($parts.Length -gt 1) { $launcherArgs = $parts[1..($parts.Length - 1)] }
    try {
        $version = & $command.Source @launcherArgs --version 2>&1
    } catch {
        continue
    }
    if ($version -match "3\.(1[2-9]|[2-9][0-9])") {
        $PythonExe = $command.Source
        $PythonArgs = $launcherArgs
        break
    }
}
if ($null -eq $PythonExe) {
    throw "需要 Python 3.12+。安装：winget install Python.Python.3.12，然后重开 PowerShell。"
}

& $PythonExe @PythonArgs -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

$profiles = Join-Path $env:APPDATA "Tencent\xwechat\radium\web\profiles"
if (-not (Test-Path $profiles)) {
    Write-Host "提醒：还没找到微信的 webview 目录。先在电脑微信打开「厦大体育」小程序并进入一次场馆预约。" -ForegroundColor Yellow
}

$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "XMU Gym.lnk"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = Join-Path $Root "scripts\start-windows.bat"
$link.WorkingDirectory = $Root
$link.Description = "XMU Gym"
$link.Save()

Write-Host ""
Write-Host "安装完成。双击桌面 XMU Gym，或运行 scripts\start-windows.bat" -ForegroundColor Green
Write-Host "演示模式：scripts\start-windows.bat --demo --port 1431"
