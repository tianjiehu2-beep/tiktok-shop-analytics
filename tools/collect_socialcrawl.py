"""SocialCrawl real-data collection: SEA hot-category keywords -> DB.

Usage:
  python tools/collect_socialcrawl.py [--region TH] [--limit 30]

API key source: env TTSHOP_SOCIALCRAWL_KEY or data/socialcrawl_key.txt.
Each keyword search costs 1 free credit (new accounts get 100).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ttshop.analysis.scoring import run_analysis
from ttshop.config import Settings
from ttshop.db import Database
from ttshop.sources.api import ApiSource

# TikTok Shop SEA (TH) hot categories: (keyword, product category label)
DEFAULT_KEYWORDS = [
    ("skincare", "美妆个护-护肤"),
    ("makeup", "美妆个护-彩妆"),
    ("women dress", "服饰-女装"),
    ("men t-shirt", "服饰-男装"),
    ("phone case", "3C数码-手机配件"),
    ("home decor", "家居生活-家居装饰"),
    ("snack", "食品饮料-零食"),
    ("supplement", "健康保健-保健品"),
    ("pet supplies", "宠物用品"),
    ("yoga mat", "运动户外-健身器材"),
    ("jewelry", "时尚饰品-珠宝"),
    ("baby products", "母婴用品"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="SocialCrawl SEA hot-category collection")
    ap.add_argument("--keywords", default=None,
                    help="comma-separated keywords (default: 12 SEA hot categories)")
    ap.add_argument("--region", default="TH", help="TH / ID / MY / SG / VN / PH")
    ap.add_argument("--limit", type=int, default=30, help="max products per keyword")
    ap.add_argument("--db", default=None, help="sqlite path (default: data/tiktok_shop.db)")
    args = ap.parse_args()

    data_dir = Path("data")
    key = (os.environ.get("TTSHOP_SOCIALCRAWL_KEY")
           or (data_dir / "socialcrawl_key.txt").read_text(encoding="utf-8").strip())
    if not key:
        print("missing SocialCrawl API key (TTSHOP_SOCIALCRAWL_KEY or data/socialcrawl_key.txt)")
        return 1

    settings = Settings(region=args.region)
    db = Database(args.db or settings.db_path)
    db.init_schema()
    source = ApiSource(settings=settings, provider="socialcrawl",
                       api_key=key, region=args.region)

    pairs = DEFAULT_KEYWORDS if not args.keywords else [
        (k.strip(), "Unknown") for k in args.keywords.split(",") if k.strip()]
    total = 0
    failed = 0
    for kw, cat in pairs:
        try:
            result = source.fetch(keyword=kw, limit=args.limit)
            for product in result.products:
                product.category = cat
            written = db.upsert_products(result.products)
            total += len(result.products)
            print(f"[{kw} -> {cat}] collected {len(result.products)}, inserted {written}")
        except Exception as exc:  # noqa: BLE001 - one bad keyword must not abort the run
            failed += 1
            print(f"[{kw}] FAILED: {exc}")
    print(f"done: {total} products collected, {failed} keywords failed")

    count = run_analysis(db, settings)
    print(f"analysis: {count} products scored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
