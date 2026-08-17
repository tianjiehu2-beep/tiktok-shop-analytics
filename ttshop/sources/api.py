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
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import Settings
from ..models import Influencer, KeywordTrend, Product
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

# EchoTik influencer/list sort fields
ECHOTIK_INFLUENCER_SORTS = {
    "followers": 1,    # total_followers_cnt
    "followers30d": 2, # total_followers_30d_cnt
    "posts": 3,        # total_post_video_cnt
    "views": 4,        # per_views_avg_cnt
    "interaction": 5,  # interaction_rate
}
ECHOTIK_INFLUENCER_RANK_FIELDS = {"followers": 1, "sales": 2}
ECHOTIK_KEYWORD_TABS = ["all", "Fashion", "Food", "Sports", "Tourism", "Gaming", "Science"]


@dataclass(frozen=True)
class ProviderConfig:
    """Connection settings for one third-party data service."""
    name: str
    base_url: str
    search_path: str = ""            # keyword search endpoint
    product_list_path: str = ""      # category/filter product list endpoint
    product_detail_path: str = ""    # batch product detail endpoint
    shop_products_path: str = ""     # seller shop product list endpoint
    ranklist_path: str = ""          # product ranklist endpoint
    category_path: str = ""          # level-1 category list
    category_l2_path: str = ""       # level-2 category list
    category_l3_path: str = ""       # level-3 category list
    influencer_list_path: str = ""   # influencer list endpoint
    influencer_ranklist_path: str = ""  # influencer ranklist endpoint
    product_influencer_path: str = ""   # product -> influencer list endpoint
    keyword_ranking_path: str = ""      # trending keyword ranking endpoint
    keyword_inspiration_path: str = ""  # keyword inspiration endpoint
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
        shop_products_path="/api/v3/echotik/seller/product/list",
        ranklist_path="/api/v3/echotik/product/ranklist",
        category_path="/api/v3/echotik/category/l1",
        category_l2_path="/api/v3/echotik/category/l2",
        category_l3_path="/api/v3/echotik/category/l3",
        influencer_list_path="/api/v3/echotik/influencer/list",
        influencer_ranklist_path="/api/v3/echotik/influencer/ranklist",
        product_influencer_path="/api/v3/echotik/product/influencer/list",
        keyword_ranking_path="/api/v3/realtime/trending/keyword/ranking",
        keyword_inspiration_path="/api/v3/realtime/inspiration/keyword",
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


class _QuotaError(Exception):
    """Internal signal: current API key has exhausted its quota."""


def _split_keys(raw: str) -> list[str]:
    """Split a key string into distinct keys (supports , | ; newline)."""
    seen: set[str] = set()
    keys: list[str] = []
    for part in re.split(r"[,|;\n]+", raw or ""):
        key = part.strip()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


class ApiSource(DataSource):
    """Product data source backed by a third-party data API."""

    def __init__(self, settings: Settings | None = None, provider: str | None = None,
                 api_base: str | None = None, api_key: str | None = None,
                 region: str | None = None, timeout: int | None = None,
                 category_id: str | None = None, pages: int = 1,
                 seller_id: str | None = None, product_ids: str | None = None,
                 sort_field: str | None = None, min_sales: int | None = None,
                 max_price: float | None = None, min_commission: float | None = None,
                 enrich: bool = False, language: str = "en-US"):
        settings = settings or Settings()
        self.settings = settings
        self.provider = (provider or settings.api_provider
                         or os.environ.get("TTSHOP_API_PROVIDER") or "fastmoss").lower()
        self.api_base = (api_base or settings.api_base
                         or os.environ.get("TTSHOP_API_BASE") or "").rstrip("/")
        raw_key = (api_key or settings.api_key
                   or os.environ.get("TTSHOP_API_KEYS")
                   or os.environ.get("TTSHOP_API_KEY") or "")
        self._api_keys = _split_keys(raw_key)
        self.api_key = self._api_keys[0] if self._api_keys else ""
        self._key_idx = 0
        self.region = region or settings.region
        self.timeout = timeout or settings.api_timeout
        self.category_id = (category_id or "").strip()
        self.seller_id = (seller_id or "").strip()
        self.product_ids = [i.strip() for i in (product_ids or "").split(",") if i.strip()]
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

        if self.product_ids:
            products = self._fetch_by_ids(config, base, limit)
        elif self.seller_id:
            products = self._fetch_shop_products(config, base, limit)
        elif keyword:
            products = self._search_keywords(config, base, keyword, limit)
        elif self.category_id:
            products = self._fetch_by_category(config, base, limit)
        else:
            raise ValueError('api 数据源需要 --keyword / --category-id / --seller-id / --product-ids 之一（例如 --category-id 603084 或 --seller-id 7496125336660249320）')

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

    def _fetch_by_ids(self, config: ProviderConfig, base: str,
                      limit: int | None) -> list[Product]:
        """Fetch exact products by product IDs (EchoTik product/detail, batch of 20)."""
        if not config.product_detail_path:
            raise RuntimeError(f"provider {config.name!r} 不支持按商品ID采集（当前仅 EchoTik 支持）")
        collected: list[Product] = []
        ids = list(dict.fromkeys(self.product_ids))
        for start in range(0, len(ids), 20):
            chunk = ids[start:start + 20]
            params = {"product_ids": ",".join(chunk), "region": self.region}
            url = base + config.product_detail_path + "?" + urlencode(params)
            logger.info("fetching products by ids: %s", url)
            payload = self._request_json(url, config)
            for item in _as_list(payload, config.items_path):
                product = self.normalize_item(item, keyword="", category=None)
                if product:
                    collected.append(product)
            if limit and len(collected) >= limit:
                break
        return collected[:limit] if limit else collected

    def _fetch_shop_products(self, config: ProviderConfig, base: str,
                             limit: int | None) -> list[Product]:
        """Crawl all products of a seller/shop (EchoTik seller/product/list)."""
        if not config.shop_products_path:
            raise RuntimeError(f"provider {config.name!r} 不支持按店铺采集（当前仅 EchoTik 支持）")
        page_size = 10  # seller/product/list caps page_size at 10
        collected: list[Product] = []
        for page in range(1, self.pages + 1):
            if limit and len(collected) >= limit:
                break
            params = {
                "seller_id": self.seller_id,
                "region": self.region,
                "page_num": page,
                "page_size": page_size,
            }
            url = base + config.shop_products_path + "?" + urlencode(params)
            logger.info("crawling shop products: %s", url)
            payload = self._request_json(url, config)
            items = _as_list(payload, config.items_path)
            if not items:
                break
            for item in items:
                product = self.normalize_item(item, keyword="", category=None)
                if product:
                    if not product.seller_id:
                        product = replace(product, seller_id=self.seller_id)
                    collected.append(product)
        return collected[:limit] if limit else collected

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

    # ------------------------------------------------------------ influencers
    def fetch_influencers(self, category_id: str | None = None, sort: str = "followers",
                          pages: int = 1, min_followers: int | None = None,
                          min_gmv: float | None = None, sales_flag: int = 3,
                          limit: int | None = None) -> list[Influencer]:
        """Fetch influencer list (EchoTik influencer/list) with pagination."""
        if not self.api_key:
            raise RuntimeError("API key missing: pass --api-key or set TTSHOP_API_KEY")
        config = self._provider_config()
        if not config.influencer_list_path:
            raise RuntimeError(f"provider {config.name!r} 不支持达人接口（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url
        sort_num = ECHOTIK_INFLUENCER_SORTS.get((sort or "followers").lower(), 1)
        collected: list[Influencer] = []
        for page in range(1, max(1, int(pages or 1)) + 1):
            params = {
                "region": self.region,
                "page_num": page,
                "page_size": 10,
                "influencer_sort_field_v2": sort_num,
                "sort_type": 1,
                "sales_flag": sales_flag,   # >0 代表带货
            }
            if category_id:
                tree = self._ensure_categories()
                params[self._category_level_param(tree, category_id)] = category_id
            if min_followers:
                params["min_total_followers_cnt"] = min_followers
            if min_gmv:
                params["min_total_sale_gmv_amt"] = min_gmv
            url = base + config.influencer_list_path + "?" + urlencode(params)
            payload = self._request_json(url, config)
            items = _as_list(payload, config.items_path)
            if not items:
                break
            for item in items:
                inf = self.normalize_influencer(item)
                if inf:
                    collected.append(inf)
            if limit and len(collected) >= limit:
                break
        return collected[:limit] if limit else collected

    def fetch_influencer_ranklist(self, category_id: str | None = None,
                                  date: str | None = None, period: str = "day",
                                  rank_field: str = "sales",
                                  limit: int | None = None) -> list[Influencer]:
        """Fetch influencer ranklist (EchoTik influencer/ranklist)."""
        if not self.api_key:
            raise RuntimeError("API key missing: pass --api-key or set TTSHOP_API_KEY")
        config = self._provider_config()
        if not config.influencer_ranklist_path:
            raise RuntimeError(f"provider {config.name!r} 不支持达人榜单（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url
        params = {
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "region": self.region,
            "rank_type": ECHOTIK_RANK_PERIODS.get((period or "day").lower(), 1),
            "influencer_rank_field": ECHOTIK_INFLUENCER_RANK_FIELDS.get((rank_field or "sales").lower(), 2),
            "page_num": 1,
            "page_size": min(limit or 10, 10),
        }
        if category_id:
            tree = self._ensure_categories()
            params[self._category_level_param(tree, category_id)] = category_id
        url = base + config.influencer_ranklist_path + "?" + urlencode(params)
        payload = self._request_json(url, config)
        items = _as_list(payload, config.items_path)
        influencers = []
        for item in items:
            inf = self.normalize_influencer(item)
            if inf:
                influencers.append(inf)
        return influencers[:limit] if limit else influencers

    def fetch_product_influencers(self, product_id: str,
                                  limit: int = 5) -> list[dict]:
        """Fetch influencers who carry a product (EchoTik product/influencer/list)."""
        if not self.api_key:
            raise RuntimeError("API key missing: pass --api-key or set TTSHOP_API_KEY")
        config = self._provider_config()
        if not config.product_influencer_path:
            raise RuntimeError(f"provider {config.name!r} 不支持商品关联达人（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url
        params = {
            "product_id": product_id,
            "product_influencer_sort_field": 3,   # per_product_ifl_sale_cnt
            "sort_type": 1,
            "page_num": 1,
            "page_size": min(limit or 5, 10),
        }
        url = base + config.product_influencer_path + "?" + urlencode(params)
        payload = self._request_json(url, config)
        items = _as_list(payload, config.items_path)
        rows = []
        for it in items[:limit]:
            rows.append({
                "product_id": str(it.get("product_id") or product_id),
                "user_id": str(it.get("user_id") or it.get("unique_id") or ""),
                "nick_name": str(it.get("nick_name") or ""),
                "followers_cnt": parse_count(it.get("total_followers_cnt")),
                "per_sale_cnt": parse_count(it.get("per_product_ifl_sale_cnt")),
                "per_gmv_amt": parse_price(it.get("per_product_ifl_gmv_amt")),
            })
        return rows

    def fetch_keyword_trends(self, tab: str = "all",
                             count: int = 20) -> list[KeywordTrend]:
        """Fetch trending keyword ranking (realtime/trending/keyword/ranking)."""
        if not self.api_key:
            raise RuntimeError("API key missing: pass --api-key or set TTSHOP_API_KEY")
        config = self._provider_config()
        if not config.keyword_ranking_path:
            raise RuntimeError(f"provider {config.name!r} 不支持关键词趋势（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url
        tab = tab if tab in ECHOTIK_KEYWORD_TABS else "all"
        url = base + config.keyword_ranking_path + "?" + urlencode({
            "tab": tab, "region": self.region, "count": min(count or 20, 50)})
        payload = self._request_json(url, config)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = data.get("inspiration_list") or []
        trends = []
        for it in items:
            keyword = str(it.get("query_text") or "").strip()
            if not keyword:
                continue
            trends.append(KeywordTrend(
                keyword=keyword,
                video_num=parse_count(it.get("video_num")),
                popularity=parse_count(it.get("popularity_v2") or it.get("popularity")),
                trend=it.get("trending_seq_v2") or it.get("trending_seq") or [],
                region=self.region,
                source="ranking",
            ))
        return trends[:count]

    def fetch_keyword_inspiration(self, keyword: str,
                                  count: int = 20) -> list[KeywordTrend]:
        """Fetch keyword inspiration (realtime/inspiration/keyword)."""
        if not self.api_key:
            raise RuntimeError("API key missing: pass --api-key or set TTSHOP_API_KEY")
        config = self._provider_config()
        if not config.keyword_inspiration_path:
            raise RuntimeError(f"provider {config.name!r} 不支持关键词灵感（目前仅 EchoTik 支持）")
        base = self.api_base or config.base_url
        url = base + config.keyword_inspiration_path + "?" + urlencode({
            "keyword": keyword, "region": self.region, "count": min(count or 20, 50)})
        payload = self._request_json(url, config)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = data.get("inspiration_list") or []
        trends = []
        for it in items:
            kw = str(it.get("query_text") or "").strip()
            if not kw:
                continue
            trends.append(KeywordTrend(
                keyword=kw,
                video_num=parse_count(it.get("video_num")),
                popularity=parse_count(it.get("popularity_v2") or it.get("popularity")),
                trend=it.get("trending_seq_v2") or it.get("trending_seq") or [],
                region=self.region,
                source="inspiration",
            ))
        return trends[:count]

    def normalize_influencer(self, item: dict) -> Influencer | None:
        user_id = str(first_key(item, ["user_id", "userId", "unique_id"]) or "").strip()
        if not user_id:
            return None
        try:
            return Influencer(
                user_id=user_id,
                nick_name=str(item.get("nick_name") or ""),
                avatar=str(item.get("avatar") or ""),
                signature=str(item.get("signature") or ""),
                region=str(item.get("region") or self.region),
                followers_cnt=parse_count(item.get("total_followers_cnt")),
                followers_30d_cnt=parse_count(item.get("total_followers_30d_cnt")),
                post_video_cnt=parse_count(item.get("total_post_video_cnt")),
                digg_cnt=parse_count(item.get("total_digg_cnt")),
                likes_cnt=parse_count(item.get("total_likes_cnt")),
                interaction_rate=parse_price(item.get("interaction_rate")),
                ec_score=parse_price(item.get("ec_score")),
                sale_cnt=parse_count(item.get("total_sale_cnt")),
                sale_gmv_amt=parse_price(item.get("total_sale_gmv_amt")),
                sale_gmv_30d_amt=parse_price(item.get("total_sale_gmv_30d_amt")),
                product_cnt=parse_count(item.get("total_product_cnt")),
                live_cnt=parse_count(item.get("total_live_cnt")),
                per_video_views_avg_7d=parse_price(
                    item.get("per_video_product_views_avg_7d_cnt")),
                category=str(item.get("category") or ""),
            )
        except (TypeError, ValueError):
            return None

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
            params["api_key"] = self._current_key()
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

    # --------------------------------------------------------------- key ring
    def _current_key(self) -> str:
        """Active API key（额度用尽后自动切到下一个）。"""
        return self._api_keys[min(self._key_idx, len(self._api_keys) - 1)]

    def _rotate_key(self) -> bool:
        """切到下一个 key；全部用完返回 False。"""
        if self._key_idx + 1 >= len(self._api_keys):
            return False
        self._key_idx += 1
        logger.warning("API key[%d] 不可用，切换到第 %d/%d 个 key",
                       self._key_idx, self._key_idx + 1, len(self._api_keys))
        return True

    @staticmethod
    def _is_quota_http(code: int) -> bool:
        """HTTP 状态码：额度用尽 / 凭据失效时常见的错误码。"""
        return code in (401, 402, 403, 429)

    @staticmethod
    def _is_quota_payload(payload: dict) -> bool:
        """业务层错误：非成功 code + 额度相关提示，视为该 key 额度用尽。"""
        code = payload.get("code") or payload.get("status") or payload.get("error_code")
        if code in (None, 0, 1, 200, "0", "1", "200"):
            return False
        if str(code) in ("401", "402", "403", "429"):
            return True
        message = str(payload.get("message") or payload.get("msg")
                      or payload.get("error") or payload.get("error_message") or "")
        lower = message.lower()
        return any(kw in lower for kw in (
            "quota", "limit", "balance", "insufficient", "exhaust", "trial",
            "额度", "余额", "次数", "不足", "用完", "耗尽", "套餐", "充值",
        ))

    def _request_json(self, url: str, config: ProviderConfig,
                      body: dict | None = None, retries: int = 2) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "ttshop-analytics/0.1"}
        if config.auth_header:
            headers[config.auth_header] = config.auth_prefix + self._current_key()
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        last_error: Exception | None = None
        attempt = 0
        while True:
            request = Request(url, data=data, headers=headers)
            try:
                with urlopen(request, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                if self._is_quota_payload(payload):
                    raise _QuotaError("quota exhausted")
                return payload
            except _QuotaError as exc:
                last_error = exc
                if self._rotate_key():
                    headers[config.auth_header] = config.auth_prefix + self._current_key()
                    continue
                raise RuntimeError(
                    "所有 API key 均不可用（额度用尽或凭据失效）："
                    "请补充新账号 key（TTSHOP_API_KEYS 逗号分隔）或改用 demo 源"
                ) from exc
            except HTTPError as exc:
                if self._is_quota_http(exc.code):
                    last_error = exc
                    if self._rotate_key():
                        headers[config.auth_header] = config.auth_prefix + self._current_key()
                        continue
                    raise RuntimeError(
                        f"所有 API key 均不可用（HTTP {exc.code}，额度用尽或凭据失效）："
                        "请补充新账号 key（TTSHOP_API_KEYS 逗号分隔）或改用 demo 源"
                    ) from exc
                last_error = exc
                if attempt < retries and exc.code >= 500:
                    wait = 2 ** attempt
                    logger.warning("API HTTP %s（第 %d 次），%ds 后重试: %s",
                                   exc.code, attempt + 1, wait, url)
                    time.sleep(wait)
                    attempt += 1
                    continue
                raise RuntimeError(f"API request failed HTTP {exc.code}: {exc.reason}") from exc
            except URLError as exc:
                last_error = exc
                if attempt < retries:
                    wait = 2 ** attempt
                    logger.warning("API 网络错误（第 %d 次），%ds 后重试: %s",
                                   attempt + 1, wait, url)
                    time.sleep(wait)
                    attempt += 1
                    continue
                raise RuntimeError(f"API network error: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"API response is not valid JSON: {exc}") from exc
        raise RuntimeError(f"API request failed: {last_error}") from last_error

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
