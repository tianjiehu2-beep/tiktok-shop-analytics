"""多源故障切换单元测试。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.config import Settings
from ttshop.db import Database
from ttshop.models import Product
from ttshop.sources import FailoverSource, get_source
from ttshop.sources.base import DataSource, SourceResult


def make_product(pid: str):
    return Product(
        product_id=pid, title=f"P-{pid}", category="Toys & Hobbies", price=20.0,
        original_price=25.0, sold_count=100, rating=4.3, review_count=50,
        seller_name="Shop A", seller_id="S1", commission_rate=0.1,
        video_views=1000, video_likes=50, listed_at="2026-01-01",
        first_seen_at="2026-01-01T00:00:00+00:00", last_seen_at="2026-01-02T00:00:00+00:00",
    )


class BoomSource(DataSource):
    """总是抛错的模拟数据源。"""

    def __init__(self, name: str, exc: Exception = RuntimeError("boom")):
        self._name = name
        self._exc = exc

    def fetch(self, keyword=None, limit=None, category=None) -> SourceResult:
        raise self._exc


class OkSource(DataSource):
    """总是成功返回的模拟数据源。"""

    def __init__(self, name: str, n: int = 3):
        self._name = name
        self._n = n

    def fetch(self, keyword=None, limit=None, category=None) -> SourceResult:
        return SourceResult(products=[make_product(f"{self._name}{i}") for i in range(self._n)])


class EmptySource(DataSource):
    """返回空数据的模拟数据源。"""

    def fetch(self, keyword=None, limit=None, category=None) -> SourceResult:
        return SourceResult(products=[])


class FailoverSourceTest(unittest.TestCase):
    def test_picks_first_working_source(self):
        fs = FailoverSource([("bad", BoomSource("bad")), ("good", OkSource("good"))])
        result = fs.fetch(limit=3)
        self.assertEqual(len(result.products), 3)
        self.assertEqual(fs.used_source, "good")

    def test_empty_result_counts_as_failure(self):
        fs = FailoverSource([("empty", EmptySource()), ("good", OkSource("good"))])
        result = fs.fetch()
        self.assertEqual(fs.used_source, "good")
        self.assertTrue(result.products)

    def test_all_fail_raises(self):
        fs = FailoverSource([("bad1", BoomSource("bad1")), ("bad2", BoomSource("bad2"))])
        with self.assertRaises(RuntimeError):
            fs.fetch()

    def test_get_source_auto_falls_back_to_demo(self):
        source = get_source("auto", Settings(db_path="data/tmp_failover_test.db"))
        self.assertIsInstance(source, FailoverSource)
        result = source.fetch(limit=3)
        self.assertEqual(source.used_source, "demo")
        self.assertTrue(result.products)


if __name__ == "__main__":
    unittest.main()
