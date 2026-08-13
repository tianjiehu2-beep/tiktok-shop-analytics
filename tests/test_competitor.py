"""竞品监控单元测试。"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.competitor import (
    AUTO_WATCH_TOP, _l1, _price_change_pct, compute_competitors, find_competitors)
from ttshop.db import Database
from ttshop.models import Product


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def make_product(pid: str, category="Toys & Hobbies", price=20.0, sold=1000, seen=60):
    return Product(
        product_id=pid, title=f"P-{pid}", category=category, price=price,
        original_price=price, sold_count=sold, rating=4.3, review_count=50,
        seller_name="", seller_id="", commission_rate=0.1, video_views=0,
        video_likes=0, listed_at="", first_seen_at=iso(seen), last_seen_at=iso(0),
    )


class CompetitorUnitTest(unittest.TestCase):
    def test_l1_split(self):
        self.assertEqual(_l1("Toys & Hobbies > Building Blocks"), "Toys & Hobbies")
        self.assertEqual(_l1(""), "")

    def test_price_change(self):
        self.assertEqual(_price_change_pct([{"price": 10.0}, {"price": 9.0}]), -10.0)
        self.assertEqual(_price_change_pct([{"price": 10.0}]), 0.0)
        self.assertEqual(_price_change_pct([]), 0.0)

    def setUp(self):
        self.tmp = Path("data/test_comp.db")
        self.tmp.parent.mkdir(exist_ok=True)
        if self.tmp.exists():
            self.tmp.unlink()
        self.db = Database(self.tmp)
        self.db.init_schema()

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def test_find_competitors_filters(self):
        watched = make_product("W1", price=20.0)
        self.db.upsert_products([
            watched,
            make_product("C1", price=22.0, sold=2000),      # 同类目同价带 -> 竞品
            make_product("D1", category="Sports & Outdoors", price=21.0),  # 不同类目
            make_product("E1", price=60.0),                 # 价格超价带
        ])
        products = self.db.products()
        series = self.db.snapshot_series(days=60)
        comps = find_competitors(self.db, watched.to_dict(), products, series)
        ids = [c["competitor_id"] for c in comps]
        self.assertIn("C1", ids)
        self.assertNotIn("D1", ids)
        self.assertNotIn("E1", ids)

    def test_auto_watch_when_empty(self):
        self.db.upsert_products([make_product(f"T{i}", sold=(i + 1) * 100) for i in range(6)])
        count, alerts = compute_competitors(self.db)
        self.assertGreaterEqual(len(self.db.watch_list()), AUTO_WATCH_TOP)
        self.assertGreater(count, 0)

    def test_competitor_price_drop_alert(self):
        self.db.upsert_products([
            make_product("W1", price=20.0, sold=1000),
            make_product("C1", price=22.0, sold=2000),
        ])
        self.db.add_watch("W1")
        # C1 近两天降价 10%：22 -> 19.8
        self.db.add_history("C1", 22.0, 100, iso(2))
        self.db.add_history("C1", 19.8, 110, iso(0))
        count, alerts = compute_competitors(self.db)
        self.assertGreater(count, 0)
        rows = self.db.latest_competitors(limit=10)
        self.assertEqual(rows[0]["competitor_id"], "C1")
        self.assertLessEqual(rows[0]["price_change_pct"], -5)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        alert_rows = self.db.alerts_by_date(today)
        types = [a["alert_type"] for a in alert_rows]
        self.assertIn("comp_price_drop", types)
