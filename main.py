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
        source = "api" if (args.keyword or args.category_id) else "demo"
    if source in ("scraper", "api") and not args.keyword and not (source == "api" and args.category_id):
        print(f"{source} 数据源需要 --keyword 或 --category-id（例如: python main.py run --source {source} --keyword \"yoga mat\"）")
        return 1
    db = _get_db(args, settings)
    try:
        report_path = run_pipeline(
            db, settings, demo=False, source=source, keyword=args.keyword,
            product_count=args.products, limit=args.limit, seed=getattr(args, "seed", None),
            proxy=args.proxy, category=args.category,
            api_provider=args.provider, api_base=args.api_base, api_key=args.api_key,
            category_id=getattr(args, "category_id", None), pages=getattr(args, "pages", 1),
            sort_field=getattr(args, "sort", None),
            min_sales=getattr(args, "min_sales", None), max_price=getattr(args, "max_price", None),
            min_commission=getattr(args, "min_commission", None), enrich=getattr(args, "enrich", False),
            language=getattr(args, "language", "en-US"),
        )
        if source == "api" and getattr(args, "with_influencers", False):
            _attach_product_influencers(db, settings, args)
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


def _api_source(args, settings: Settings):
    from ttshop.sources import ApiSource

    return ApiSource(settings=settings, provider=args.provider,
                     api_base=args.api_base, api_key=args.api_key,
                     language=getattr(args, "language", "en-US"))


def _attach_product_influencers(db, settings, args) -> None:
    """对本次采集的 Top 商品拉取带货达人，写入 product_influencers 表。"""
    try:
        source = _api_source(args, settings)
        top = db.products(limit=getattr(args, "influencer_products", 5) or 5)
        saved = 0
        for product in top:
            rows = source.fetch_product_influencers(product["product_id"],
                                                    limit=getattr(args, "influencer_limit", 3) or 3)
            if rows:
                saved += db.save_product_influencers(rows)
        print(f"商品-达人关联写入 {saved} 条")
    except RuntimeError as exc:
        print(f"商品关联达人采集失败: {exc}")


def cmd_influencers(args, settings: Settings) -> int:
    source = _api_source(args, settings)
    db = _get_db(args, settings)
    if args.rank:
        influencers = source.fetch_influencer_ranklist(
            category_id=args.category_id, date=args.date, period=args.period,
            rank_field=args.rank_field, limit=args.limit)
        written = db.upsert_influencers(influencers)
        print(f"达人榜采集完成: {len(influencers)} 条（{args.period}榜, {args.rank_field}），写入 {written} 条")
    else:
        influencers = source.fetch_influencers(
            category_id=args.category_id, sort=args.sort, pages=args.pages,
            min_followers=args.min_followers, min_gmv=args.min_gmv,
            limit=args.limit)
        written = db.upsert_influencers(influencers)
        print(f"达人列表采集完成: {len(influencers)} 条（按{args.sort}排序, 仅带货达人），写入 {written} 条")
    print()
    print("Top 带货达人（按带货GMV）:")
    for i, inf in enumerate(db.top_influencers(limit=10), 1):
        print(f"{i:>2}. {inf['nick_name'][:28]:<30} 粉丝 {inf['followers_cnt']:>10,}  "
              f"带货 {inf['sale_cnt']:>8,}  GMV ${inf['sale_gmv_amt']:>12,.0f}  "
              f"EC分 {inf['ec_score']}")
    print()
    print("看商品由谁在带: python main.py run --source api --keyword \"<词>\" --with-influencers")
    return 0


def cmd_keywords(args, settings: Settings) -> int:
    source = _api_source(args, settings)
    db = _get_db(args, settings)
    if args.keyword:
        trends = source.fetch_keyword_inspiration(args.keyword, count=args.limit)
        written = db.upsert_keyword_trends(trends)
        print(f"关键词灵感采集完成: {len(trends)} 条（围绕 {args.keyword!r}），写入 {written} 条")
    else:
        trends = source.fetch_keyword_trends(tab=args.tab, count=args.limit)
        written = db.upsert_keyword_trends(trends)
        print(f"趋势搜索词榜采集完成: {len(trends)} 条（tab={args.tab}），写入 {written} 条")
    print()
    print("飙升关键词（按热度）:")
    for i, t in enumerate(db.latest_keyword_trends(source="inspiration" if args.keyword else "ranking", limit=15), 1):
        trend = (t.get("trend_json") or "[]")
        print(f"{i:>2}. {t['keyword'][:34]:<36} 视频数 {t['video_num']:>10,}  热度 {t['popularity']:>12,}")
    return 0


def cmd_trend(args, settings: Settings) -> int:
    from ttshop.analysis.trend import compute_trends

    db = _get_db(args, settings)
    count = compute_trends(db, settings)
    rows = db.latest_trends(limit=args.limit)
    hot = db.latest_trends(limit=999, only_hot=True)
    print(f"趋势/爆品指数计算完成: {count} 条，爆品（指数≥60）{len(hot)} 个")
    print()
    print("爆品预测 Top:")
    for i, t in enumerate(rows, 1):
        tag = "NEW " if t["is_new"] else "HOT " if t["is_hot"] else "    "
        print(f"{i:>2}. {tag}{t['title'][:38]:<40} "
              f"7天 {t['sold_7d']:>6,}  增速 {t['growth_7d']:>5.1f}x  "
              f"新品分 {t['novelty_score']:>3.0f}  爆品指数 {t['hot_score']:>5.1f}")
    return 0


def cmd_categories(args, settings: Settings) -> int:
    from ttshop.sources import ApiSource

    source = ApiSource(settings=settings, provider=args.provider,
                       api_base=args.api_base, api_key=args.api_key,
                       language=args.language)
    if args.refresh:
        tree = source.fetch_categories(refresh=True)
        print(f"类目树已刷新: {source.categories_cache}（l1={len(tree['l1'])} l2={len(tree['l2'])} l3={len(tree['l3'])}）")
        return 0
    if args.search:
        matches = source.search_categories(args.search, limit=args.limit)
        if not matches:
            print(f"未找到包含 {args.search!r} 的类目，可先执行: python main.py categories --refresh")
            return 0
        for m in matches:
            print(f"{m['category_id']}  [L{m['level']}]  {m['path']}")
        print(f"共 {len(matches)} 条匹配")
        return 0
    tree = source.fetch_categories()
    print(f"EchoTik 一级类目（{len(tree['l1'])} 个，缓存: {source.categories_cache}）")
    print("用法示例:")
    print("  python main.py categories --search 瑜伽     # 按名称搜类目（可搜三级）")
    print("  python main.py run --source api --category-id <类目ID> --pages 3")
    print()
    for c in tree["l1"]:
        print(f"{c['category_id']}  {c['category_name']}")
    return 0


def cmd_ranklist(args, settings: Settings) -> int:
    from ttshop.sources import ApiSource

    source = ApiSource(settings=settings, provider=args.provider,
                       api_base=args.api_base, api_key=args.api_key,
                       language=args.language)
    result = source.fetch_ranklist(category_id=args.category_id, date=args.date,
                                   period=args.period, rank_field=args.rank_field,
                                   limit=args.limit)
    db = _get_db(args, settings)
    written = db.upsert_products(result.products)
    print(f"榜单采集完成: {len(result.products)} 条（{args.period}榜, {args.rank_field}），写入 {written} 条")
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
            proxy=args.proxy, category=getattr(args, "category", None),
            api_provider=args.provider, api_base=args.api_base, api_key=args.api_key,
            category_id=getattr(args, "category_id", None), pages=getattr(args, "pages", 1),
            sort_field=getattr(args, "sort", None),
            min_sales=getattr(args, "min_sales", None), max_price=getattr(args, "max_price", None),
            min_commission=getattr(args, "min_commission", None), enrich=getattr(args, "enrich", False),
            language=getattr(args, "language", "en-US"),
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

    p_trend = sub.add_parser("trend", help="计算趋势与爆品预测（7天/30天增速、新品检测、爆品指数）")
    p_trend.add_argument("--limit", type=int, default=15)
    p_trend.set_defaults(func=cmd_trend)

    p_categories = sub.add_parser("categories", help="浏览/搜索 TikTok Shop 类目树（EchoTik）并获取类目ID")
    p_categories.add_argument("--search", default=None, help="按名称搜索类目，例如 瑜伽 / home / kitchen")
    p_categories.add_argument("--refresh", action="store_true", help="强制重新拉取类目树并刷新缓存")
    p_categories.add_argument("--limit", type=int, default=50, help="搜索结果条数上限")
    p_categories.add_argument("--language", default="en-US", help="类目语言，默认 en-US")
    _add_source_args(p_categories)
    p_categories.set_defaults(func=cmd_categories)

    p_influencers = sub.add_parser("influencers", help="采集带货达人（列表/榜单，EchoTik）")
    p_influencers.add_argument("--rank", action="store_true", help="达人榜单模式（默认列表模式）")
    p_influencers.add_argument("--period", choices=["day", "week", "month"], default="day", help="榜单周期（榜单模式）")
    p_influencers.add_argument("--rank-field", choices=["sales", "followers"], default="sales", help="榜单排序字段")
    p_influencers.add_argument("--date", default=None, help="榜单日期 yyyy-MM-dd（默认今天）")
    p_influencers.add_argument("--sort", default="followers",
                               choices=["followers", "followers30d", "posts", "views", "interaction"],
                               help="列表排序（列表模式）")
    p_influencers.add_argument("--pages", type=int, default=1, help="列表翻页数（每页10条）")
    p_influencers.add_argument("--min-followers", type=int, default=None, help="最低粉丝数")
    p_influencers.add_argument("--min-gmv", type=float, default=None, help="最低带货GMV")
    p_influencers.add_argument("--category-id", default=None, help="按类目过滤（用 categories 命令查）")
    p_influencers.add_argument("--limit", type=int, default=None)
    p_influencers.add_argument("--language", default="en-US")
    _add_source_args(p_influencers)
    p_influencers.set_defaults(func=cmd_influencers)

    p_keywords = sub.add_parser("keywords", help="采集飙升关键词/关键词灵感（EchoTik）")
    p_keywords.add_argument("--tab", choices=["all", "Fashion", "Food", "Sports", "Tourism", "Gaming", "Science"],
                            default="all", help="趋势榜分类")
    p_keywords.add_argument("--keyword", default=None, help="关键词灵感：围绕该词找相关热词")
    p_keywords.add_argument("--limit", type=int, default=20)
    p_keywords.add_argument("--language", default="en-US")
    _add_source_args(p_keywords)
    p_keywords.set_defaults(func=cmd_keywords)

    p_ranklist = sub.add_parser("ranklist", help="采集商品榜单（EchoTik，日/周/月榜）")
    p_ranklist.add_argument("--category-id", default=None, help="类目ID（用 categories 命令查）")
    p_ranklist.add_argument("--date", default=None, help="榜单日期 yyyy-MM-dd，默认今天")
    p_ranklist.add_argument("--period", choices=["day", "week", "month"], default="day", help="榜单周期")
    p_ranklist.add_argument("--rank-field", choices=["sales", "influencer"], default="sales", help="榜单排序字段")
    p_ranklist.add_argument("--limit", type=int, default=10)
    p_ranklist.add_argument("--language", default="en-US")
    _add_source_args(p_ranklist)
    p_ranklist.set_defaults(func=cmd_ranklist)

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
    p_run.add_argument("--category", default=None, help="类目标签（写库时标记用）")
    p_run.add_argument("--category-id", default=None, help="类目ID按类目采集（api源，用 categories 命令查询）")
    p_run.add_argument("--pages", type=int, default=1, help="按类目采集翻页数（每页最多10条）")
    p_run.add_argument("--sort", default=None, choices=["sales", "gmv", "price", "sales7d", "sales30d", "gmv7d", "gmv30d"],
                       help="按类目采集排序字段（默认销量降序）")
    p_run.add_argument("--min-sales", type=int, default=None, help="筛选最低总销量")
    p_run.add_argument("--max-price", type=float, default=None, help="筛选最高均价（美元）")
    p_run.add_argument("--min-commission", type=float, default=None, help="筛选最低佣金率")
    p_run.add_argument("--enrich", action="store_true", help="采集后调用商品详情接口补全评分/评论/GMV")
    p_run.add_argument("--with-influencers", action="store_true", help="采集后拉取Top商品的带货达人（人-货关联）")
    p_run.add_argument("--influencer-products", type=int, default=5, help="拉取关联达人的Top商品数")
    p_run.add_argument("--influencer-limit", type=int, default=3, help="每个商品的关联达人条数")
    p_run.add_argument("--language", default="en-US", help="类目语言（默认 en-US）")
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
    p_schedule.add_argument("--category", default=None, help="类目标签")
    p_schedule.add_argument("--category-id", default=None, help="类目ID按类目采集（api源）")
    p_schedule.add_argument("--pages", type=int, default=1)
    p_schedule.add_argument("--sort", default=None, choices=["sales", "gmv", "price", "sales7d", "sales30d", "gmv7d", "gmv30d"])
    p_schedule.add_argument("--min-sales", type=int, default=None)
    p_schedule.add_argument("--max-price", type=float, default=None)
    p_schedule.add_argument("--min-commission", type=float, default=None)
    p_schedule.add_argument("--enrich", action="store_true")
    p_schedule.add_argument("--with-influencers", action="store_true")
    p_schedule.add_argument("--influencer-products", type=int, default=5)
    p_schedule.add_argument("--influencer-limit", type=int, default=3)
    p_schedule.add_argument("--language", default="en-US")
    p_schedule.add_argument("--products", type=int, default=200)
    p_schedule.add_argument("--limit", type=int, default=None)
    _add_source_args(p_schedule)
    p_schedule.set_defaults(func=cmd_schedule)

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings()
    return args.func(args, settings)


if __name__ == "__main__":
    sys.exit(main())
