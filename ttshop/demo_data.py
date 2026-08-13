"""美区 TikTok Shop 模拟数据生成器（本地演示完整数据管道，无需联网）。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .models import LiveSession, Product

CATEGORY_PRICE_RANGE = {
    "Beauty & Personal Care": (6.0, 45.0),
    "Electronics & Accessories": (9.0, 70.0),
    "Home & Living": (7.0, 55.0),
    "Apparel & Accessories": (5.0, 38.0),
    "Sports & Outdoors": (6.0, 50.0),
    "Toys & Hobbies": (5.0, 32.0),
}

NOUNS = {
    "Beauty & Personal Care": [
        "Lip Gloss Set", "Hair Curler", "Facial Roller", "Makeup Brush Set", "Vitamin C Serum",
        "Nail Polish Kit", "Lash Curler", "Face Mist", "Skincare Kit", "Spa Headband",
    ],
    "Electronics & Accessories": [
        "Wireless Earbuds", "Phone Stand", "LED Strip Lights", "Smart Watch", "Portable Charger",
        "Bluetooth Speaker", "Laptop Sleeve", "Car Phone Mount", "Cable Organizer", "Mini Projector",
    ],
    "Home & Living": [
        "Kitchen Organizer", "Scented Candle Set", "Storage Bins", "Silicone Mat", "Vacuum Storage Bag",
        "Throw Pillow", "Insulated Coffee Mug", "Desk Lamp", "Bamboo Cutting Board", "Bath Mat",
    ],
    "Apparel & Accessories": [
        "Oversized Hoodie", "Sweat Set", "Bucket Hat", "Polarized Sunglasses", "Faux Leather Belt",
        "Silk Scarf", "Socks Pack", "Yoga Leggings", "Denim Jacket", "Crossbody Bag",
    ],
    "Sports & Outdoors": [
        "Resistance Bands", "Non-Slip Yoga Mat", "Insulated Water Bottle", "Jump Rope", "Hiking Poles",
        "Adjustable Dumbbells", "Foam Roller", "Sports Cap", "Gym Gloves", "Pilates Ring",
    ],
    "Toys & Hobbies": [
        "Building Blocks", "Plush Stuffed Animal", "Fidget Toy Set", "RC Car", "Puzzle Set",
        "Kids Art Kit", "Family Board Game", "Magnetic Tiles", "Dinosaur Toy", "Card Game",
    ],
}

ADJECTIVES = [
    "Portable", "Premium", "Viral", "Ultra-Soft", "Multi-Functional", "Trendy", "Adjustable",
    "Lightweight", "Upgraded", "Waterproof",
]

MODIFIERS = [
    "for Women", "for Men", "Gift Idea", "with Storage Bag", "Home Essentials", "Must-Have",
    "Bundle of 3", "for Travel", "for Office", "for Kids",
]

SELLER_PREFIX = ["TikTop", "Bloom", "Nova", "Luxe", "Urban", "Sunny", "Zest", "Coco", "Aura", "Hype"]
SELLER_SUFFIX = ["Deals", "Store", "Picks", "Supply", "Market", "Boutique", "Hub", "Co", "Goods", "Studio"]

# 头部店铺：demo 中少数店铺拥有多商品并持续上新，用于店铺监控演示
STAR_SELLER_IDS = [f"US88{i:06d}" for i in range(8)]
STAR_SELLER_NAMES = [
    "StarShop Deals", "TrendNest Store", "MegaPick Supply", "ViralDeal Market",
    "TopSell Boutique", "HotBox Goods", "RapidShip Hub", "PrimeFind Co",
]


def _pick(rng: random.Random, seq: list[str]) -> str:
    return seq[rng.randrange(len(seq))]


def _price(rng: random.Random, lo: float, hi: float) -> float:
    base = rng.uniform(lo, hi)
    return round(int(base) + 0.99, 2) if base >= 2 else round(base, 2)


def _sold_count(rng: random.Random) -> int:
    return max(5, int(rng.lognormvariate(6.4, 1.5)))


def _rating(rng: random.Random) -> float:
    return round(min(5.0, max(3.7, rng.gauss(4.4, 0.32))), 1)


def generate_products(count: int = 200, category: str | None = None, seed: int = 42) -> list[Product]:
    """生成模拟商品。seed 固定时结果可复现，便于重复演示增量采集。"""
    rng = random.Random(seed)
    categories = [category] if category else list(CATEGORY_PRICE_RANGE)
    products: list[Product] = []
    now = datetime.now(timezone.utc)
    for i in range(count):
        cat = _pick(rng, categories)
        lo, hi = CATEGORY_PRICE_RANGE[cat]
        price = _price(rng, lo, hi)
        original_price = round(price * rng.uniform(1.15, 1.6), 2)
        sold = _sold_count(rng)
        reviews = max(0, int((sold / rng.uniform(35, 90)) * rng.uniform(0.5, 1.5)))
        views = max(sold, int(sold * rng.uniform(3, 25)))
        likes = int(views * rng.uniform(0.01, 0.06))
        listed_days = rng.randint(0, 7) if rng.random() < 0.2 else rng.randint(1, 365)
        if i < len(STAR_SELLER_IDS):
            listed_days = rng.randint(0, 6)   # 头部店铺持续上新
        listed_at = (now - timedelta(days=listed_days)).date().isoformat()
        if i < len(STAR_SELLER_IDS) or rng.random() < 0.3:
            star_idx = i % len(STAR_SELLER_IDS)
            seller = STAR_SELLER_NAMES[star_idx]
            seller_id = STAR_SELLER_IDS[star_idx]
        else:
            seller = f"{_pick(rng, SELLER_PREFIX)} {_pick(rng, SELLER_SUFFIX)}"
            seller_id = f"US{rng.randrange(10 ** 8):08d}"
        product = Product(
            product_id=f"TT{rng.randrange(10**11):011d}",
            title=f"{_pick(rng, ADJECTIVES)} {_pick(rng, NOUNS[cat])} {_pick(rng, MODIFIERS)}",
            category=cat,
            price=price,
            original_price=original_price,
            sold_count=sold,
            rating=_rating(rng),
            review_count=reviews,
            seller_name=seller,
            seller_id=seller_id,
            commission_rate=round(rng.uniform(0.05, 0.25), 3),
            video_views=views,
            video_likes=likes,
            listed_at=listed_at,
            first_seen_at=(now - timedelta(days=listed_days)).isoformat(),
        )
        products.append(product)
    return products


def generate_history(products: list[Product], days: int = 14, points: int = 6, seed: int = 7) -> list[tuple]:
    """为商品生成历史快照（过去 N 天销量逐步增长），用于趋势图演示。"""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    history = []
    for p in products:
        trend = rng.choice(("up", "flat", "down"))
        for k in range(points):
            t = now - timedelta(days=days * (points - k) / points)
            base = (k + 1) / points
            if trend == "up":
                ratio = base * rng.uniform(0.85, 1.0)
            elif trend == "flat":
                ratio = 0.97 + 0.03 * base + rng.uniform(-0.01, 0.01)
            else:  # down：从 1.25 倍回落到 1.0 倍
                ratio = (1.25 - 0.25 * base) * rng.uniform(0.97, 1.03)
            if k == points - 1:
                ratio = 1.0
            sold = max(0, int(p.sold_count * ratio))
            price = p.price * rng.uniform(0.97, 1.03)
            history.append((p.product_id, round(price, 2), sold, t.replace(microsecond=0).isoformat()))
    return history

LIVE_TITLES = [
    "Weekly Top Picks Live", "Flash Sale Livestream", "New Arrivals Showcase",
    "Best Sellers Countdown", "Late Night Deals Live", "Staff Picks Livestream",
    "Bundle Bonanza Live", "Trending Now Live", "Hot Picks Express",
    "Clearance Steals Live",
]


def generate_live_sessions(products: list[Product], count: int = 24, seed: int = 11) -> list[LiveSession]:
    """为现有商品生成模拟直播带货场次（近 3 天，GMV/销量/峰值观看/时长）。"""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    products = [p if isinstance(p, dict) else p.to_dict() for p in products]
    sessions: list[LiveSession] = []
    for _ in range(count):
        p = products[rng.randrange(len(products))]
        hours_ago = rng.randint(1, 72)
        price = p.get("price") or 10.0
        gmv = round(price * rng.uniform(300, 6000), 2)
        sold = max(10, int(gmv / price * rng.uniform(0.6, 1.4)))
        sessions.append(LiveSession(
            session_id=f"LIVE{rng.randrange(10 ** 10):010d}",
            seller_name=p.get("seller_name") or "",
            seller_id=p.get("seller_id") or "",
            product_id=p.get("product_id") or "",
            product_title=p.get("title") or "",
            category=p.get("category") or "",
            live_title=_pick(rng, LIVE_TITLES),
            gmv_amt=gmv,
            sold_cnt=sold,
            viewers_peak=rng.randint(800, 60000),
            duration_min=rng.randint(30, 240),
            live_at=(now - timedelta(hours=hours_ago)).replace(microsecond=0).isoformat(),
        ))
    return sessions
