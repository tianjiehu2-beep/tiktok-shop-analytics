"""多数据源故障切换：按优先级依次尝试，全部失败才抛错。

用法（main.py 提供 --source auto）：
  python main.py run --source auto --category-id 603084
会依次尝试 EchoTik API -> FastMoss API -> demo，任一成功即停止。
每次尝试内部还带请求级重试退避（见 sources/api.py 的 _request_json）。
"""

from __future__ import annotations

import logging

from .base import DataSource, SourceResult

logger = logging.getLogger(__name__)


class FailoverSource(DataSource):
    """包装多个数据源，按顺序尝试，记录实际命中的数据源名称。"""

    def __init__(self, sources: list[tuple[str, DataSource]]):
        self.sources = sources
        self.used_source: str | None = None

    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        last_error: Exception | None = None
        for name, source in self.sources:
            try:
                result = source.fetch(keyword=keyword, limit=limit, category=category)
                if result.products or result.history:
                    self.used_source = name
                    logger.info("故障切换命中数据源 %s（%d 商品）", name, len(result.products))
                    return result
                last_error = RuntimeError(f"{name} 返回空数据")
                logger.warning("%s 返回空数据，尝试下一个数据源", name)
            except Exception as exc:  # noqa: BLE001 - 故障切换需要捕获任意来源错误
                last_error = exc
                logger.warning("数据源 %s 失败: %s，尝试下一个数据源", name, exc)
        raise RuntimeError(f"全部数据源均失败: {last_error}") from last_error
