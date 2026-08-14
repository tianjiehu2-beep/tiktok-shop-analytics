"""TikTok Shop ???????? Playwright??

???
- TikTok ?????X-Bogus/MsToken ??????????????????????
- ??????? shop.tiktok.com ??????????? DOM ??????
  ???/??/??/??/???/????????? JSON ????????
- ???????pip install playwright && playwright install chromium
- ????????????????????
"""

from __future__ import annotations

import logging
import random
import re
from urllib.parse import quote

from ..models import Product

logger = logging.getLogger(__name__)

# ????????TikTok Shop ?????????????
SEARCH_URL_TEMPLATE = "https://shop.tiktok.com/{region_lower}/s?q={keyword}"

# ?????????????? JS?????????? DOM ???????????????
_CARDS_JS = r"""() => {
  const out = [];
  const seen = new Set();
  const links = Array.from(document.querySelectorAll('a[href*="/pdp/"]'));
  for (const a of links) {
    const href = a.href || a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    seen.add(href);
    let card = a;
    for (let i = 0; i < 8; i++) {
      card = card.parentElement;
      if (!card) break;
      const t = card.innerText || '';
      if (t.includes('$') && card.querySelector('img')) break;
    }
    const titleEl = a.querySelector('h3') || a;
    const title = (titleEl.innerText || a.getAttribute('title') || '').trim();
    const imgEl = card ? card.querySelector('img') : null;

    let seller = '', sold = '', rating = '';
    if (card) {
      const leaves = Array.from(card.querySelectorAll('*')).filter(el => el.childElementCount === 0);
      for (const el of leaves) {
        if (el === titleEl || el === a) break;
        const t = (el.textContent || '').trim();
        if (!t) continue;
        if (/^[\d.,]+[KkMm]?\s*sold$/i.test(t)) continue;
        if (/^\d\.\d$/.test(t)) continue;
        if (/^(free\s*shipping|coupon|best\s*seller|low\s*stock|add\s*to\s*cart)$/i.test(t)) continue;
        if (t.length <= 60 && !/^US?\$/.test(t) && !/^-?\d/.test(t)) seller = t;
      }
      for (const el of leaves) {
        const t = (el.textContent || '').trim();
        if (!t) continue;
        if (!sold && /^[\d.,]+[KkMm]?\s*sold$/i.test(t)) sold = t;
        if (!rating && /^\d\.\d$/.test(t)) rating = t;
        if (sold && rating) break;
      }
    }
    const text = card ? (card.innerText || '') : '';
    const priceMatches = text.match(/(?<!-)(?:US)?\$\s*([\d,]+\.?\d*)/g) || [];
    const clean = (s) => s ? s.replace(/[^0-9.]/g, '') : '';
    const price = priceMatches.length ? clean(priceMatches[0]) : '';
    const original = priceMatches.length > 1 ? clean(priceMatches[priceMatches.length - 1]) : price;
    out.push({
      href: href,
      title: title,
      img: imgEl ? (imgEl.currentSrc || imgEl.src || '') : '',
      price: price,
      original: original,
      sold: sold,
      rating: rating,
      seller: seller
    });
  }
  return out;
}
"""

# ??????????????????/???
_BADGE_WORDS = {
    "free", "shipping", "coupon", "best", "seller", "low", "stock",
    "hot", "top", "sold", "out", "in", "new", "deal", "order",
    "save", "off", "sale", "today", "now", "only", "left", "add",
    "cart", "favorite", "ship", "24h", "24hrs",
}


def _parse_sold(value) -> int:
    """?? '2.3K' / '1.2M' / 1234 ???????"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([KkMm?]?)", text)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        num *= 1000
    elif unit == "m":
        num *= 1_000_000
    elif unit == "?":
        num *= 10_000
    return int(num)


def _region_path(region: str) -> str:
    """US -> us??? URL ???"""
    return (region or "US").strip().lower()[:2]


def _extract_seller(text: str, title: str) -> str:
    """?????????????????????/?????????"""
    if not title:
        return ""
    idx = text.find(title)
    if idx < 0:
        return ""
    before = text[:idx]
    tokens = [t.strip(" .??|,;:") for t in re.split(r"\s+", before) if t.strip()]
    picked: list[str] = []
    for t in reversed(tokens):
        key = t.lower().strip(".")
        if key in _BADGE_WORDS or re.fullmatch(r"[\d.,KkM%$???+-]+", key):
            break
        picked.append(t)
        if len(picked) >= 3:
            break
    return " ".join(reversed(picked))[:80]


class TikTokShopScraper:
    """?? Playwright ? TikTok Shop ??????"""

    def __init__(self, region: str = "US", headless: bool = True, slow_mo_ms: int = 800,
                 max_products_per_run: int = 100, proxy: str | None = None):
        self.region = region
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.max_products_per_run = max_products_per_run
        self.proxy = proxy

    def _browser(self):
        from playwright.sync_api import sync_playwright  # ?????demo ??????
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        context_kwargs = dict(
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        )
        if self.proxy:
            context_kwargs["proxy"] = {"server": self.proxy}
        context = browser.new_context(**context_kwargs)
        return playwright, browser, context

    def scrape_search(self, keyword: str, limit: int | None = None) -> list[Product]:
        limit = limit or self.max_products_per_run
        url = SEARCH_URL_TEMPLATE.format(region_lower=_region_path(self.region),
                                         keyword=quote(keyword))
        logger.info("????: %s", url)

        playwright, browser, context = self._browser()
        products: list[Product] = []
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector('a[href*="/pdp/"]', timeout=30_000)
            except Exception:
                logger.warning("??????????????????????")
            page.wait_for_timeout(2_000)

            seen: set[str] = set()
            stale = 0
            raw_total = 0
            skipped = 0
            for _ in range(40):
                try:
                    cards = page.evaluate(_CARDS_JS)
                except Exception as exc:
                    logger.warning("????????: %s", exc)
                    break
                raw_total = max(raw_total, len(cards))
                new_count = 0
                for card in cards:
                    product = self._parse_card(card, keyword)
                    if product is None:
                        skipped += 1
                        continue
                    if product.product_id not in seen:
                        seen.add(product.product_id)
                        products.append(product)
                        new_count += 1
                if len(products) >= limit:
                    break
                if new_count == 0:
                    stale += 1
                    if stale >= 5:
                        break
                else:
                    stale = 0
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(random.randint(900, 1_800))
        finally:
            browser.close()
            playwright.stop()

        logger.info("????: %d ?????? %d?????? %d?", len(products), raw_total, skipped)
        return products[:limit]

    def _parse_card(self, card: dict, keyword: str) -> Product | None:
        title = (card.get("title") or "").strip()
        if not title:
            return None

        try:
            price = float(card.get("price") or 0)
        except ValueError:
            price = 0.0
        if price <= 0:
            return None
        try:
            original = float(card.get("original") or price)
        except ValueError:
            original = price
        if original <= price:
            original = price

        sold = _parse_sold(card.get("sold"))
        try:
            rating = float(card.get("rating") or 0)
        except ValueError:
            rating = 0.0
        if rating > 5.0:
            rating = 0.0

        href = card.get("href") or ""
        id_m = re.search(r"/(\d{15,20})(?:[/?]|$)", href)
        product_id = id_m.group(1) if id_m else f"kw-{keyword}-{abs(hash(href)) % 10**10}"

        return Product(
            product_id=product_id,
            title=title,
            category="Search:" + keyword,
            price=round(price, 2),
            original_price=round(original, 2),
            sold_count=sold,
            rating=rating,
            review_count=0,
            seller_name=(card.get("seller") or "").strip()[:80],
            seller_id="",
            commission_rate=0.0,
            video_views=0,
            video_likes=0,
            listed_at="",
        )
