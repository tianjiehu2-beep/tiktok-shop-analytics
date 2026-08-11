# TikTok Shop 爆品监测与选品分析系统

面向跨境电商（TikTok Shop 美区）的商品数据采集与选品决策项目。
自动完成「采集 → 存储 → 毛利测算 → 选品评分 → 可视化看板」全流程，可作为简历项目或日常选品工具。

## 特性
- 数据管道：商品库 + 价格/销量历史快照 + 分析快照，分层存储（SQLite）。
- 选品评分：需求度（销量+视频播放）× 竞争度（评论稀疏度）× 利润（跨境小包直发毛利模型）。
- 可视化：自包含 HTML 看板（离线可打开），含类目销量、选品评分、销量趋势图与爆品榜。
- 双模式：demo 模式零依赖跑通全流程；真实模式基于 Playwright 采集 TikTok Shop。

## 目录结构
```
ttshop/
  config.py          # 配置（毛利参数、评分权重、区域）
  db.py              # SQLite 数据层
  models.py          # 数据模型
  demo_data.py       # 模拟美区商品数据生成器（离线演示）
  scraper/           # 真实采集器（Playwright）
  analysis/          # 毛利测算 + 选品评分
  report/            # HTML 看板 + CSV 导出
main.py              # CLI 入口
tests/               # 端到端冒烟测试
resume.md            # 简历项目描述
```

## 快速开始（demo 模式，无需安装任何依赖）
```bash
# Windows（若 python 不在 PATH，用完整路径，如 D:\Python\python.exe）
python main.py run --demo
python main.py report
```
打开 `reports/tiktok_shop_report.html` 查看看板。

## 常用命令
```bash
python main.py init                          # 初始化数据库
python main.py seed --products 300           # 生成模拟商品（可重复执行，模拟增量采集）
python main.py analyze                       # 运行选品评分与毛利分析
python main.py report                        # 生成 HTML 看板 + top_products.csv
python main.py stats                         # 查看数据规模
python main.py run --demo                    # 一键全流程
```

## 真实采集 TikTok Shop
```bash
pip install playwright
playwright install chromium
python main.py scrape --keyword "yoga mat" --limit 100
python main.py run --keyword "yoga mat"
```
注意：
- TikTok 反爬强、页面结构常变，`ttshop/scraper/tiktok_shop.py` 中的
  搜索 URL 与字段名可能需要按当前页面调整。
- 建议使用目标区域代理、控制频率、低峰运行。
- 仅采集公开数据，遵守平台规则；生产环境优先对接官方 TikTok Shop Partner API。

## 毛利测算模型（可在 config.py 调整）
预估毛利 = 售价 − 采购成本(25%) − 平台佣金(8%) − 物流($6) − 广告(15%) − 其他($1)

## 运行测试
```bash
python -m unittest discover -s tests -v
```
