# TikTok Shop 爆品监测与选品分析系统

面向跨境电商（TikTok Shop 美区）的商品数据采集与选品决策项目。
自动完成「采集 → 存储 → 毛利测算 → 选品评分 → 可视化看板」全流程，可作为简历项目或日常选品工具。

## 特性
- 数据管道：商品库 + 价格/销量历史快照 + 分析快照，分层存储（SQLite）。
- 可插拔数据源：demo（模拟）/ scraper（Playwright 采集）/ api（第三方数据 API），管道层与数据来源解耦。
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

```bash
python main.py run --source demo --products 200              # 模拟数据
python main.py run --source scraper --keyword "yoga mat"     # Playwright 采集
python main.py run --source api --keyword "yoga mat" --api-key xxx
```

第三方 API 平台（Kalodata / EchoTik / FastMoss 等）注册账号后一般有免费额度：
```bash
set TTSHOP_API_KEY=xxx          # Windows 环境变量（推荐，避免 key 进命令行历史）
python main.py run --source api --keyword "yoga mat" --provider kalodata
```
各平台响应字段不同，在 `ttshop/sources/api.py` 的 `PROVIDERS` 中按官方文档调整
base_url / search_path / 鉴权方式 / items_path，字段归一化在 `normalize_item` 中完成。
新增数据源：实现 `ttshop/sources/base.py` 的 `DataSource` 并在 `get_source` 注册即可。

## 常用命令
```bash
python main.py init                          # 初始化数据库
python main.py seed --products 300           # 生成模拟商品（可重复执行，模拟增量采集）
python main.py analyze                       # 运行选品评分与毛利分析
python main.py report                        # 生成 HTML 看板 + top_products.csv
python main.py stats                         # 查看数据规模
python main.py run --demo                    # 一键全流程（模拟数据）
python main.py schedule --once --demo        # 立即执行一次（测试）
python main.py schedule --time 08:30 --demo  # 每天 08:30 自动执行
```

## 定时调度（每日自动跑数据管道）
```bash
# 前台方式（保持窗口开着）
python main.py schedule --time 08:30 --demo

# 推荐生产方式：注册 Windows 任务计划程序（管理员 PowerShell）
.\scripts\install_task.ps1
.\scripts\install_task.ps1 -Keyword "yoga mat" -Time "07:00"
```
任务执行日志写入 `logs/scheduler.log`；单次任务失败不会中断后续调度。

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
