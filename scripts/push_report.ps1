# push_report.ps1 - 本地多源采集 + 生成看板 + 自动推送 GitHub（零成本每日任务）
# 由 scripts/install_task.ps1 注册为 Windows 计划任务，或手动执行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push_report.ps1
# 数据源优先级：EchoTik API -> FastMoss API -> demo（main.py --source auto 自动故障切换）
# Key 存放（均已 gitignore）：
#   data/api_key.txt       = EchoTik 的 Base64(Basic 凭据)
#   data/fastmoss_key.txt  = FastMoss 的 client_secret
#   也可用环境变量 TTSHOP_API_KEY / FAST_MOSS_API_KEY 替代。

$ErrorActionPreference = "Continue"
$Repo    = "D:\workplace\tiktok-shop-analytics"
$Python  = if (Test-Path "D:\Python\python.exe") { "D:\Python\python.exe" } else { "python" }
Set-Location $Repo

# 清掉可能残留的代理环境变量（本机经 Cloudflare 全局路由可直接访问 GitHub/数据 API）
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
}

# ---------- Keys ----------
$EchoKey = ""
if (Test-Path (Join-Path $Repo "data\api_key.txt")) { $EchoKey = (Get-Content (Join-Path $Repo "data\api_key.txt") -Raw).Trim() }
if (-not $EchoKey) { $EchoKey = $env:TTSHOP_API_KEY }
$FmKey = ""
if (Test-Path (Join-Path $Repo "data\fastmoss_key.txt")) { $FmKey = (Get-Content (Join-Path $Repo "data\fastmoss_key.txt") -Raw).Trim() }
if (-not $FmKey) { $FmKey = $env:FAST_MOSS_API_KEY }
$echoArgs = @()
if ($EchoKey) { $echoArgs = @("--api-key", $EchoKey); Log "EchoTik Key 已加载" } else { Log "无 EchoTik Key（将跳过 EchoTik 采集）" }
if ($FmKey)   { Log "FastMoss Key 已加载（作为兜底数据源）" } else { Log "无 FastMoss Key（将跳过 FastMoss 兜底）" }

$script:finalSource = "demo"
Log "===== 开始每日采集与推送 ====="

# 1) 商品采集：--source auto 自动故障切换（EchoTik API -> FastMoss API -> demo）
Step "商品采集（auto: EchoTik -> FastMoss -> demo）" {
    & $Python main.py run --source auto --category-id 603084 --pages 5 --sort sales7d --enrich @echoArgs
    if ($LASTEXITCODE -eq 0) {
        if (Select-String -LiteralPath "reports\tiktok_shop_report.html" -Pattern "数据源：api" -Quiet) {
            $script:finalSource = "api"
            Log "    实际数据源: api"
        } else {
            Log "    实际数据源: demo"
        }
    } else {
        Log "    !!! auto 全链路失败"
    }
}

# 2-4) EchoTik 扩展采集（关键词/达人/飙升关键词，仅在 EchoTik Key 存在时尝试）
if ($EchoKey) {
    Step "关键词采集 yoga mat"   { & $Python main.py run --source api --keyword "yoga mat" --limit 30 --with-influencers @echoArgs }
    Step "达人榜采集"             { & $Python main.py influencers --sort followers --pages 2 --limit 20 @echoArgs }
    Step "飙升关键词采集"         { & $Python main.py keywords --tab all --limit 15 @echoArgs }
} else {
    Log "跳过关键词/达人/飙升关键词（无 EchoTik Key）"
}

# 5) 异动告警（生成 reports/alerts_*.md，看板"今日异动"面板依赖）
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
    $msg = "chore(data): 每日数据更新 " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    git -c http.proxy= -c https.proxy= commit -m $msg 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    git -c http.proxy= -c https.proxy= pull --rebase --autostash origin main 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    git -c http.proxy= -c https.proxy= push origin main 2>&1 | ForEach-Object { Add-Content -LiteralPath $LogFile -Value ("      " + $_) -Encoding UTF8 }
    if ($LASTEXITCODE -ne 0) { Log "!!! 推送失败，请检查网络或仓库地址"; exit 1 }
    Log "已推送提交: $msg"
} else {
    Log "报告无变化，跳过提交"
}

Log ("===== 完成（数据源: " + $script:finalSource + "）=====")
exit 0