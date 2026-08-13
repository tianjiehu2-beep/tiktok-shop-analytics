"""店铺监控（卖家聚合/上新告警）与直播带货榜单元测试。"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.shop import AUTO_WATCH_TOP, compute_shop_alerts, ensure_shop_watch
from ttshop.db import Database
from ttshop.demo_data import generate_live_sessions, generate_products
from ttshop.models import Product


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def make_product(pid: str, seller: str = "S1", sold: int = 100, seen: int = 30, gmv: float = 0.0):
    return Product(
        product_id=pid, title=f"P-{pid}", category="Toys & Hobbies", price=20.0,
        original_price=25.0, sold_count=sold, rating=4.3, review_count=50,
        seller_name=f"Shop {seller}", seller_id=seller, commission_rate=0.1,
        video_views=1000, video_likes=50, listed_at=iso(seen)[:10],
        gmv_total=gmv, first_seen_at=iso(seen), last_seen_at=iso(0),
    )


class ShopUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("data/test_shop.db")
        self.tmp.parent.mkdir(exist_ok=True)
        if self.tmp.exists():
            self.tmp.unlink()
        self.db = Database(self.tmp)
        self.db.init_schema()

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def test_sync_sellers_aggregates(self):
        self.db.upsert_products([
            make_product("A1", seller="S1", sold=100, gmv=5000),
            make_product("A2", seller="S1", sold=50, gmv=2000),
            make_product("B1", seller="S2", sold=30),
        ])
        n = self.db.sync_sellers()
        self.assertEqual(n, 2)
        s1 = next(s for s in self.db.top_sellers() if s["seller_id"] == "S1")
        self.assertEqual(s1["product_cnt"], 2)
        self.assertEqual(s1["total_sold"], 150)
        self.assertAlmostEqual(s1["total_gmv"], 7000.0, delta=0.01)

    def test_auto_watch_top_sellers(self):
        self.db.upsert_products([
            make_product(f"P{i}", seller=f"S{i}", sold=(i + 1) * 100) for i in range(5)
        ])
        self.db.sync_sellers()
        watched = ensure_shop_watch(self.db)
        self.assertGreaterEqual(len(watched), AUTO_WATCH_TOP)

    def test_shop_new_listing_alert(self):
        self.db.upsert_products([
            make_product("OLD", seller="S1", sold=1000, seen=30),
            make_product("NEW", seller="S1", sold=10, seen=0),
        ])
        self.db.sync_sellers()
        self.db.add_shop_watch("S1")
        sellers, alerts = compute_shop_alerts(self.db, days=7)
        self.assertGreater(sellers, 0)
        rows = self.db.shop_new_listings(days=7)
        self.assertTrue(any(r["product_id"] == "NEW" for r in rows))
        self.assertFalse(any(r["product_id"] == "OLD" for r in rows))
        self.assertGreater(alerts, 0)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        types = [a["alert_type"] for a in self.db.alerts_by_date(today)]
        self.assertIn("shop_new", types)

    def test_live_sessions_roundtrip(self):
        products = generate_products(count=20, seed=3)
        sessions = generate_live_sessions(products, count=10, seed=5)
        self.assertEqual(len(sessions), 10)
        written = self.db.upsert_live_sessions(sessions)
        self.assertEqual(written, 10)
        top = self.db.top_live_sessions(limit=5)
        self.assertLessEqual(len(top), 5)
        self.assertTrue(all(s["gmv_amt"] > 0 for s in top))
        # 排序按 GMV 降序
        gmv = [s["gmv_amt"] for s in top]
        self.assertEqual(gmv, sorted(gmv, reverse=True))


if __name__ == "__main__":
    unittest.main()
