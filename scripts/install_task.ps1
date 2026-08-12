# install_task.ps1 - 注册 Windows 任务计划程序，每天定时运行 TikTok Shop 数据管道
# 用法（管理员 PowerShell）：
#   .\scripts\install_task.ps1                                  # 默认：每天 08:30 用模拟数据
#   .\scripts\install_task.ps1 -Keyword "yoga mat" -Time "07:00" # 每天 07:00 真实采集
param(
    [string]$Keyword = "",
    [string]$Time = "08:30",
    [string]$TaskName = "TikTokShopAnalytics"
)

$Python = "D:\Python\python.exe"
$ProjectDir = "D:\workplace\tiktok-shop-analytics"

if (-not (Test-Path $Python)) {
    Write-Host "[错误] 未找到 Python: $Python" -ForegroundColor Red
    exit 1
}

$args = "main.py schedule --once"
if ($Keyword) { $args += " --keyword `"$Keyword`"" } else { $args += " --demo" }

$action = New-ScheduledTaskAction -Execute $Python -Argument $args -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $taskSettings -Description "TikTok Shop 每日数据管道（$Time）" -Force | Out-Null

Write-Host "已注册任务: $TaskName（每天 $Time，参数: $args）" -ForegroundColor Green
Write-Host "查看:     Get-ScheduledTask -TaskName $TaskName"
Write-Host "删除:     Unregister-ScheduledTask -TaskName $TaskName"
