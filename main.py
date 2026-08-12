"""TikTok Shop 爆品监测与选品分析系统 —— 命令行入口。

用法示例：
  python main.py run --demo                                  # 一键：模拟数据 -> 分析 -> 出报告
  python main.py run --source demo                           # 等价于 --demo
  python main.py run --source scraper --keyword "yoga mat"   # Playwright 真实采集（需安装 playwright）
  python main.py run --source api --keyword "yoga mat" --api-key xxx   # 第三方数据 API
  python main.py seed --products 300            # 生成模拟商品数据（可重复执行，模拟增量采集）
  python main.py scrape --keyword "yoga mat"    # 真实采集（需安装 playwright）
  python main.py analyze                        # 重新计算选品评分
  python main.py report                         # 生成 HTML 看板
  python main.py schedule --once --demo         # 定时调度：立即执行一次（测试）
  python main.py schedule --time 08:30 --demo   # 每天 08:30 自动执行（前台进程）
  python main.py stats                          # 查看数据量
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ttshop.analysis.scoring import run_analysis
from ttshop.config import Settings
from ttshop.db import Database
from ttshop.demo_data import generate_history, generate_products
from ttshop.pipeline import run_pipeline
from ttshop.report.html_report import build_report

logger = logging.getLogger("ttshop")


def _get_db(args, settings: Settings) -> Database:
    db = Database(args.db or settings.db_path)
    db.init_schema()
    return db


def cmd_init(args, settings: Settings) -> int:
    db = _get_db(args, settings)
    print(f"数据库初始化完成: {db.db_path}")
    return 0


def cmd_seed(args, settings: Settings) -> int:
    db = _get_db(args, settings)
    products = generate_products(count=args.products, category=args.category, seed=args.seed)
    written = db.upsert_products(products)
    if args.history:
        for product_id, price, sold, ts in generate_history(products):
            db.add_history(product_id, price, sold, ts)
    print(f"写入商品 {written} 条（含历史快照）。数据规模: {db.stats()}")
    return 0


def cmd_scrape(args, settings: Settings) -> int:
    try:
        from ttshop.scraper.tiktok_shop import TikTokShopScraper
    except ImportError:
        print("未安装 playwright，无法真实采集。")
        print("请先执行: pip install playwright && playwright install chromium")
        print("或者用模拟数据演示: python main.py run --demo")
        return 1
    db = _get_db(args, settings)
    scraper = TikTokShopScraper(
        region=settings.region,
        headless=not args.headful,
        slow_mo_ms=settings.slow_mo_ms,
        max_products_per_run=args.limit or settings.max_products_per_run,
        proxy=args.proxy or settings.proxy or os.environ.get("TTSHOP_PROXY") or None,
    )
    products = scraper.scrape_search(args.keyword, limit=args.limit)
    written = db.upsert_products(products)
    print(f"采集并写入 {written} 条商品。数据规模: {db.stats()}")
    return 0


def cmd_analyze(args, settings: Settings) -> int:
    db = _get_db(args, settings)
    count = run_analysis(db, settings)
    print(f"分析完成: {count} 条商品完成选品评分与毛利测算。")
    return 0


def cmd_report(args, settings: Settings) -> int:
    db = _get_db(args, settings)
    output = Path(args.output) if args.output else Path(settings.report_dir) / "tiktok_shop_report.html"
    report_path = build_report(db, settings, output)
    print(f"报告已生成: {report_path}")
    print(f"Top 商品 CSV: {report_path.parent / 'top_products.csv'}")
    return 0


def cmd_stats(args, settings: Settings) -> int:
    db = _get_db(args, settings)
    print(f"数据库: {db.db_path}")
    for k, v in db.stats().items():
        print(f"  {k}: {v:,}")
    return 0


def cmd_run(args, settings: Settings) -> int:
    source = "demo" if args.demo else args.source
    if source is None:
        source = "scraper" if args.keyword else "demo"
    if source in ("scraper", "api") and not args.keyword:
        print(f"{source} 数据源需要 --keyword（例如: python main.py run --source {source} --keyword \"yoga mat\"）")
        return 1
    db = _get_db(args, settings)
    try:
        report_path = run_pipeline(
            db, settings, demo=False, source=source, keyword=args.keyword,
            product_count=args.products, limit=args.limit, seed=getattr(args, "seed", None),
            proxy=args.proxy, category=args.category,
            api_provider=args.provider, api_base=args.api_base, api_key=args.api_key,
        )
    except ImportError:
        print("未安装 playwright，无法使用 scraper 数据源。")
        print("请先执行: pip install playwright && playwright install chromium")
        return 1
    except RuntimeError as exc:
        print(f"数据源执行失败: {exc}")
        return 1
    print()
    print("=" * 60)
    print("全流程完成 [OK]")
    print(f"  数据源: {source}")
    print(f"  HTML 看板: {report_path}")
    print(f"  Top 商品 CSV: {report_path.parent / 'top_products.csv'}")
    print(f"  数据规模: {db.stats()}")
    print("=" * 60)
    return 0


def cmd_schedule(args, settings: Settings) -> int:
    from ttshop.scheduler import run_loop

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    handler = logging.FileHandler(log_dir / "scheduler.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logging.getLogger("ttshop").addHandler(handler)

    db = _get_db(args, settings)

    def job() -> None:
        run_pipeline(
            db, settings, demo=args.demo, source=args.source, keyword=args.keyword,
            product_count=args.products, limit=args.limit,
            proxy=args.proxy, category=args.category,
            api_provider=args.provider, api_base=args.api_base, api_key=args.api_key,
        )

    run_loop(job, run_at=args.time, interval_minutes=args.interval_minutes, once=args.once)
    return 0


def _add_source_args(parser) -> None:
    parser.add_argument("--source", choices=["demo", "scraper", "api"], default=None,
                        help="数据源：demo / scraper / api（默认：有 --keyword 用 scraper，否则 demo）")
    parser.add_argument("--proxy", default=None, help="代理地址（scraper 源），如 socks5://127.0.0.1:40000")
    parser.add_argument("--provider", default=None, help="第三方数据平台（api 源），如 kalodata/echotik/fastmoss")
    parser.add_argument("--api-base", default=None, help="第三方 API 地址（api 源），也可用环境变量 TTSHOP_API_BASE")
    parser.add_argument("--api-key", default=None, help="第三方 API Key（api 源），也可用环境变量 TTSHOP_API_KEY")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="SQLite 数据库路径（默认 data/tiktok_shop.db）")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化数据库表结构")
    p_init.set_defaults(func=cmd_init)

    p_seed = sub.add_parser("seed", help="生成模拟商品数据（演示用，可重复执行模拟增量采集）")
    p_seed.add_argument("--products", type=int, default=200)
    p_seed.add_argument("--category", default=None)
    p_seed.add_argument("--seed", type=int, default=42)
    p_seed.add_argument("--no-history", dest="history", action="store_false", default=True, help="不生成历史销量快照")
    p_seed.set_defaults(func=cmd_seed)

    p_scrape = sub.add_parser("scrape", help="真实采集 TikTok Shop（需 playwright）")
    p_scrape.add_argument("--keyword", required=True)
    p_scrape.add_argument("--limit", type=int, default=None)
    p_scrape.add_argument("--category", default=None, help="写入数据库时标记的类目")
    p_scrape.add_argument("--headful", action="store_true", help="显示浏览器窗口（便于调试反爬）")
    p_scrape.add_argument("--proxy", default=None, help="代理地址，如 socks5://127.0.0.1:40000")
    p_scrape.set_defaults(func=cmd_scrape)

    p_analyze = sub.add_parser("analyze", help="运行选品评分与毛利分析")
    p_analyze.set_defaults(func=cmd_analyze)

    p_report = sub.add_parser("report", help="生成 HTML 分析看板")
    p_report.add_argument("--output", default=None)
    p_report.set_defaults(func=cmd_report)

    p_stats = sub.add_parser("stats", help="查看数据库统计")
    p_stats.set_defaults(func=cmd_stats)

    p_run = sub.add_parser("run", help="一键全流程：采集 -> 分析 -> 报告")
    p_run.add_argument("--demo", action="store_true", help="使用模拟数据（等价于 --source demo）")
    p_run.add_argument("--keyword", default=None)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--category", default=None)
    p_run.add_argument("--products", type=int, default=200)
    p_run.add_argument("--seed", type=int, default=None)
    _add_source_args(p_run)
    p_run.set_defaults(func=cmd_run)

    p_schedule = sub.add_parser("schedule", help="定时调度：每日/按间隔自动执行数据管道")
    p_schedule.add_argument("--time", default="08:30", help="每日执行时刻 HH:MM（本地时区，默认 08:30）")
    p_schedule.add_argument("--interval-minutes", type=int, default=None, help="按固定间隔执行（分钟），用于测试")
    p_schedule.add_argument("--once", action="store_true", help="立即执行一次后退出（配合 Windows 任务计划程序）")
    p_schedule.add_argument("--demo", action="store_true", help="使用模拟数据（等价于 --source demo）")
    p_schedule.add_argument("--keyword", default=None, help="scraper/api 数据源搜索关键词")
    p_schedule.add_argument("--products", type=int, default=200)
    p_schedule.add_argument("--limit", type=int, default=None)
    _add_source_args(p_schedule)
    p_schedule.set_defaults(func=cmd_schedule)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings()
    return args.func(args, settings)


if __name__ == "__main__":
    sys.exit(main())
