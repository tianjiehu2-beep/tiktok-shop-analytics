"""监控告警/异动检测单元测试。"""

import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.alerts import compute_alerts, export_markdown
from ttshop.analysis.trend import compute_trends
from ttshop.db import Database
from ttshop.models import Product


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def make_product(pid: str, price: float = 10.0, sold: int = 100, title: str = "Test Product") -> Product:
    return Product(
        product_id=pid, title=title, category="Test", price=price, original_price=price,
        sold_count=sold, rating=0.0, review_count=0, seller_name="", seller_id="",
        commission_rate=0.0, video_views=0, video_likes=0, listed_at="",
        first_seen_at=iso(2), last_seen_at=iso(0),
    )


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("data/test_alerts.db")
        self.tmp.parent.mkdir(exist_ok=True)
        if self.tmp.exists():
            self.tmp.unlink()
        self.db = Database(self.tmp)
        self.db.init_schema()

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def _snap(self, pid: str, price: float, sold: int, days_ago: int):
        self.db.add_history(pid, price, sold, iso(days_ago))

    def test_price_drop(self):
        # upsert 写入当前快照(17@now)，add_history 写入 2 天前快照(20@2d)
        self.db.upsert_products([make_product("P1", price=17.0)])
        self._snap("P1", 20.0, 100, 2)
        self.assertEqual(compute_alerts(self.db, min_surge=10**9), 1)
        alerts = self.db.alerts_by_date()
        types = {a["alert_type"] for a in alerts}
        self.assertIn("price_drop", types)
        msg = next(a["message"] for a in alerts if a["alert_type"] == "price_drop")
        self.assertIn("17.00", msg)

    def test_price_drop_below_threshold(self):
        self.db.upsert_products([make_product("P1", price=19.5)])
        self._snap("P1", 20.0, 100, 2)
        self.assertEqual(compute_alerts(self.db, min_surge=10**9), 0)
        alerts = self.db.alerts_by_date()
        self.assertFalse(any(a["alert_type"] == "price_drop" for a in alerts))

    def test_surge_and_new_hot(self):
        self.db.upsert_products([make_product("P2", price=10.0, sold=300)])
        self._snap("P2", 10.0, 50, 30)
        self._snap("P2", 10.0, 60, 7)
        compute_trends(self.db)
        new_count = compute_alerts(self.db, min_surge=100, growth_threshold=1.5)
        self.assertGreaterEqual(new_count, 2)
        alerts = self.db.alerts_by_date()
        types = {a["alert_type"] for a in alerts}
        self.assertIn("surge", types)
        self.assertIn("new_hot", types)      # 2天前入库 + 爆品指数高

    def test_dedupe(self):
        self.db.upsert_products([make_product("P1", price=17.0)])
        self._snap("P1", 20.0, 100, 2)
        first = compute_alerts(self.db, min_surge=10**9)
        second = compute_alerts(self.db, min_surge=10**9)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)           # 同一天不重复
        stored = self.db.alerts_by_date()
        self.assertEqual(len(stored), 1)

    def test_export_markdown(self):
        self.db.upsert_products([make_product("P1", price=17.0)])
        self._snap("P1", 20.0, 100, 2)
        compute_alerts(self.db, min_surge=10**9)
        alerts = self.db.alerts_by_date()
        out = Path("data/test_alerts_md")
        path = export_markdown(alerts, out)
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("异动", text)
        shutil.rmtree(out, ignore_errors=True)
