# install_task.ps1 - 注册 Windows 任务计划程序：每天定时跑真实采集并推送 GitHub
# 用法（管理员 PowerShell）：
#   .\scripts\install_task.ps1                  # 默认：每天 08:00
#   .\scripts\install_task.ps1 -Time "07:30"
param(
    [string]$Time = "08:00",
    [string]$TaskName = "TikTokShopAnalytics"
)

$ProjectDir = "D:\workplace\tiktok-shop-analytics"
$Script = Join-Path $ProjectDir "scripts\push_report.ps1"

if (-not (Test-Path $Script)) {
    Write-Host "[错误] 未找到脚本: $Script" -ForegroundColor Red
    exit 1
}

$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "TikTok Shop 每日真实数据采集+推送 GitHub Pages（$Time）" -Force | Out-Null

Write-Host "已注册任务: $TaskName（每天 $Time 运行 push_report.ps1）" -ForegroundColor Green
Write-Host "立即试跑:   powershell -NoProfile -ExecutionPolicy Bypass -File `"$Script`""
Write-Host "查看任务:   Get-ScheduledTask -TaskName $TaskName"
Write-Host "删除任务:   Unregister-ScheduledTask -TaskName $TaskName"