# 在 Windows 任务计划程序中登记每天 02:00 的备份。使用本机时区。
$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "backup.ps1"
$dockerDir = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $dockerDir
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask `
    -TaskName "muzi-llm-postgres-backup" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Dump muzi_llm every day at 02:00 local time and keep 30 days." `
    -Force | Out-Null
Write-Output "registered muzi-llm-postgres-backup"
