"""第三方数据 API 数据源（可插拔适配器）。

用于对接 Kalodata / EchoTik / FastMoss 等 TikTok Shop 第三方数据服务，
替代/补充自建 Playwright 采集器：规避反爬，还能拿到更全的历史销量与带货数据。

对接步骤：
1. 在第三方平台注册账号、申请开放平台 API Key（一般有免费额度）。
2. 配置 provider / api_base / api_key（CLI 参数或环境变量）：
       python main.py run --source api --keyword "yoga mat" \
           --provider kalodata --api-base https://openapi.kalodata.com --api-key xxx
   等价环境变量：TTSHOP_API_PROVIDER / TTSHOP_API_BASE / TTSHOP_API_KEY
3. 各平台响应字段不同：在 PROVIDERS 中按官方文档调整 base_url、search_path、
   鉴权方式与 items_path（商品列表在响应 JSON 中的点号路径）即可。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import Settings
from ..models import Product
from .base import DataSource, SourceResult
from .fields import first_key, parse_count, parse_price, parse_rating

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderConfig:
    """单个第三方数据服务的接入配置（以平台官方文档为准）。"""
    name: str
    base_url: str
    search_path: str
    items_path: str = "data.list"        # 商品列表在响应 JSON 中的路径
    auth_header: str = "Authorization"   # 为空则改用 api_key 查询参数
    auth_prefix: str = "Bearer "


# 注意：以下为示例配置，正式接入前请按各平台最新官方文档核对
# base_url / search_path / 鉴权方式 / items_path，字段映射见 normalize_item。
PROVIDERS: dict[str, ProviderConfig] = {
    "kalodata": ProviderConfig(
        name="kalodata",
        base_url="https://openapi.kalodata.com",
        search_path="/openapi/v1/product/search",
        items_path="data.list",
    ),
    "echotik": ProviderConfig(
        name="echotik",
        base_url="https://api.echotik.live",
        search_path="/v1/products/search",
        items_path="data.list",
    ),
    "fastmoss": ProviderConfig(
        name="fastmoss",
        base_url="https://openapi.fastmoss.com",
        search_path="/api/v1/product/search",
        items_path="data.list",
    ),
}


def dig_path(payload: dict, path: str) -> list:
    """按点号路径取值，如 'data.list' -> payload['data']['list']。"""
    node: object = payload
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit():
            node = node[int(part)]
        else:
            return []
        if node is None:
            return []
    return node if isinstance(node, list) else []


class ApiSource(DataSource):
    """基于第三方数据 API 的商品数据源。"""

    def __init__(self, settings: Settings | None = None, provider: str | None = None,
                 api_base: str | None = None, api_key: str | None = None,
                 region: str | None = None, timeout: int | None = None):
        settings = settings or Settings()
        self.settings = settings
        self.provider = (provider or settings.api_provider
                         or os.environ.get("TTSHOP_API_PROVIDER") or "kalodata").lower()
        self.api_base = (api_base or settings.api_base
                         or os.environ.get("TTSHOP_API_BASE") or "").rstrip("/")
        self.api_key = api_key or settings.api_key or os.environ.get("TTSHOP_API_KEY") or ""
        self.region = region or settings.region
        self.timeout = timeout or settings.api_timeout

    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        if not keyword:
            raise ValueError('api 数据源需要 --keyword（例如 "yoga mat"）')
        if not self.api_key:
            raise RuntimeError(
                "未配置 API Key：请使用 --api-key 或环境变量 TTSHOP_API_KEY "
                "（需先在 Kalodata/EchoTik/FastMoss 等平台申请开放平台接口）"
            )
        config = self._provider_config()
        base = self.api_base or config.base_url
        if not base:
            raise RuntimeError("未配置 API 地址：请用 --api-base 或环境变量 TTSHOP_API_BASE")
        url = self._build_url(config, base, keyword, limit, category)
        log_url = url.replace(self.api_key, "***") if not config.auth_header else url
        logger.info("请求第三方数据 API: %s", log_url)
        payload = self._get_json(url, config)
        items = dig_path(payload, config.items_path)
        products: list[Product] = []
        for item in items:
            product = self.normalize_item(item, keyword, category)
            if product:
                products.append(product)
        if limit:
            products = products[:limit]
        logger.info("API 返回商品 %d 条", len(products))
        return SourceResult(products=products)

    def _provider_config(self) -> ProviderConfig:
        try:
            return PROVIDERS[self.provider]
        except KeyError:
            raise ValueError(
                f"未知数据源提供商: {self.provider}，可用: {', '.join(PROVIDERS)}"
            ) from None

    def _build_url(self, config: ProviderConfig, base: str, keyword: str,
                   limit: int | None, category: str | None) -> str:
        params = {"keyword": keyword, "region": self.region}
        if limit:
            params["page_size"] = limit
        if category:
            params["category"] = category
        if not config.auth_header:
            params["api_key"] = self.api_key
        return base + config.search_path + "?" + urlencode(params)

    def _get_json(self, url: str, config: ProviderConfig) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "ttshop-analytics/0.1"}
        if config.auth_header:
            headers[config.auth_header] = config.auth_prefix + self.api_key
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise RuntimeError(f"API 请求失败 HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"API 网络错误: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"API 响应不是合法 JSON: {exc}") from exc

    def normalize_item(self, item: dict, keyword: str,
                       category: str | None = None) -> Product | None:
        """将第三方平台返回的商品字段归一化为内部 Product 模型。"""
        try:
            title = str(first_key(item, ["title", "name", "product_title", "productTitle"]) or "").strip()
            price = parse_price(first_key(item, ["price", "sale_price", "salePrice", "current_price"]))
            if not title or price <= 0:
                return None
            product_id = str(first_key(item, ["product_id", "productId", "id"]) or "")
            if not product_id:
                product_id = f"api-{keyword}-{abs(hash(title)) % 10**10}"
            return Product(
                product_id=product_id,
                title=title,
                category=str(item.get("category") or item.get("category_name")
                             or category or "Unknown"),
                price=round(price, 2),
                original_price=round(
                    parse_price(first_key(item, ["original_price", "originalPrice", "list_price"])
                                or price), 2),
                sold_count=parse_count(first_key(
                    item, ["sales", "sold", "sold_count", "sales_count", "soldCount"])),
                rating=parse_rating(first_key(item, ["rating", "rating_score", "ratingScore"])),
                review_count=parse_count(first_key(
                    item, ["review_count", "reviewCount", "rating_count", "ratingCount"])),
                seller_name=str(first_key(
                    item, ["seller_name", "sellerName", "shop_name", "shopName"]) or ""),
                seller_id=str(first_key(
                    item, ["seller_id", "sellerId", "shop_id", "shopId"]) or ""),
                commission_rate=parse_price(first_key(
                    item, ["commission_rate", "commissionRate"])),
                video_views=parse_count(first_key(
                    item, ["video_views", "videoViews", "views"])),
                video_likes=parse_count(first_key(
                    item, ["video_likes", "videoLikes", "likes"])),
                listed_at=str(item.get("listed_at") or item.get("listedAt")
                              or item.get("create_time") or ""),
            )
        except (TypeError, ValueError) as exc:
            logger.debug("解析商品失败: %s", exc)
            return None
