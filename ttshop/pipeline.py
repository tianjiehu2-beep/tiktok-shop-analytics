"""核心数据管道：采集/生成 → 分析 → 报告。

供 CLI（main.py）与调度器（ttshop/scheduler.py）复用，保证各入口行为一致。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .analysis.scoring import run_analysis
from .config import Settings
from .db import Database
from .report.html_report import build_report
from .sources import get_source

logger = logging.getLogger("ttshop.pipeline")


def run_pipeline(db: Database, settings: Settings, demo: bool = False,
                 source: str | None = None, keyword: str | None = None,
                 product_count: int = 200, limit: int | None = None,
                 seed: int | None = None, proxy: str | None = None,
                 category: str | None = None,
                 api_provider: str | None = None, api_base: str | None = None,
                 api_key: str | None = None) -> Path:
    """执行一次完整数据管道，返回 HTML 报告路径。

    :param demo: True 时强制使用 demo 数据源（等价 source='demo'）
    :param source: 数据源名称 demo/scraper/api；默认有 keyword 时用 scraper，否则 demo
    """
    if demo:
        source = "demo"
    source = source or ("scraper" if keyword else "demo")

    data_source = get_source(
        source, settings,
        seed=seed, product_count=product_count, proxy=proxy, category=category,
        api_provider=api_provider, api_base=api_base, api_key=api_key,
    )
    result = data_source.fetch(keyword=keyword, limit=limit, category=category)

    written = db.upsert_products(result.products)
    for product_id, price, sold, ts in result.history:
        db.add_history(product_id, price, sold, ts)
    logger.info("数据源 %s 获取商品 %d 条，写入 %d 条", source, len(result.products), written)

    analyzed = run_analysis(db, settings)
    report_path = Path(settings.report_dir) / "tiktok_shop_report.html"
    build_report(db, settings, report_path)
    logger.info("分析完成 %d 条，报告已生成: %s", analyzed, report_path)
    return report_path
