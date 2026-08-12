"""模拟数据源：离线演示完整数据管道，无需联网与浏览器。"""

from __future__ import annotations

from datetime import datetime

from ..demo_data import generate_history, generate_products
from .base import DataSource, SourceResult


def demo_seed() -> int:
    """demo 默认种子：按日期变化，模拟每天新增一批商品（增量采集效果）。"""
    return int(datetime.now().strftime("%Y%m%d"))


class DemoSource(DataSource):
    """生成本地模拟的美区 TikTok Shop 商品数据。"""

    def __init__(self, seed: int | None = None, product_count: int = 200):
        self.seed = seed
        self.product_count = product_count

    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        seed = self.seed if self.seed is not None else demo_seed()
        products = generate_products(count=self.product_count, category=category, seed=seed)
        return SourceResult(products=products, history=generate_history(products))
