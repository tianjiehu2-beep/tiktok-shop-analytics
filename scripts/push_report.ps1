# push_report.ps1 - 本地真实采集 + 生成看板 + 自动推送 GitHub（零成本每日任务）
# 由 scripts/install_task.ps1 注册为 Windows 计划任务，或手动执行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push_report.ps1
# 说明：
#   - API Key 优先读取 data/api_key.txt（已被 gitignore），其次 TTSHOP_API_KEY 环境变量；
#     没有 Key 时自动退回 demo 数据，保证报告总能生成。
#   - 每次运行会把 reports/ 下的 HTML/CSV/告警提交推送到 main，CI 检测到真实数据后直接部署。

$ErrorActionPreference = "Continue"
$Repo    = "D:\workplace\tiktok-shop-analytics"
$Python  = if (Test-Path "D:\Python\python.exe") { "D:\Python\python.exe" } else { "python" }
Set-Location $Repo

# 清掉可能残留的代理环境变量（本机经 Cloudflare 全局路由可直接访问 GitHub/EchoTik）
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY,Env:http_proxy,Env:https_proxy,Env:all_proxy -ErrorAction SilentlyContinue

$LogDir   = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile  = Join-Path $LogDir ("push_report_" + (Get-Date -Format "yyyyMMdd") + ".log")

function Log([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}
function Step([string]$name, [scriptblock]$body) {
    Log ">>> $name"
    & $body 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    $rc = $LASTEXITCODE
    if ($rc -ne 0) { Log ("    !!! " + $name + " 未成功（exit " + $rc + "）") } else { Log ("    " + $name + " 完成") }
    return $rc
}

# ---------- API Key ----------
$Key = ""
if (Test-Path (Join-Path $Repo "data\api_key.txt")) { $Key = (Get-Content (Join-Path $Repo "data\api_key.txt") -Raw).Trim() }
if (-not $Key) { $Key = $env:TTSHOP_API_KEY }
$apiArgs = @()
if ($Key) { $apiArgs = @("--api-key", $Key); Log "API Key 已加载（本次使用真实数据）" }
else      { Log "未找到 API Key，本次将使用 demo 数据" }

$script:finalSource = "demo"
Log "===== 开始每日采集与推送 ====="

# 1) 类目商品采集（失败自动降级 demo 300 条，保证报告存在）
Step "采集类目商品 category=603084" {
    & $Python main.py run --source api --category-id 603084 --pages 5 --sort sales7d --enrich @apiArgs
    if ($LASTEXITCODE -ne 0) {
        Log "    类目 API 失败，降级 demo"
        & $Python main.py run --source demo --products 300
    } else {
        $script:finalSource = "api"
    }
}

# 2-4) 关键词 / 达人 / 飙升关键词（仅在有 Key 时执行）
if ($Key) {
    Step "关键词采集 yoga mat"   { & $Python main.py run --source api --keyword "yoga mat" --limit 30 --with-influencers @apiArgs }
    Step "达人榜采集"             { & $Python main.py influencers --sort followers --pages 2 --limit 20 @apiArgs }
    Step "飙升关键词采集"         { & $Python main.py keywords --tab all --limit 15 @apiArgs }
} else {
    Log "跳过关键词/达人/飙升关键词（无 API Key）"
}

# 5) 异动告警（生成 reports/alerts_*.md，看板“今日异动”面板依赖）
Step "异动告警检测" { & $Python main.py alerts }

# 6) 汇总生成最终看板（含达人/关键词/异动面板）
Step "生成最终看板" { & $Python main.py report --source $script:finalSource }

# 7) 复制为 Pages 首页
if (Test-Path "reports\tiktok_shop_report.html") {
    Copy-Item -Force "reports\tiktok_shop_report.html" "reports\index.html"
    Log "已生成 reports/index.html"
} else {
    Log "!!! 报告未生成，退出"
    exit 1
}

# ---------- 提交并推送 ----------
git add reports 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
$changed = git status --porcelain reports
if ($changed) {
    $msg = "chore(data): 每日真实数据更新 " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    git -c http.proxy= -c https.proxy= commit -m $msg 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    git -c http.proxy= -c https.proxy= pull --rebase --autostash origin main 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    git -c http.proxy= -c https.proxy= push origin main 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    if ($LASTEXITCODE -ne 0) { Log "!!! 推送失败，请检查网络或仓库地址"; exit 1 }
    Log "已推送提交: $msg"
} else {
    Log "报告无变化，跳过提交"
}

Log "===== 完成 ====="
exit 0