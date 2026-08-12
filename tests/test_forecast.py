"""销量预测/生命周期/推荐理由单元测试。"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.forecast import classify_lifecycle, compute_forecasts
from ttshop.db import Database
from ttshop.models import Product


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


class LifecycleUnitTest(unittest.TestCase):
    def test_decline(self):
        self.assertEqual(classify_lifecycle(growth_7d=0.4, rel_slope=-0.1, age_days=80), "衰退期")
        self.assertEqual(classify_lifecycle(growth_7d=0.0, rel_slope=-0.3, age_days=80), "衰退期")

    def test_introduction(self):
        self.assertEqual(classify_lifecycle(growth_7d=0.0, rel_slope=0.0, age_days=5), "导入期")
        self.assertEqual(classify_lifecycle(growth_7d=1.0, rel_slope=0.9, age_days=2), "导入期")

    def test_growth(self):
        self.assertEqual(classify_lifecycle(growth_7d=2.0, rel_slope=0.2, age_days=40), "成长期")
        self.assertEqual(classify_lifecycle(growth_7d=1.0, rel_slope=0.2, age_days=40), "成长期")

    def test_mature(self):
        self.assertEqual(classify_lifecycle(growth_7d=1.0, rel_slope=0.01, age_days=90), "成熟期")


class ForecastComputeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("data/test_forecast.db")
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

    def test_growing_product_forecast(self):
        # 每天累计销量 +100：30 天前 1000 -> 今天 4000，应判为成长期且有正向预测
        self.db.upsert_products([self._product("A1", 4000, seen_days_ago=30)])
        for i in range(30, -1, -1):
            self._snap("A1", 1000 + (30 - i) * 100, i)
        count = compute_forecasts(self.db)
        self.assertEqual(count, 1)
        rows = self.db.latest_forecasts(limit=5)
        self.assertEqual(rows[0]["product_id"], "A1")
        self.assertEqual(rows[0]["lifecycle"], "成长期")
        self.assertGreater(rows[0]["predicted_7d"], 0)
        self.assertIn("预测", rows[0]["reason"])
        self.assertIn("成长期", rows[0]["reason"])

    def test_new_product_is_introduction(self):
        self.db.upsert_products([self._product("B1", 10, seen_days_ago=2)])
        self._snap("B1", 5, 2)
        self._snap("B1", 10, 0)
        compute_forecasts(self.db)
        rows = self.db.latest_forecasts(limit=5)
        self.assertEqual(rows[0]["product_id"], "B1")
        self.assertEqual(rows[0]["lifecycle"], "导入期")

    def test_declining_product(self):
        self.db.upsert_products([self._product("C1", 880, seen_days_ago=90)])
        for i in range(30, -1, -2):
            self._snap("C1", 1000 - (30 - i) * 4, i)
        compute_forecasts(self.db)
        rows = self.db.latest_forecasts(limit=5)
        self.assertEqual(rows[0]["product_id"], "C1")
        self.assertEqual(rows[0]["lifecycle"], "衰退期")
