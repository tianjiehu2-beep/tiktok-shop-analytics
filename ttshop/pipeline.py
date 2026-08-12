"""核心数据管道：采集/生成 → 分析 → 报告。

供 CLI（main.py）与调度器（ttshop/scheduler.py）复用，保证各入口行为一致。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .analysis.scoring import run_analysis
from .config import Settings
from .db import Database
from .demo_data import generate_history, generate_products
from .report.html_report import build_report

logger = logging.getLogger("ttshop.pipeline")


def demo_seed() -> int:
    """demo 模式默认种子：按日期变化，模拟每天新增一批商品（增量采集效果）。"""
    return int(datetime.now().strftime("%Y%m%d"))


def run_pipeline(db: Database, settings: Settings, demo: bool = False,
                 keyword: str | None = None, product_count: int = 200,
                 limit: int | None = None, seed: int | None = None) -> Path:
    """执行一次完整数据管道，返回 HTML 报告路径。"""
    if demo:
        products = generate_products(count=product_count, seed=seed if seed is not None else demo_seed())
        db.upsert_products(products)
        for product_id, price, sold, ts in generate_history(products):
            db.add_history(product_id, price, sold, ts)
        logger.info("生成模拟商品 %d 条", len(products))
    else:
        if not keyword:
            raise ValueError("真实采集模式必须提供 --keyword")
        from .scraper.tiktok_shop import TikTokShopScraper  # 延迟导入，demo 无需 playwright

        scraper = TikTokShopScraper(
            region=settings.region,
            headless=settings.headless,
            slow_mo_ms=settings.slow_mo_ms,
            max_products_per_run=limit or settings.max_products_per_run,
        )
        products = scraper.scrape_search(keyword, limit=limit)
        db.upsert_products(products)
        logger.info("采集商品 %d 条", len(products))

    analyzed = run_analysis(db, settings)
    report_path = Path(settings.report_dir) / "tiktok_shop_report.html"
    build_report(db, settings, report_path)
    logger.info("分析完成 %d 条，报告已生成: %s", analyzed, report_path)
    return report_path
