"""数据源抽象：所有数据来源统一返回 SourceResult，管道层无需关心来源。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import Product


@dataclass
class SourceResult:
    """一次抓取/生成的产物：商品列表 + 可选历史快照。"""
    products: list[Product]
    history: list[tuple] = field(default_factory=list)  # (product_id, price, sold_count, captured_at)


class DataSource(ABC):
    """数据源接口。实现 fetch 即可接入新来源（官方 API、第三方数据服务等）。"""

    @abstractmethod
    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        """获取商品数据。keyword 为搜索关键词（demo 模式可为空）。"""
        raise NotImplementedError
