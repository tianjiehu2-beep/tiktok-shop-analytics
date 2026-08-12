"""Third-party data API source (pluggable adapter).

Connects to TikTok Shop data services such as FastMoss / Kalodata / EchoTik,
as a replacement/supplement for the in-house Playwright scraper.

How to use:
1. Register an account on the third-party platform and get an API key.
2. Configure provider / api_base / api_key via CLI args or env vars:
       python main.py run --source api --keyword "yoga mat" \
           --provider fastmoss --api-key <client_secret>
   Env vars: TTSHOP_API_PROVIDER / TTSHOP_API_BASE / TTSHOP_API_KEY
3. Field layouts differ per platform: adjust base_url / search_path / auth /
   method / items_path in PROVIDERS according to the official docs.

Verified: FastMoss OpenAPI (developer.fastmoss.com) - POST /product/v1/search,
auth via `Authorization: Bearer <client_secret>`, products under data.list.
kalodata entry is a placeholder; verify before real use.
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
    """Connection settings for one third-party data service."""
    name: str
    base_url: str
    search_path: str
    items_path: str = "data.list"        # dotted path to product list in response
    auth_header: str = "Authorization"   # empty -> pass api_key as query param
    auth_prefix: str = "Bearer "
    method: str = "GET"                  # GET or POST


PROVIDERS: dict[str, ProviderConfig] = {
    "fastmoss": ProviderConfig(
        name="fastmoss",
        base_url="https://openapi.fastmoss.com",
        search_path="/product/v1/search",
        items_path="data.list",
        auth_prefix="Bearer ",
        method="POST",
    ),
    "kalodata": ProviderConfig(
        name="kalodata",
        base_url="https://openapi.kalodata.com",
        search_path="/openapi/v1/product/search",
        items_path="data.list",
    ),
    "echotik": ProviderConfig(
        name="echotik",
        base_url="https://open.echotik.live",
        search_path="/api/v3/echotik/search/items",
        items_path="data.list",
        auth_prefix="Basic ",   # api_key = base64(username:password)
        method="GET",
    ),
}


def dig_path(payload: dict, path: str) -> list:
    """Resolve a dotted path like 'data.list' -> payload['data']['list']."""
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


def _dig(data: dict, path: str, default=None):
    """Resolve a dotted path inside a nested dict, e.g. 'shop.name'."""
    node: object = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
        if node is None:
            return default
    return node


class ApiSource(DataSource):
    """Product data source backed by a third-party data API."""

    def __init__(self, settings: Settings | None = None, provider: str | None = None,
                 api_base: str | None = None, api_key: str | None = None,
                 region: str | None = None, timeout: int | None = None):
        settings = settings or Settings()
        self.settings = settings
        self.provider = (provider or settings.api_provider
                         or os.environ.get("TTSHOP_API_PROVIDER") or "fastmoss").lower()
        self.api_base = (api_base or settings.api_base
                         or os.environ.get("TTSHOP_API_BASE") or "").rstrip("/")
        self.api_key = api_key or settings.api_key or os.environ.get("TTSHOP_API_KEY") or ""
        self.region = region or settings.region
        self.timeout = timeout or settings.api_timeout

    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        if not keyword:
            raise ValueError('api source requires --keyword (e.g. "yoga mat")')
        if not self.api_key:
            raise RuntimeError(
                "API key missing: pass --api-key or set TTSHOP_API_KEY "
                "(apply for one on FastMoss/Kalodata/EchoTik first)"
            )
        config = self._provider_config()
        base = self.api_base or config.base_url
        if not base:
            raise RuntimeError("API base URL missing: pass --api-base or set TTSHOP_API_BASE")
        url, body = self._build_request(config, base, keyword, limit, category)
        log_url = url.replace(self.api_key, "***") if not config.auth_header else url
        logger.info("requesting third-party data API: %s", log_url)
        payload = self._request_json(url, config, body=body)
        items = dig_path(payload, config.items_path)
        if not items and isinstance(payload.get("data"), list):
            items = payload["data"]
        products: list[Product] = []
        for item in items:
            product = self.normalize_item(item, keyword, category)
            if product:
                products.append(product)
        if limit:
            products = products[:limit]
        logger.info("API returned %d products", len(products))
        return SourceResult(products=products)

    def _provider_config(self) -> ProviderConfig:
        try:
            return PROVIDERS[self.provider]
        except KeyError:
            raise ValueError(
                f"unknown provider: {self.provider}, available: {', '.join(PROVIDERS)}"
            ) from None

    def _query_params(self, config: ProviderConfig, keyword: str,
                      limit: int | None, category: str | None) -> dict:
        """Provider-specific query params for GET-style providers."""
        if config.name == "echotik":
            params: dict = {
                "sk": keyword,
                "region": self.region,
                "type": 2,                       # product search
                "size": min(limit or 10, 30),    # search/items caps at 30
                "sortType": 4,                   # total_sale_cnt desc
            }
        else:
            params = {"keyword": keyword, "region": self.region}
            if limit:
                params["page_size"] = limit
            if category:
                params["category"] = category
        if not config.auth_header:
            params["api_key"] = self.api_key
        return params

    def _build_url(self, config: ProviderConfig, base: str, keyword: str,
                   limit: int | None, category: str | None) -> str:
        """Build a GET query URL (used by GET-style providers)."""
        params = self._query_params(config, keyword, limit, category)
        return base + config.search_path + "?" + urlencode(params)

    def _build_request(self, config: ProviderConfig, base: str, keyword: str,
                       limit: int | None, category: str | None) -> tuple[str, dict | None]:
        """Return (url, body); body is None for GET providers."""
        if config.method.upper() == "POST":
            body = {
                "keywords": keyword,
                "filter": {"region": self.region or "US", "off_shelves": 0},
                "orderby": [{"field": "day7_units_sold", "order": "desc"}],
                "page": 1,
                "pagesize": min(limit or 10, 100),
            }
            return base + config.search_path, body
        return self._build_url(config, base, keyword, limit, category), None

    def _request_json(self, url: str, config: ProviderConfig,
                      body: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "ttshop-analytics/0.1"}
        if config.auth_header:
            headers[config.auth_header] = config.auth_prefix + self.api_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise RuntimeError(f"API request failed HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"API network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"API response is not valid JSON: {exc}") from exc

    def normalize_item(self, item: dict, keyword: str,
                       category: str | None = None) -> Product | None:
        """Normalize a provider product record into the internal Product model."""
        try:
            title = str(first_key(item, ["title", "name", "product_title", "productTitle", "product_name"]) or "").strip()
            price = parse_price(first_key(
                item, ["floor_price", "price", "min_price", "spu_avg_price", "sale_price", "salePrice", "current_price"]))
            if not title or price <= 0:
                return None
            product_id = str(first_key(item, ["product_id", "productId", "id"]) or "")
            if not product_id:
                product_id = f"api-{keyword}-{abs(hash(title)) % 10**10}"
            category_raw = item.get("category") or item.get("category_name") or ""
            if isinstance(category_raw, dict):
                names = [
                    category_raw.get(level, {}).get("name")
                    for level in ("l1", "l2", "l3")
                    if isinstance(category_raw.get(level), dict)
                ]
                category_name = " > ".join(n for n in names if n) or str(category or "Unknown")
            else:
                category_name = str(category_raw or category or "Unknown")
            shop = item.get("shop") if isinstance(item.get("shop"), dict) else {}
            seller_name = str(first_key(
                item, ["seller_name", "sellerName", "shop_name", "shopName"])
                or _dig(shop, "name") or "")
            seller_id = str(first_key(
                item, ["seller_id", "sellerId", "shop_id", "shopId"])
                or _dig(shop, "seller_id") or _dig(shop, "id") or "")
            return Product(
                product_id=product_id,
                title=title,
                category=category_name,
                price=round(price, 2),
                original_price=round(
                    parse_price(first_key(item, ["original_price", "originalPrice", "list_price"])
                                or price), 2),
                sold_count=parse_count(first_key(
                    item, ["total_units_sold", "total_sale_cnt", "sales", "sold", "sold_count", "sales_count", "soldCount"])),
                rating=parse_rating(first_key(
                    item, ["product_rating", "rating", "rating_score", "ratingScore"])),
                review_count=parse_count(first_key(
                    item, ["review_count", "reviewCount", "rating_count", "ratingCount"])),
                seller_name=seller_name,
                seller_id=seller_id,
                commission_rate=parse_price(first_key(
                    item, ["commission_rate", "product_commission_rate", "commissionRate"])),
                video_views=parse_count(first_key(
                    item, ["video_views", "videoViews", "views"])),
                video_likes=parse_count(first_key(
                    item, ["video_likes", "videoLikes", "likes"])),
                listed_at=str(item.get("listed_at") or item.get("listedAt")
                              or item.get("create_time") or item.get("ctime") or ""),
            )
        except (TypeError, ValueError) as exc:
            logger.debug("failed to parse product: %s", exc)
            return None