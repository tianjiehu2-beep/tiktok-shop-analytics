"""选品评分：需求度 × 竞争度 × 毛利。

- 需求度：销量越高、带货视频播放越高，得分越高（对数归一，避免爆款碾压）。
- 竞争度：评论数越少代表竞争越低（新品机会），高评论成熟品按对数衰减。
- 利润分：毛利率 30% 满分、10% 为及格线。
"""

from __future__ import annotations

import math

from ..db import Database
from ..models import utc_now
from .profit import estimate_profit

REVIEW_THRESHOLD = 50  # 评论数低于该值视为低竞争新品


def _log_norm(value: float, cap: float = 1_000_000) -> float:
    return math.log1p(max(0.0, value)) / math.log1p(cap)


def demand_score(p: dict) -> float:
    """需求度：总销量 + 近7天销量 + 视频播放 + 带货达人/视频规模 + 达人带货效率。"""
    sold = _log_norm(p.get("sold_count") or 0)
    sale_7d = _log_norm(p.get("sale_7d_cnt") or 0, cap=100_000)
    views = _log_norm(p.get("video_views") or 0)
    influencers = _log_norm(p.get("influencer_cnt") or 0, cap=100_000)
    videos = _log_norm(p.get("video_cnt") or 0, cap=100_000)
    per_influencer_gmv = 0.0
    if (p.get("gmv_total") or 0) > 0 and (p.get("influencer_cnt") or 0) > 0:
        per_influencer_gmv = _log_norm(
            p["gmv_total"] / max(1, p.get("influencer_cnt") or 1), cap=100_000)
    score = (40 * sold + 18 * sale_7d + 12 * views + 12 * influencers
             + 8 * videos + 10 * per_influencer_gmv) * 100
    return round(min(100.0, score), 1)


def competition_score(p: dict) -> float:
    reviews = p["review_count"] or 0
    if reviews <= REVIEW_THRESHOLD:
        return 100.0
    return round(max(10.0, 100 - 20 * math.log2(reviews / REVIEW_THRESHOLD)), 1)


def profit_score(profit: float, margin: float) -> float:
    base = max(0.0, (margin - 0.10) / 0.20) * 100
    return round(min(100.0, base), 1)


def run_analysis(db: Database, settings) -> int:
    """对当前全部活跃商品计算选品评分，写入分析快照。返回处理条数。"""
    products = db.products()
    now = utc_now()
    count = 0
    with db.conn() as conn:
        for p in products:
            estimate = estimate_profit(p["price"] or 0.0, settings)
            d = demand_score(p)
            c = competition_score(p)
            pr = profit_score(estimate.profit, estimate.margin)
            s = settings.weight_demand * d + settings.weight_competition * c + settings.weight_profit * pr
            conn.execute(
                """INSERT INTO analysis_snapshots
                   (product_id, analyzed_at, demand_score, competition_score, profit_score,
                    selection_score, est_profit, est_margin)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (p["product_id"], now, d, c, pr, round(s, 1),
                 estimate.profit, estimate.margin),
            )
            count += 1
    return count
