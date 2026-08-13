# TikTok Shop 爆品监测与选品分析系统

面向跨境电商（TikTok Shop 美区）的商品数据采集与选品决策项目。
自动完成「采集 → 存储 → 毛利测算 → 选品评分 → 可视化看板」全流程，可作为简历项目或日常选品工具。

## 特性
- 数据管道：商品库 + 价格/销量历史快照 + 分析快照，分层存储（SQLite）。
- 可插拔数据源：demo（模拟）/ scraper（Playwright 采集）/ api（第三方数据 API）/ auto（多源故障切换），管道层与数据来源解耦。
- 选品评分：需求度（销量+视频播放）× 竞争度（评论稀疏度）× 利润（跨境小包直发毛利模型）。
- 可视化：自包含 HTML 看板（离线可打开），含类目销量、选品评分、销量趋势图与爆品榜。
- 定时调度：每日自动采集 → 分析 → 出报告（标准库实现零依赖，支持 Windows 任务计划程序一键注册）。

## 目录结构
```
ttshop/
  config.py          # 配置（毛利参数、评分权重、区域、API）
  db.py              # SQLite 数据层
  models.py          # 数据模型
  demo_data.py       # 模拟美区商品数据生成器（离线演示）
  sources/           # 可插拔数据源：base / demo / scraper / api
  scraper/           # Playwright 采集器（scraper 数据源的实现）
  analysis/          # 毛利测算 + 选品评分
  pipeline.py        # 数据管道（采集→分析→报告），CLI 与调度器共用
  scheduler.py       # 定时调度器（标准库实现）
  report/            # HTML 看板 + CSV 导出
scripts/             # Windows 任务计划注册脚本
main.py              # CLI 入口
tests/               # 单元测试与端到端冒烟测试
resume.md            # 简历项目描述
```

## 快速开始（demo 模式，无需安装任何依赖）
```bash
# Windows（若 python 不在 PATH，用完整路径，如 D:\Python\python.exe）
python main.py run --demo
python main.py report
```
打开 `reports/tiktok_shop_report.html` 查看看板。

## 数据源（可插拔）
管道层只依赖统一的 `DataSource.fetch()` 接口，切换数据源不影响分析/报告/调度逻辑。

| 数据源 | 说明 | 适用场景 |
| --- | --- | --- |
| `demo` | 本地生成模拟美区商品数据，零依赖 | 演示全流程、CI、面试 Demo |
| `scraper` | Playwright 真实采集 TikTok Shop 搜索页 | 少量自采、验证反爬思路 |
| `api` | 对接第三方数据平台开放接口 | 生产环境、稳定批量数据 |
| `auto` | EchoTik API → FastMoss API → demo 自动故障切换（任一成功即停止） | 每日定时任务、无人值守 |

```bash
python main.py run --source demo --products 200              # 模拟数据
python main.py run --source scraper --keyword "yoga mat"     # Playwright 采集
python main.py run --source api --keyword "yoga mat" --api-key xxx
python main.py run --source auto --category-id 603084 --pages 5   # 多源自动故障切换
```

第三方 API 平台（Kalodata / EchoTik / FastMoss 等）注册账号后一般有免费额度：
```bash
set TTSHOP_API_KEY=xxx          # Windows 环境变量（推荐，避免 key 进命令行历史）
python main.py run --source api --keyword "yoga mat" --provider echotik
```

#### EchoTik（推荐：免费 100 次测试额度）
1. 打开 https://echotik.live/platform/api-keys 注册 / 登录，领取专属 `username` 和 `password`。
2. API Key = `Base64(username:password)`，Windows 一行算出：
   ```powershell
   [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("你的username:你的password"))
   ```
3. 把上一步结果设为环境变量（避免 key 进命令行历史）：
   ```powershell
   setx TTSHOP_API_KEY "Base64结果"
   set TTSHOP_API_KEY=Base64结果   # 当前窗口立即生效
   python main.py run --source api --keyword "yoga mat" --provider echotik
   ```
4. 请求：`GET https://open.echotik.live/api/v3/echotik/search/items?sk=关键词&type=2&size=30`，
   认证头 `Authorization: Basic <Base64>`，商品列表取 `data.list`（字段见 `normalize_item`）。
5. 按类目采集时用 `--category-id`（先 `python main.py categories --search <词>` 拿类目 ID），
   商品列表接口 `GET /api/v3/echotik/product/list` 每页最多 10 条，用 `--pages` 翻页。
各平台响应字段不同，在 `ttshop/sources/api.py` 的 `PROVIDERS` 中按官方文档调整
base_url / search_path / 鉴权方式 / items_path，字段归一化在 `normalize_item` 中完成。
新增数据源：实现 `ttshop/sources/base.py` 的 `DataSource` 并在 `get_source` 注册即可。

## 成熟采集：按类目 / 按具体商品 / 按店铺（EchoTik API）
采集器已对接 EchoTik 多个真实接口，支持类目树浏览、按类目分页采集、批量关键词、
按商品 ID 精准采集、按店铺整店采集、商品详情补全与商品榜单，落库后自动做
毛利测算、选品评分并生成看板。所有命令都能脱离 Codex 直接运行。

```bash
# 1) 浏览 / 搜索类目，拿到类目 ID（缓存到 data/categories.json）
python main.py categories                      # 列出全部一级类目
python main.py categories --search yoga        # 按名称搜类目（支持三级类目）
python main.py categories --refresh            # 强制刷新类目缓存

# 2) 按类目采集：翻页 + 按近7天销量排序 + 详情补全
python main.py run --source api --category-id 603084 --pages 5 --sort sales7d --enrich

# 3) 按具体东西采集：支持逗号分隔批量关键词，自动去重
python main.py run --source api --keyword "yoga mat,resistance band,pilates bar" --limit 30

# 4) 筛选条件：最低销量 / 最高均价 / 最低佣金率
python main.py run --source api --category-id 600001 --pages 3 --min-sales 1000 --max-price 30 --min-commission 0.1

# 5) 按具体商品 ID 采集：精准追踪某个商品/链接（逗号分隔多个，每批最多 20 个）
python main.py run --source api --product-ids 1729385586104308698,1731306663137743592

# 6) 按店铺采集：抓取某个卖家在售商品（--pages 控制翻页，每页最多 10 条）
python main.py run --source api --seller-id 7496125336660249320 --pages 5
python main.py shops add --seller-id 7496125336660249320   # 加入店铺监控，之后 shops new 看上新

# 7) 采集商品榜单（日/周/月榜）
python main.py ranklist --category-id 603084 --period day --limit 10
python main.py ranklist --period week --limit 10

# 8) 达人数据（人-货闭环）：带货达人榜 / 达人列表 / 商品关联达人
python main.py influencers --rank --period day --limit 10      # 达人带货榜（按销量）
python main.py influencers --sort followers --min-gmv 1000     # 高带货GMV达人列表
python main.py run --source api --keyword "yoga mat" --with-influencers   # 商品由谁在带

# 9) 关键词数据：趋势搜索词榜 / 关键词灵感（发现选题与选品方向）
python main.py keywords --tab all --limit 20                  # 飙升关键词
python main.py keywords --keyword yoga --limit 20             # 围绕 yoga 的相关热词

# 8) 趋势分析与爆品预测：7天/30天增速、新品检测、爆品指数（run 全流程自动计算）
python main.py trend --limit 15                              # 查看爆品预测榜


# 10) 竞品监控：关注商品池 + 同赛道竞品识别（价格/销量变动 + 竞品降价/爆量告警）
python main.py watch add-top 5              # 把销量 Top 5 加入关注池（或 watch add <商品ID>）
python main.py watch list                    # 查看关注池
python main.py competitors                    # 识别竞品并检测变动（run 全流程自动执行）

# 11) 店铺监控：卖家维度聚合 + 关注店铺上新检测（run 全流程自动执行）
python main.py shops                              # 同步店铺维度 + 查看 Top 店铺与关注列表
python main.py shops new                          # 关注店铺近 7 天新上架商品
python main.py shops add-top 5                    # 把销量 Top 5 店铺加入关注（或 shops add <sellerId>）
python main.py shops rm <sellerId>                # 移除关注店铺

# 12) 直播/短视频带货榜：直播场次销售数据（demo 生成器；第三方 live 接口可在 sources/api.py 扩展）
python main.py live                               # 采集直播场次并查看直播带货榜（按 GMV）

# 13) 类目洞察：规模 / 近7天销量 / 增速 / CR4集中度 / 蓝海指数（run 全流程自动计算）
python main.py catinsight                          # 查看类目洞察与机会类目

# 14) 今日变动：新上架 / 价格异动 / 销量激增（对比昨日状态）
python main.py changes                             # 查看今日变动汇总

# 9) 监控告警：每日异动检测（降价/爆量/新品上榜/店铺上新）+ 导出 + 推送（run 全流程自动检测）
python main.py alerts                                        # 查看今日异动
python main.py alerts --min-surge 200 --growth 2             # 调高异动阈值
python main.py alerts --webhook https://oapi.dingtalk.com/robot/send?access_token=xxx  # 推送钉钉/企业微信/飞书
```

支持的数据字段：
- 商品（`product/list` / `product/detail`）：售价区间、总销量、近 7 天/30 天销量、
  总 GMV、评分、评论数、佣金率、带货达人数、带货视频数、是否包邮/爆款/全托管、三级类目路径。
- 达人（`influencer/list` / `influencer/ranklist` / `product/influencer/list`）：粉丝数、
  视频数、互动率、EC分、带货量/GMV、平均带货视频播放、商品-达人带货关系。
- 关键词（`trending/keyword/ranking` / `inspiration/keyword`）：热词、视频数、热度、7天趋势。
- 趋势/爆品/预测（`price_snapshots` 时间序列 + `product_trends` + `product_forecasts`）：近7天/30天销量增量、
  7天增速、新品检测、爆品指数、未来7/30天销量预测、生命周期（导入/成长/成熟/衰退）与选品推荐理由。
- 店铺（`sellers` 聚合 + `shop_watch` 关注池）：卖家商品数/累计销量/GMV/均价、关注店铺上新检测与告警。
- 直播（`live_sessions`）：直播场次 GMV/销量/峰值观看/时长，直播带货榜；短视频带货榜按带货视频数排序。
看板已包含商品/类目/达人/关键词/爆品预测/店铺监控/内容带货榜/今日变动八个维度；类目洞察含蓝海指数（增速×分散度×进入门槛×市场量级，>=50 为机会类目）。

## 常用命令
```bash
python main.py init                          # 初始化数据库
python main.py seed --products 300           # 生成模拟商品（可重复执行，模拟增量采集）
python main.py categories --search yoga      # 搜类目拿 ID（EchoTik）
python main.py analyze                       # 运行选品评分与毛利分析
python main.py report                        # 生成 HTML 看板 + top_products.csv
python main.py stats                         # 查看数据规模
python main.py run --demo                    # 一键全流程（模拟数据）
python main.py run --source api --category-id 603084 --pages 3   # 按类目真实采集
python main.py run --source api --keyword "yoga mat"             # 按关键词真实采集
python main.py run --source api --product-ids 1729385586104308698 # 按商品ID精准采集
python main.py run --source api --seller-id 7496125336660249320 --pages 3  # 按店铺整店采集
python main.py ranklist --period day         # 采集商品榜单
python main.py schedule --once --demo        # 立即执行一次（测试）
python main.py schedule --time 08:30 --demo  # 每天 08:30 自动执行
```

## Web UI 看板（本地运行 / 免费上线）

基于 Streamlit 的可视化界面，支持在浏览器里选类目、输入商品/店铺 ID 采集数据并查看图表，
不依赖 Codex，任何电脑都能用。界面复用 `ttshop` 全部采集/分析能力，共 5 页：
总览看板 / 类目采集 / 商品采集 / 店铺采集 / 数据快照。

### 本地启动（Windows）

```powershell
python -m pip install -r requirements.txt
python -m streamlit run ui/app.py
```

或直接双击 `ui/start_ui.bat`。浏览器自动打开 http://localhost:8501 。

### 免费上线（Streamlit Community Cloud，永久网址）

1. 把代码 push 到 GitHub（仓库可保持私有）。
2. 打开 https://share.streamlit.io ，用 GitHub 账号登录 → New app → 选择本仓库，
   入口文件填 `ui/app.py` → Deploy，获得 https://<名字>.streamlit.app 永久网址。
3. 在 App Settings → Secrets 里配置（一行一个）：
   ```toml
   TTSHOP_API_KEY = "<EchoTik 的 base64(用户名:密码)>"
   APP_PASSWORD = "<你设置的访问密码>"
   ```
   - `TTSHOP_API_KEY` 就是 `data/api_key.txt` 里那一串，代码读不到本地文件时自动用云端密钥；
   - `APP_PASSWORD` 开启访问密码门，防止陌生人消耗你的 EchoTik 免费额度（可留空=不设密码）。
4. 以后本地 push 代码，云端自动重新部署。

### 云端注意事项

- 免费档的数据库文件会在应用重启/休眠后重置，请在「数据快照」页导出 `.db` 快照保存到本地；
  换设备/换电脑后用「导入快照」恢复。
- 应用闲置一段时间会休眠，下次打开等待 30~60 秒唤醒属正常现象。
- 定时更新建议留在本地 CLI：`python main.py schedule --time 08:30 --source api --category-id <ID>`。

## 在线看板（GitHub Pages，可选）
仓库内置 GitHub Actions 工作流（`.github/workflows/daily-report.yml`）：每天自动生成数据与
HTML 看板并部署到 GitHub Pages，简历里可以放一个可点击的在线链接。

启用步骤（一次性）：
1. 仓库 Settings → Pages → Source 选择 **GitHub Actions**。
2. 推送后到 Actions 页面手动运行一次 `daily-report` 验证。
3. 有第三方 API Key 时，在仓库 Settings → Secrets and variables → Actions 添加
   `TTSHOP_API_KEY`（存在则自动用真实数据，否则回退 demo 数据）。
看板地址形如：`https://<用户名>.github.io/<仓库名>/index.html`。

## 每日自动更新（真实数据 + 推送 GitHub，免费）
本地每天定时跑真实 API 采集，生成看板后自动推送到 GitHub；CI 检测到已提交的真实数据时直接部署，不再用 demo 覆盖。

```bash
# 1) 首次：把 API Key 写入本地文件 data/api_key.txt（已被 gitignore），留空则用 TTSHOP_API_KEY 环境变量

# 2) 注册 Windows 任务计划程序（管理员 PowerShell）：默认每天 08:00
.\scripts\install_task.ps1 -Time "08:00"

# 3) 手动试跑一次
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push_report.ps1
```

执行日志写入 `logs/push_report_yyyyMMdd.log`；`reports/` 下的看板与 Top 商品 CSV 会随每日提交推送到仓库。
### 真实数据快照（2026-08-13）
已用 EchoTik 免费额度完成一次真实采集并提交看板：

- 关键词 `yoga mat`（30 条）+ 类目 `Yoga & Pilates`(603084)（20 条），去重后 **48 个真实商品**入库；
- 带货达人 **20 位**、飙升关键词 **15 个**（EchoTik 实时接口）；
- 线上看板即该快照：[在线看板](https://tianjiehu2-beep.github.io/tiktok-shop-analytics/index.html)；
- 选品分析报告（运营交付样例）：[中文版](reports/TikTokShop选品分析报告_yoga_mat.docx) · [English](reports/TikTokShop_US_Product_Selection_Analysis_yoga_mat.docx)；
- 说明：直播带货榜无公开直播场次 API，由 `python main.py live` 基于库内商品生成**模拟数据**（看板面板已标注）；短视频带货榜按 EchoTik 真实带货视频数 / 带货达人数排序。

## Playwright 真实采集 TikTok Shop
```bash
pip install playwright
playwright install chromium
python main.py scrape --keyword "yoga mat" --limit 100
python main.py run --source scraper --keyword "yoga mat"
```
注意：
- TikTok 反爬强、页面结构常变，`ttshop/scraper/tiktok_shop.py` 中的
  搜索 URL 与字段名可能需要按当前页面调整。
- 建议使用目标区域代理、控制频率、低峰运行。
- 仅采集公开数据，遵守平台规则；生产环境优先对接官方 TikTok Shop Partner API
  或第三方数据服务（`--source api`）。

## 毛利测算模型（可在 config.py 调整）
预估毛利 = 售价 − 采购成本(25%) − 平台佣金(8%) − 物流($6) − 广告(15%) − 其他($1)

## 运行测试
```bash
python -m unittest discover -s tests -v
```
