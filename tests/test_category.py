"""类目洞察单元测试：蓝海指数与类目聚合。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttshop.analysis.category import blue_ocean_score, category_insights


def product(pid, cat, sold, reviews=50, price=20.0):
    return {"product_id": pid, "category": cat, "sold_count": sold,
            "review_count": reviews, "price": price, "first_seen_at": "2026-01-01T00:00:00+00:00"}


def trend(pid, sold_7d=100, growth=1.0):
    return {"product_id": pid, "sold_7d": sold_7d, "growth_7d": growth}


class CategoryInsightTest(unittest.TestCase):
    def test_blue_ocean_score_direction(self):
        low = blue_ocean_score(growth_7d=0.5, cr4=0.8, avg_reviews=500, avg_sold_7d=10)
        high = blue_ocean_score(growth_7d=5.0, cr4=0.2, avg_reviews=5, avg_sold_7d=3000)
        self.assertGreater(high, low)
        self.assertGreaterEqual(high, 50)
        self.assertLess(low, 50)

    def test_category_insights_aggregation(self):
        # Toys：8 个商品销量均匀（CR4=0.5）、低评论、高增速 -> 机会类目
        products = [product(f"A{i}", "Toys", sold=100, reviews=5) for i in range(8)]
        products += [product("B1", "Beauty", sold=500, reviews=999),
                     product("B2", "Beauty", sold=100, reviews=888)]
        trends = [trend(f"A{i}", sold_7d=300, growth=3.0) for i in range(8)]
        trends += [trend("B1", sold_7d=50, growth=0.2), trend("B2", sold_7d=10, growth=0.1)]
        rows = category_insights(products, trends)
        self.assertEqual(len(rows), 2)
        toys = next(r for r in rows if r["category"] == "Toys")
        beauty = next(r for r in rows if r["category"] == "Beauty")
        self.assertEqual(toys["product_cnt"], 8)
        self.assertEqual(toys["total_sold"], 800)
        self.assertAlmostEqual(toys["cr4"], 0.5, places=3)
        self.assertAlmostEqual(toys["cr10"], 1.0, places=3)
        self.assertAlmostEqual(toys["avg_reviews"], 5.0, places=3)
        self.assertAlmostEqual(toys["avg_growth_7d"], 3.0, places=2)
        # 蓝海指数降序：Toys（高增速/低评论/分散）排在 Beauty 前面
        self.assertEqual(rows[0]["category"], "Toys")
        self.assertTrue(toys["is_opportunity"])
        self.assertFalse(beauty["is_opportunity"])


if __name__ == "__main__":
    unittest.main()
