"""类目洞察：类目规模 / 近7天销量 / 增速 / 集中度 / 竞争度 / 蓝海指数。

- 集中度 CR4/CR10：类目头部商品销量占比，越高代表头部越垄断。
- 蓝海指数 0-100：需求（7天增速）+ 分散（低集中度）+ 进入门槛（低平均评论）+ 市场量级，
  >= 60 标记为机会类目。
"""

from __future__ import annotations

import math
from collections import defaultdict

BLUE_OCEAN_THRESHOLD = 50.0  # 蓝海指数 >= 该值视为机会类目


def _log_norm(value: float, cap: float = 5000.0) -> float:
    return math.log1p(max(0.0, value)) / math.log1p(cap)


def blue_ocean_score(growth_7d: float, cr4: float, avg_reviews: float, avg_sold_7d: float) -> float:
    """需求（增速）× 分散（1-CR4）× 进入门槛（评论稀疏）× 市场量级。"""
    demand = min(100.0, 40 * math.log1p(max(0.0, growth_7d)))
    spread = max(0.0, (0.8 - cr4) / 0.6) * 100.0
    entry = max(0.0, 100.0 - 20 * math.log2(max(1.0, avg_reviews)))
    scale = min(100.0, 100.0 * _log_norm(avg_sold_7d))
    return round(0.30 * demand + 0.30 * spread + 0.20 * entry + 0.20 * scale, 1)


def category_insights(products: list[dict], trends: list[dict]) -> list[dict]:
    """按类目聚合商品与趋势数据，输出类目洞察（按蓝海指数降序）。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        groups[p["category"] or "未分类"].append(p)
    trend_map = {t["product_id"]: t for t in trends}
    rows: list[dict] = []
    for cat, items in groups.items():
        n = len(items)
        total_sold = sum(int(p.get("sold_count") or 0) for p in items)
        sold_7d = [int((trend_map.get(p["product_id"]) or {}).get("sold_7d") or 0) for p in items]
        growth = [float((trend_map.get(p["product_id"]) or {}).get("growth_7d") or 0) for p in items]
        reviews = [int(p.get("review_count") or 0) for p in items]
        prices = [float(p.get("price") or 0) for p in items if p.get("price")]
        top_sold = sorted((int(p.get("sold_count") or 0) for p in items), reverse=True)
        cr4 = sum(top_sold[:4]) / total_sold if total_sold else 0.0
        cr10 = sum(top_sold[:10]) / total_sold if total_sold else 0.0
        avg_sold_7d = sum(sold_7d) / n
        avg_growth = sum(growth) / n
        avg_reviews = sum(reviews) / n
        avg_price = sum(prices) / len(prices) if prices else 0.0
        score = blue_ocean_score(avg_growth, cr4, avg_reviews, avg_sold_7d)
        rows.append({
            "category": cat,
            "product_cnt": n,
            "total_sold": total_sold,
            "avg_price": round(avg_price, 2),
            "avg_sold_7d": round(avg_sold_7d, 1),
            "avg_growth_7d": round(avg_growth, 2),
            "avg_reviews": round(avg_reviews, 1),
            "cr4": round(cr4, 3),
            "cr10": round(cr10, 3),
            "blue_ocean": score,
            "is_opportunity": score >= BLUE_OCEAN_THRESHOLD,
        })
    rows.sort(key=lambda r: r["blue_ocean"], reverse=True)
    return rows
