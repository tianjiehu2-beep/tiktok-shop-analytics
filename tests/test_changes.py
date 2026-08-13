"""今日变动单元测试：新上架 / 价格异动 / 销量激增。"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.changes import new_products, price_moves, surge_products
from ttshop.analysis.trend import compute_trends
from ttshop.db import Database
from ttshop.models import Product


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def make_product(pid: str, seen: int = 30, sold: int = 100):
    return Product(
        product_id=pid, title=f"P-{pid}", category="Toys & Hobbies", price=20.0,
        original_price=25.0, sold_count=sold, rating=4.3, review_count=50,
        seller_name="Shop A", seller_id="S1", commission_rate=0.1,
        video_views=1000, video_likes=50, listed_at=iso(seen)[:10],
        first_seen_at=iso(seen), last_seen_at=iso(0),
    )


class ChangesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("data/test_changes.db")
        self.tmp.parent.mkdir(exist_ok=True)
        if self.tmp.exists():
            self.tmp.unlink()
        self.db = Database(self.tmp)
        self.db.init_schema()

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def test_new_products_filters_by_first_seen(self):
        self.db.upsert_products([
            make_product("NEW", seen=0, sold=50),
            make_product("OLD", seen=10, sold=999),
        ])
        rows = new_products(self.db, days=1)
        self.assertEqual([r["product_id"] for r in rows], ["NEW"])
        rows7 = new_products(self.db, days=30)
        self.assertEqual(len(rows7), 2)

    def test_price_moves_from_snapshots(self):
        p = make_product("P1", seen=3)
        p.price = 10.0
        self.db.upsert_products([p])   # 第一次运行快照：$10
        p.price = 9.0
        self.db.upsert_products([p])   # 第二次运行快照：$9
        moves = price_moves(self.db, limit=10)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["product_id"], "P1")
        self.assertAlmostEqual(moves[0]["pct"], -10.0, places=2)
        self.assertEqual(moves[0]["title"], "P-P1")

    def test_surge_products_from_trends(self):
        self.db.upsert_products([make_product("S1", seen=31, sold=2000)])
        # 三段快照：31天前 100 -> 9天前 300 -> 今天 2000（近7天 1700，增速约 27x）
        self.db.add_history("S1", 20.0, 100, iso(31))
        self.db.add_history("S1", 20.0, 300, iso(9))
        self.db.add_history("S1", 20.0, 2000, iso(0))
        compute_trends(self.db)
        rows = surge_products(self.db)
        self.assertTrue(any(r["product_id"] == "S1" for r in rows))
        s1 = next(r for r in rows if r["product_id"] == "S1")
        self.assertGreaterEqual(s1["growth_7d"], 1.5)
        self.assertGreaterEqual(s1["sold_7d"], 100)


if __name__ == "__main__":
    unittest.main()
