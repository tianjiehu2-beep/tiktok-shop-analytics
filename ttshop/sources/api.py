"""Third-party data API source (pluggable adapter).

Connects to TikTok Shop data services such as FastMoss / Kalodata / EchoTik,
as a replacement/supplement for the in-house Playwright scraper.

Verified providers:
- EchoTik OpenAPI (echotik.live): keyword search / product list by category /
  batch product detail / product ranklist / category tree.
  Auth via `Authorization: Basic <base64(username:password)>`.
- FastMoss OpenAPI (developer.fastmoss.com): POST /product/v1/search with
  `Authorization: Bearer <client_secret>`, products under data.list.
- kalodata entry is a placeholder; verify before real use.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import Settings
from ..models import Product
from .base import DataSource, SourceResult
from .fields import first_key, parse_count, parse_price, parse_rating

logger = logging.getLogger(__name__)

# EchoTik product/list sort fields: value -> API product_sort_field
ECHOTIK_SORT_FIELDS = {
    "sales": 1,      # total_sale_cnt
    "gmv": 2,        # total_sale_gmv_amt
    "price": 3,      # spu_avg_price
    "sales7d": 4,    # total_sale_7d_cnt
    "sales30d": 5,   # total_sale_30d_cnt
    "gmv7d": 6,      # total_sale_gmv_7d_amt
    "gmv30d": 7,     # total_sale_gmv_30d_amt
}

ECHOTIK_RANK_FIELDS = {"sales": 1, "influencer": 2}
ECHOTIK_RANK_PERIODS = {"day": 1, "week": 2, "month": 3}
ECHOTIK_CATEGORY_LEVELS = [("l3", "category_l3_id"), ("l2", "category_l2_id")]


@dataclass(frozen=True)
class ProviderConfig:
    """Connection settings for one third-party data service."""
    name: str
    base_url: str
    search_path: str = ""            # keyword search endpoint
    product_list_path: str = ""      # category/filter product list endpoint
    product_detail_path: str = ""    # batch product detail endpoint
    ranklist_path: str = ""          # product ranklist endpoint
    category_path: str = ""          # level-1 category list
    category_l2_path: str = ""       # level-2 category list
    category_l3_path: str = ""       # level-3 category list
    items_path: str = "data.list"    # dotted path to product list in response
    auth_header: str = "Authorization"   # empty -> pass api_key as query param
    auth_prefix: str = "Bearer "
    method: str = "GET"              # GET or POST


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
        product_list_path="/api/v3/echotik/product/list",
        product_detail_path="/api/v3/echotik/product/detail",
        ranklist_path="/api/v3/echotik/product/ranklist",
        category_path="/api/v3/echotik/category/l1",
        category_l2_path="/api/v3/echotik/category/l2",
        category_l3_path="/api/v3/echotik/category/l3",
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


def _as_list(payload: dict, items_path: str) -> list:
    """Product list from response: dotted path first, then bare data array."""
    items = dig_path(payload, items_path)
    if not items and isinstance(payload.get("data"), list):
        items = payload["data"]
    return items


class ApiSource(DataSource):
    """Product data source backed by a third-party data API."""

    def __init__(self, settings: Settings | None = None, provider: str | None = None,
                 api_base: str | None = None, api_key: str | None = None,
                 region: str | None = None, timeout: int | None = None,
                 category_id: str | None = None, pages: int = 1,
                 sort_field: str | None = None, min_sales: int | None = None,
                 max_price: float | None = None, min_commission: float | None = None,
                 enrich: bool = False, language: str = "en-US"):
        settings = settings or Settings()
        self.settings = settings
        self.provider = (provider or settings.api_provider
                         or os.environ.get("TTSHOP_API_PROVIDER") or "fastmoss").lower()
        self.api_base = (api_base or settings.api_base
                         or os.environ.get("TTSHOP_API_BASE") or "").rstrip("/")
        self.api_key = api_key or settings.api_key or os.environ.get("TTSHOP_API_KEY") or ""
        self.region = region or settings.region
        self.timeout = timeout or settings.api_timeout
        self.category_id = (category_id or "").strip()
        self.pages = max(1, int(pages or 1))
        self.sort_field = (sort_field or "").lower()
        self.min_sales = min_sales
        self.max_price = max_price
        self.min_commission = min_commission
        self.enrich = bool(enrich)
        self.language = language
        self._tree: dict | None = None
        self.categories_cache = Path(settings.db_path).parent / "categories.json"

    # ------------------------------------------------------------------ fetch
    def fetch(self, keyword: str | None = None, limit: int | None = None,
              category: str | None = None) -> SourceResult:
        if not self.api_key:
            raise RuntimeError(
                "API key missing: pass --api-key or set TTSHOP_API_KEY "
                "(apply for one on FastMoss/Kalodata/EchoTik first)"
            )
        config = self._provider_config()
        base = self.api_base or config.base_url
        if not base:
            raise RuntimeError("API base URL missing: pass --api-base or set TTSHOP_API_BASE")

        if keyword:
            products = self._search_keywords(config, base, keyword, limit)
        elif self.category_id:
            products = self._fetch_by_category(config, base, limit)
        else:
            raise ValueError('api 数据源需要 --keyword 或 --category-id（例如 "yoga mat" / 600001）')

        if self.enrich and products:
            products = self._enrich_details(config, base, products)
        if limit:
            products = products[:limit]
        logger.info("API returned %d products", len(products))
        return SourceResult(products=products)

    # ---------------------------------------------------------- search / batch
    def _search_keywords(self, config: ProviderConfig, base: str,
                         keyword: str, limit: int | None) -> list[Product]:
        """Keyword search; supports comma-separated batch keywords, dedup by id."""
        keywords = [k.strip() for k in keyword.split(",") if k.strip()] or [keyword]
        seen: dict[str, Product] = {}
        for kw in keywords:
            url, body = self._build_request(config, base, kw, min(limit or 10, 30), None)
            log_url = url.replace(self.api_key, "***") if not config.auth_header else url
            logger.info("requesting third-party data API: %s", log_url)
            payload = self._request_json(url, config, body=body)
            for item in _as_list(payload, config.items_path):
                product = self.normalize_item(item, kw, category=None)
                if product:
                    seen.setdefault(product.product_id, product)
        return list(seen.values())

    # ------------------------------------------------------- category crawling
    def _fetch_by_category(self, config: ProviderConfig, base: str,
                           limit: int | None) -> list[Product]:
        """Crawl products of a category (EchoTik product/list), with pagination."""
        if not config.product_list_path:
            raise RuntimeError(f"provider {config.name!r} 不支持按类目采集（目前仅 EchoTik 支持）")
        tree = self._ensure_categories()
        level_param = self._category_level_param(tree, self.category_id)
        page_size = 10  # EchoTik product/list caps page_size at 10
        collected: list[Product] = []
        for page in range(1, self.pages + 1):
            if limit and len(collected) >= limit:
                break
            params = {
                "region": self.region,
                "page_num": page,
                "page_size": page_size,
                "off_mark": 0,
                level_param: self.category_id,
            }
            if self.sort_field in ECHOTIK_SORT_FIELDS:
                params["product_sort_field"] = ECHOTIK_SORT_FIELDS[self.sort_field]
                params["sort_type"] = 1
            if self.min_sales:
                params["min_total_sale_cnt"] = self.min_sales
            if self.max_price:
                params["max_spu_avg_price"] = self.max_price
            if self.min_commission:
                params["min_product_commission_rate"] = self.min_commission
            url = base + config.product_list_path + "?" + urlencode(params)
            logger.info("crawling category products: %s", url)
            payload = self._request_json(url, config)
            items = _as_list(payload, config.items_path)
            if not items:
                break
            for item in items:
                product = self.normalize_item(item, keyword="", category=self.category_id)
                if product:
                    collected.append(product)
        return collected

    def _enrich_details(self, config: ProviderConfig, base: str,
                        products: list[Product]) -> list[Product]:
        """Fetch batch product detail (rating/reviews/sales/GMV) and merge back."""
        if not config.product_detail_path:
            logger.info("provider %r 不支持商品详情补全，跳过 enrich", config.name)
            return products
        enriched: dict[str, Product] = {p.product_id: p for p in products}
        ids = list(enriched)
        for start in range(0, len(ids), 20):
            chunk = ids[start:start + 20]
            params = {"product_ids": ",".join(chunk), "region": self.region}
            url = base + config.product_detail_path + "?" + urlencode(params)
            try:
                payload = self._request_json(url, config)
            except RuntimeError as exc:
                logger.warning("detail enrich failed: %s", exc)
                continue
            for item in _as_list(payload, config.items_path):
                detail = self.normalize_item(item, keyword="", category=None)
                if not detail or detail.product_id not in enriched:
                    continue
                old = enriched[detail.product_id]
                enriched[detail.product_id] = Product(
                    product_id=old.product_id, title=old.title, category=old.category,
                    price=detail.price or old.price, original_price=old.original_price,
                    sold_count=detail.sold_count or old.sold_count,
                    rating=detail.rating or old.rating,
                    review_count=detail.review_count or old.review_count,
                    seller_name=detail.seller_name or old.seller_name,
                    seller_id=detail.seller_id or old.seller_id,
                    commission_rate=detail.commission_rate or old.commission_rate,
                    video_views=old.video_views, video_likes=old.video_likes,
                    listed_at=old.listed_at, sale_7d_cnt=old.sale_7d_cnt,
                    sale_30d_cnt=old.sale_30d_cnt, gmv_total=detail.gmv_total or old.gmv_total,
                    influencer_cnt=detail.influencer_cnt or old.influencer_cnt,
                    video_cnt=detail.video_cnt or old.video_cnt,
                    category_id=old.category_id,
                )
        return list(enriched.values())

    def fetch_ranklist(self, category_id: str | None = None, date: str | None = None,
                       period: str = "day", rank_field: str = "sales",
                       limit: int | None = None) -> SourceResult:
        """Fetch a product ranklist (EchoTik product/ranklist) and normalize."""
        if not self.api_key:
            raise RuntimeError("API key missing: pass --api-key or set TTSHOP_API_KEY")
        config = self._provider_config()
        if not config.ranklist_path:
            raise RuntimeError(f"provider {config.name!r} 不支持榜单接口（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url
        rank_type = ECHOTIK_RANK_PERIODS.get((period or "day").lower(), 1)
        rank_field_num = ECHOTIK_RANK_FIELDS.get((rank_field or "sales").lower(), 1)
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        params = {
            "date": date,
            "region": self.region,
            "product_rank_field": rank_field_num,
            "rank_type": rank_type,
            "page_num": 1,
            "page_size": min(limit or 10, 10),
        }
        if category_id:
            tree = self._ensure_categories()
            params[self._category_level_param(tree, category_id)] = category_id
        url = base + config.ranklist_path + "?" + urlencode(params)
        logger.info("requesting product ranklist: %s", url)
        payload = self._request_json(url, config)
        items = _as_list(payload, config.items_path)
        products = []
        for item in items:
            product = self.normalize_item(item, keyword="", category=category_id)
            if product:
                products.append(product)
        if limit:
            products = products[:limit]
        return SourceResult(products=products)

    # ------------------------------------------------------------ categories
    def fetch_categories(self, language: str | None = None, refresh: bool = False) -> dict:
        """Fetch the full category tree (l1/l2/l3) and cache it to disk."""
        language = language or self.language
        if not refresh and self.categories_cache.exists():
            try:
                tree = json.loads(self.categories_cache.read_text(encoding="utf-8"))
                if tree.get("language") == language and tree.get("l1"):
                    self._tree = tree
                    return tree
            except (OSError, json.JSONDecodeError):
                pass
        config = self._provider_config()
        if not config.category_path:
            raise RuntimeError(f"provider {config.name!r} 不支持类目接口（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url

        def _get(path: str) -> list:
            url = base + path + "?" + urlencode({"language": language})
            payload = self._request_json(url, config)
            data = payload.get("data")
            return data if isinstance(data, list) else []

        tree = {
            "language": language,
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "l1": _get(config.category_path),
            "l2": _get(config.category_l2_path),
            "l3": _get(config.category_l3_path),
        }
        self.categories_cache.parent.mkdir(parents=True, exist_ok=True)
        self.categories_cache.write_text(
            json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
        self._tree = tree
        return tree

    def _ensure_categories(self) -> dict:
        if self._tree is None:
            self._tree = self.fetch_categories()
        return self._tree

    def search_categories(self, term: str, limit: int = 50) -> list[dict]:
        """Search the category tree by name; returns full-path matches."""
        tree = self._ensure_categories()
        term_lower = (term or "").strip().lower()
        nodes = {c["category_id"]: c for level in ("l1", "l2", "l3") for c in tree.get(level, [])}
        matches = []
        for cid, node in nodes.items():
            path = self._category_path(tree, cid)
            if not term_lower or term_lower in path.lower() or term_lower in str(node.get("category_name", "")).lower():
                matches.append({
                    "category_id": cid,
                    "name": node.get("category_name", ""),
                    "level": node.get("category_level", ""),
                    "path": path,
                    "parent_id": node.get("parent_id", ""),
                })
        matches.sort(key=lambda m: (m["level"], m["path"]))
        return matches[:limit]

    def _category_level_param(self, tree: dict, category_id: str) -> str:
        for level, param in ECHOTIK_CATEGORY_LEVELS:
            if any(c.get("category_id") == category_id for c in tree.get(level, [])):
                return param
        return "category_id"

    def _category_name(self, tree: dict, category_id: str) -> str:
        for level in ("l1", "l2", "l3"):
            for c in tree.get(level, []):
                if c.get("category_id") == category_id:
                    return c.get("category_name", "")
        return ""

    def _category_path(self, tree: dict, category_id: str) -> str:
        names: dict[str, str] = {}
        parents: dict[str, str] = {}
        for level in ("l1", "l2", "l3"):
            for c in tree.get(level, []):
                cid = c.get("category_id", "")
                names[cid] = c.get("category_name", "")
                if cid not in parents or level != "l1":
                    parents[cid] = str(c.get("parent_id") or "")
        chain: list[str] = []
        cur = str(category_id)
        for _ in range(3):
            if not cur or cur not in names:
                break
            chain.append(names[cur])
            cur = parents.get(cur, "")
        return " > ".join(reversed(chain))

    # ------------------------------------------------------------ HTTP helpers
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

    # ----------------------------------------------------------- normalization
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
            l1_id = str(item.get("category_id") or "")
            l2_id = str(item.get("category_l2_id") or "")
            l3_id = str(item.get("category_l3_id") or "")
            category_id = l3_id or l2_id or l1_id
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
            if self._tree and (l1_id or l2_id or l3_id):
                parts: list[str] = []
                for cid in (l1_id, l2_id, l3_id):
                    name = self._category_name(self._tree, cid)
                    if name:
                        parts.append(name)
                    elif cid:
                        break
                if parts:
                    category_name = " > ".join(parts)
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
                sale_7d_cnt=parse_count(first_key(
                    item, ["total_sale_7d_cnt", "total_sale_7d", "sale_7d_cnt"])),
                sale_30d_cnt=parse_count(first_key(
                    item, ["total_sale_30d_cnt", "total_sale_30d", "sale_30d_cnt"])),
                gmv_total=parse_price(first_key(
                    item, ["total_sale_gmv_amt", "total_gmv", "gmv", "gmv_total"])),
                influencer_cnt=parse_count(first_key(
                    item, ["total_ifl_cnt", "total_influencer_cnt", "influencer_cnt", "total_ifl_video_cnt"])),
                video_cnt=parse_count(first_key(
                    item, ["total_video_cnt", "video_cnt"])),
                category_id=category_id,
            )
        except (TypeError, ValueError) as exc:
            logger.debug("failed to parse product: %s", exc)
            return None
