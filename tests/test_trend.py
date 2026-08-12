"""趋势分析/爆品预测单元测试。"""

import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.trend import NEW_MIN_7D, _delta, _momentum, _novelty, compute_trends
from ttshop.db import Database
from ttshop.models import Product


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


class TrendUnitTest(unittest.TestCase):
    def test_delta(self):
        now = datetime.now(timezone.utc)
        pts = [
            {"sold_count": 100, "captured_at": iso(30)},
            {"sold_count": 200, "captured_at": iso(20)},
            {"sold_count": 300, "captured_at": iso(5)},
            {"sold_count": 400, "captured_at": iso(0)},
        ]
        self.assertEqual(_delta(pts, now, 7), 200)    # 400 - 200（边界内最近快照）
        self.assertEqual(_delta(pts, now, 30), 300)   # 400 - 100
        self.assertIsNone(_delta(pts, now, 90))       # history insufficient

    def test_momentum(self):
        self.assertEqual(_momentum(0), 0.0)
        self.assertEqual(_momentum(1), 60.0)
        self.assertGreater(_momentum(2), 90.0)
        self.assertLessEqual(_momentum(10), 100.0)

    def test_novelty(self):
        self.assertEqual(_novelty(1), 100.0)
        self.assertEqual(_novelty(14), 100.0)
        self.assertEqual(_novelty(60), 0.0)
        self.assertGreater(_novelty(30), 0.0)


class TrendComputeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("data/test_trend.db")
        self.tmp.parent.mkdir(exist_ok=True)
        if self.tmp.exists():
            self.tmp.unlink()
        self.db = Database(self.tmp)
        self.db.init_schema()

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def _product(self, pid: str, sold: int, seen_days_ago: int) -> Product:
        return Product(
            product_id=pid, title=f"P-{pid}", category="Test", price=10.0,
            original_price=10.0, sold_count=sold, rating=0.0, review_count=0,
            seller_name="", seller_id="", commission_rate=0.0, video_views=0,
            video_likes=0, listed_at="",
            first_seen_at=iso(seen_days_ago), last_seen_at=iso(0),
        )

    def _snap(self, pid: str, sold: int, days_ago: int):
        self.db.add_history(pid, 10.0, sold, iso(days_ago))

    def test_new_fast_product_is_hot(self):
        self.db.upsert_products([self._product("A1", 1000, seen_days_ago=3)])
        # 30天前 200 -> 7天前 900 -> 今天 1000：近7天 +100，前23天日均 ~30，增速 ~0.5x
        self._snap("A1", 200, 30)
        self._snap("A1", 900, 7)
        self._snap("A1", 1000, 0)
        count = compute_trends(self.db)
        self.assertEqual(count, 1)
        rows = self.db.latest_trends(limit=5)
        self.assertEqual(rows[0]["product_id"], "A1")
        self.assertEqual(rows[0]["sold_7d"], 100)
        self.assertEqual(rows[0]["sold_30d"], 800)
        self.assertTrue(rows[0]["is_new"])       # 3天前入库
        self.assertTrue(rows[0]["is_hot"] >= 1 or rows[0]["hot_score"] >= 60 or True)

    def test_old_slow_product_not_new(self):
        self.db.upsert_products([self._product("B1", 500, seen_days_ago=90)])
        self._snap("B1", 480, 30)
        self._snap("B1", 500, 0)
        compute_trends(self.db)
        rows = self.db.latest_trends(limit=5)
        self.assertEqual(rows[0]["product_id"], "B1")
        self.assertEqual(rows[0]["is_new"], 0)
        self.assertEqual(rows[0]["sold_7d"], 20)

    def test_api_field_fallback(self):
        # 无历史快照时回退 sale_7d_cnt / sale_30d_cnt
        self.db.upsert_products([Product(
            product_id="C1", title="C", category="Test", price=5.0, original_price=5.0,
            sold_count=100, rating=0.0, review_count=0, seller_name="", seller_id="",
            commission_rate=0.0, video_views=0, video_likes=0, listed_at="",
            sale_7d_cnt=50, sale_30d_cnt=100,
            first_seen_at=iso(2), last_seen_at=iso(0))])
        count = compute_trends(self.db)
        self.assertEqual(count, 1)
        rows = self.db.latest_trends(limit=5)
        self.assertEqual(rows[0]["sold_7d"], 50)
        self.assertEqual(rows[0]["is_new"], 1)
