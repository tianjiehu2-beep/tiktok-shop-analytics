"""可插拔数据源：demo（模拟）/ scraper（Playwright 采集）/ api（第三方数据 API）。

新增数据源：实现 ttshop.sources.base.DataSource 并在 get_source 中注册即可。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Settings
from .api import PROVIDERS, ProviderConfig, ApiSource
from .base import DataSource, SourceResult
from .demo import DemoSource
from .failover import FailoverSource
from .scraper import ScraperSource

__all__ = [
    "DataSource", "SourceResult", "DemoSource", "ScraperSource", "ApiSource",
    "FailoverSource", "ProviderConfig", "PROVIDERS", "get_source",
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
        data_dir = Path(settings.db_path).parent
        api_provider = kwargs.pop("api_provider", None)
        api_key = kwargs.pop("api_key", None) or os.environ.get("TTSHOP_API_KEY") or ""
        if not api_key:
            # 与 auto 源一致：自动从本地密钥文件加载（EchoTik -> FastMoss）
            if api_provider is None:
                if _read_secret(data_dir / "api_key.txt"):
                    api_provider, api_key = "echotik", _read_secret(data_dir / "api_key.txt")
                elif _read_secret(data_dir / "fastmoss_key.txt"):
                    api_provider, api_key = "fastmoss", _read_secret(data_dir / "fastmoss_key.txt")
            elif str(api_provider).lower() == "echotik":
                api_key = _read_secret(data_dir / "api_key.txt")
            elif str(api_provider).lower() == "fastmoss":
                api_key = os.environ.get("FAST_MOSS_API_KEY") or _read_secret(data_dir / "fastmoss_key.txt")
        return ApiSource(
            settings=settings,
            provider=api_provider,
            api_base=kwargs.pop("api_base", None),
            api_key=api_key,
            region=kwargs.pop("region", settings.region),
            timeout=kwargs.pop("api_timeout", None),
            category_id=kwargs.pop("category_id", None),
            pages=kwargs.pop("pages", 1),
            sort_field=kwargs.pop("sort_field", None),
            min_sales=kwargs.pop("min_sales", None),
            max_price=kwargs.pop("max_price", None),
            min_commission=kwargs.pop("min_commission", None),
            enrich=kwargs.pop("enrich", False),
            language=kwargs.pop("language", "en-US"),
        )
    if name == "auto":
        # 多源故障切换：EchoTik API -> FastMoss API -> demo，任一成功即停止
        api_kwargs = {
            "region": kwargs.pop("region", settings.region),
            "timeout": kwargs.pop("api_timeout", None),
            "category_id": kwargs.pop("category_id", None),
            "pages": kwargs.pop("pages", 1),
            "sort_field": kwargs.pop("sort_field", None),
            "min_sales": kwargs.pop("min_sales", None),
            "max_price": kwargs.pop("max_price", None),
            "min_commission": kwargs.pop("min_commission", None),
            "enrich": kwargs.pop("enrich", False),
            "language": kwargs.pop("language", "en-US"),
        }
        kwargs.pop("api_provider", None)
        kwargs.pop("api_base", None)
        data_dir = Path(settings.db_path).parent
        echo_key = (kwargs.pop("api_key", None)
                    or os.environ.get("TTSHOP_API_KEY")
                    or _read_secret(data_dir / "api_key.txt"))
        fm_key = (os.environ.get("FAST_MOSS_API_KEY")
                  or _read_secret(data_dir / "fastmoss_key.txt"))
        sources: list[tuple[str, DataSource]] = []
        if echo_key:
            sources.append(("api", ApiSource(settings=settings, provider="echotik",
                                             api_key=echo_key, **api_kwargs)))
        if fm_key:
            sources.append(("fastmoss", ApiSource(settings=settings, provider="fastmoss",
                                                  api_key=fm_key, **api_kwargs)))
        sources.append(("demo", DemoSource(seed=kwargs.pop("seed", None),
                                           product_count=kwargs.pop("product_count", 200))))
        return FailoverSource(sources)
    raise ValueError(f"未知数据源: {name!r}，可选 demo / scraper / api / auto")


def _read_secret(path: Path) -> str:
    """读取本地密钥文件（如 data/api_key.txt），不存在时返回空串。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
