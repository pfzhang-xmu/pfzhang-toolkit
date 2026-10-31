#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TaskName = "XMU Gym"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"& '$Root\scripts\start-windows.bat' --no-browser`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "XMU Gym local page and 06:58 scheduler" -Force | Out-Null

Write-Host "已安装登录自启：$TaskName" -ForegroundColor Green
Write-Host "卸载：Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
