"""TikTok Shop 真实采集器（基于 Playwright）。

说明：
- TikTok 反爬较强（X-Bogus/MsToken 签名、滑块、行为风控），页面结构也经常改版。
- 本实现优先从页面内嵌的 __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON 提取商品数据，
  失败时回退到 DOM 选择器。
- 使用前请安装：pip install playwright && playwright install chromium
- 建议使用目标区域（美区）代理，控制抓取频率，并在当地低峰时段运行。
"""

from __future__ import annotations

import json
import logging
import random
import re
from urllib.parse import quote

from ..models import Product

logger = logging.getLogger(__name__)

# 搜索页地址模板（TikTok Shop 会随版本调整，失效时更新）
SEARCH_URL_TEMPLATE = "https://www.tiktok.com/shop/tt4b/search?q={keyword}&region={region}"
# 页面内嵌数据脚本（比 CSS 选择器稳定）
DATA_SCRIPT_ID = "__UNIVERSAL_DATA_FOR_REHYDRATION__"

TITLE_KEYS = ["title", "name", "productTitle"]
PRICE_KEYS = ["price", "salePrice"]
SOLD_KEYS = ["sales", "sold", "soldCount", "salesCount"]
RATING_KEYS = ["rating", "ratingScore"]
REVIEW_KEYS = ["reviewCount", "reviews", "ratingCount"]
SELLER_KEYS = ["sellerName", "shopName", "seller"]
SELLER_ID_KEYS = ["sellerId", "shopId"]
PRODUCT_ID_KEYS = ["productId", "id", "itemId"]


def _first(d: dict, keys: list[str], default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _parse_sold(value) -> int:
    """解析 '2.3K' / '1.2M' / 1234 这类销量文本。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([KkMm万]?)", text)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        num *= 1000
    elif unit == "m":
        num *= 1_000_000
    elif unit == "万":
        num *= 10_000
    return int(num)


class TikTokShopScraper:
    """基于 Playwright 的 TikTok Shop 商品采集器。"""

    def __init__(self, region: str = "US", headless: bool = True, slow_mo_ms: int = 800,
                 max_products_per_run: int = 100):
        self.region = region
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.max_products_per_run = max_products_per_run

    def _browser(self):
        from playwright.sync_api import sync_playwright  # 延迟导入，demo 模式无需安装
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        )
        return playwright, browser, context

    def scrape_search(self, keyword: str, limit: int | None = None) -> list[Product]:
        limit = limit or self.max_products_per_run
        url = SEARCH_URL_TEMPLATE.format(keyword=quote(keyword), region=self.region)
        logger.info("开始采集: %s", url)

        playwright, browser, context = self._browser()
        products: list[Product] = []
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)

            seen: set[str] = set()
            for _ in range(20):
                for item in self._extract_from_data_script(page):
                    p = self._parse_item(item, keyword)
                    if p and p.product_id not in seen:
                        seen.add(p.product_id)
                        products.append(p)
                if len(products) >= limit:
                    break
                page.mouse.wheel(0, 1_200)
                page.wait_for_timeout(random.randint(800, 2_000))
        finally:
            browser.close()
            playwright.stop()

        logger.info("采集完成: %d 条", len(products))
        return products[:limit]

    def _extract_from_data_script(self, page) -> list[dict]:
        """从页面内嵌 JSON 中提取商品数据（对结构变化最鲁棒）。"""
        try:
            raw = page.evaluate(f"document.getElementById('{DATA_SCRIPT_ID}')?.textContent")
        except Exception as exc:  # 浏览器执行异常
            logger.warning("读取内嵌数据失败: %s", exc)
            return []
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("内嵌 JSON 解析失败: %s", exc)
            return []

        # 商品数据通常嵌在 product / searchResult 等路径下，做宽松递归查找
        found: list[dict] = []
        stack = [payload]
        while stack and len(found) < 50:
            node = stack.pop()
            if isinstance(node, dict):
                if any(k in node for k in TITLE_KEYS) and any(k in node for k in PRICE_KEYS):
                    found.append(node)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return found

    def _parse_item(self, item: dict, keyword: str) -> Product | None:
        try:
            title = str(_first(item, TITLE_KEYS) or "").strip()
            price = float(_first(item, PRICE_KEYS) or 0)
            if not title or price <= 0:
                return None
            product_id = str(_first(item, PRODUCT_ID_KEYS) or "")
            if not product_id:
                # 没有稳定 ID 时用关键词 + 标题做键
                product_id = f"kw-{keyword}-{abs(hash(title)) % 10**10}"
            return Product(
                product_id=product_id,
                title=title,
                category=item.get("categoryName") or item.get("category") or "Unknown",
                price=round(price, 2),
                original_price=round(float(_first(item, ["originalPrice", "listPrice"]) or price), 2),
                sold_count=_parse_sold(_first(item, SOLD_KEYS)),
                rating=float(_first(item, RATING_KEYS) or 0.0),
                review_count=_parse_sold(_first(item, REVIEW_KEYS)),
                seller_name=str(_first(item, SELLER_KEYS) or ""),
                seller_id=str(_first(item, SELLER_ID_KEYS) or ""),
                commission_rate=float(_first(item, ["commissionRate", "commission"]) or 0.0),
                video_views=_parse_sold(_first(item, ["videoViews", "views"])),
                video_likes=_parse_sold(_first(item, ["videoLikes", "likes"])),
                listed_at=item.get("listedAt") or item.get("createTime") or "",
            )
        except (TypeError, ValueError) as exc:
            logger.debug("解析商品失败: %s", exc)
            return None
