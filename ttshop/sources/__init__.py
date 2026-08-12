"""可插拔数据源：demo（模拟）/ scraper（Playwright 采集）/ api（第三方数据 API）。

新增数据源：实现 ttshop.sources.base.DataSource 并在 get_source 中注册即可。
"""

from __future__ import annotations

from ..config import Settings
from .api import PROVIDERS, ProviderConfig, ApiSource
from .base import DataSource, SourceResult
from .demo import DemoSource
from .scraper import ScraperSource

__all__ = [
    "DataSource", "SourceResult", "DemoSource", "ScraperSource", "ApiSource",
    "ProviderConfig", "PROVIDERS", "get_source",
]


def get_source(name: str | None, settings: Settings | None = None, **kwargs) -> DataSource:
    """按名称创建数据源实例。name: demo / scraper / api。"""
    settings = settings or Settings()
    name = (name or "demo").lower()
    if name == "demo":
        return DemoSource(
            seed=kwargs.pop("seed", None),
            product_count=kwargs.pop("product_count", 200),
        )
    if name == "scraper":
        return ScraperSource(
            region=kwargs.pop("region", settings.region),
            headless=kwargs.pop("headless", settings.headless),
            slow_mo_ms=kwargs.pop("slow_mo_ms", settings.slow_mo_ms),
            max_products_per_run=kwargs.pop("max_products_per_run", settings.max_products_per_run),
            proxy=kwargs.pop("proxy", settings.proxy or None),
        )
    if name == "api":
        return ApiSource(
            settings=settings,
            provider=kwargs.pop("api_provider", None),
            api_base=kwargs.pop("api_base", None),
            api_key=kwargs.pop("api_key", None),
            region=kwargs.pop("region", settings.region),
            timeout=kwargs.pop("api_timeout", None),
        )
    raise ValueError(f"未知数据源: {name!r}，可选 demo / scraper / api")
