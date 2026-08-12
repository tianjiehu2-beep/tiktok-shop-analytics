"""Playwright 采集器数据源：真实抓取 TikTok Shop 搜索页。"""

from __future__ import annotations

from .base import DataSource, SourceResult


class ScraperSource(DataSource):
    """封装 ttshop.scraper.tiktok_shop.TikTokShopScraper。"""

    def __init__(self, region: str = "US", headless: bool = True, slow_mo_ms: int = 800,
                 max_products_per_run: int = 100, proxy: str | None = None):
        self.region = region
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.max_products_per_run = max_products_per_run
        self.proxy = proxy

    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        if not keyword:
            raise ValueError('scraper 数据源需要 --keyword（例如 "yoga mat"）')
        from ..scraper.tiktok_shop import TikTokShopScraper  # 延迟导入，demo 模式无需 playwright

        scraper = TikTokShopScraper(
            region=self.region,
            headless=self.headless,
            slow_mo_ms=self.slow_mo_ms,
            max_products_per_run=limit or self.max_products_per_run,
            proxy=self.proxy,
        )
        return SourceResult(products=scraper.scrape_search(keyword, limit=limit))
