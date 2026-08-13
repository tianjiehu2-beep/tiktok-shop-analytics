"""核心数据管道：采集/生成 → 分析 → 报告。

供 CLI（main.py）与调度器（ttshop/scheduler.py）复用，保证各入口行为一致。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .analysis.alerts import compute_alerts
from .analysis.competitor import compute_competitors
from .analysis.forecast import compute_forecasts
from .analysis.scoring import run_analysis
from .analysis.shop import compute_shop_alerts
from .analysis.trend import compute_trends
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
                 api_key: str | None = None, category_id: str | None = None,
                 pages: int = 1, sort_field: str | None = None,
                 min_sales: int | None = None, max_price: float | None = None,
                 min_commission: float | None = None, enrich: bool = False,
                 language: str = "en-US") -> Path:
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
        category_id=category_id, pages=pages, sort_field=sort_field,
        min_sales=min_sales, max_price=max_price, min_commission=min_commission,
        enrich=enrich, language=language,
    )
    result = data_source.fetch(keyword=keyword, limit=limit, category=category)

    written = db.upsert_products(result.products)
    for product_id, price, sold, ts in result.history:
        db.add_history(product_id, price, sold, ts)
    logger.info("数据源 %s 获取商品 %d 条，写入 %d 条", source, len(result.products), written)

    analyzed = run_analysis(db, settings)
    trended = compute_trends(db, settings)
    forecasted = compute_forecasts(db, settings)
    competitors, comp_alerts = compute_competitors(db)
    sellers, shop_alerts = compute_shop_alerts(db)
    alerted = compute_alerts(db)
    report_path = Path(settings.report_dir) / "tiktok_shop_report.html"
    used_source = getattr(data_source, "used_source", None) or source
    build_report(db, settings, report_path, source=used_source)
    logger.info("分析 %d 条，趋势 %d 条，预测 %d 条，竞品 %d 条，店铺 %d 家，今日异动 %d 条，实际数据源 %s，报告已生成: %s",
                analyzed, trended, forecasted, competitors, sellers, alerted, used_source, report_path)
    return report_path, used_source
