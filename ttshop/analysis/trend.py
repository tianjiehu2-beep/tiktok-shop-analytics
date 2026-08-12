"""趋势分析与爆品预测：把销量快照变成时间序列信号。

对每个活跃商品，用 price_snapshots 计算：
- sold_7d / sold_30d：近 7 天 / 30 天销量增量（快照优先，缺历史时回退 API 字段）
- growth_7d：近 7 天日均销量相对前 23 天日均的倍数（>1 表示在加速）
- is_new：首次入库 14 天以内且近 7 天有起量 -> 新品
- velocity_score / momentum_score / novelty_score -> hot_score（爆品指数 0-100）
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from ..db import Database

NEW_DAYS = 14          # 首次入库多少天内算新品窗口
NEW_MIN_7D = 50        # 新品至少需要达到的近7天销量
VELOCITY_CAP = 10_000  # 7天销量对数归一化的上限


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _delta(pts: list[dict], now: datetime, days: int) -> int | None:
    """最新销量 - (now-days) 时刻的销量；历史不足时返回 None。"""
    if not pts:
        return None
    latest = pts[-1]["sold_count"]
    boundary = now - timedelta(days=days)
    old = None
    for pt in pts:
        ts = _parse_dt(pt["captured_at"])
        if ts is not None and ts <= boundary:
            old = pt["sold_count"]
    if old is None:
        return None
    return max(0, int(latest - old))


def _momentum(growth: float) -> float:
    """增速 -> 动能分。growth=0 -> 0，1x -> 60，3x+ -> 100（对数刻度）。"""
    if growth <= 0:
        return 0.0
    return round(min(100.0, 60 * math.log2(growth + 1)), 1)


def _novelty(age_days: float) -> float:
    """新品分：14 天内 100 分，线性衰减到 60 天归零。"""
    if age_days <= NEW_DAYS:
        return 100.0
    if age_days >= 60:
        return 0.0
    return round(100 * (60 - age_days) / (60 - NEW_DAYS), 1)


def _log_norm(value: float, cap: float = VELOCITY_CAP) -> float:
    return math.log1p(max(0.0, value)) / math.log1p(cap)


def compute_trends(db: Database, settings=None) -> int:
    """计算全部活跃商品的趋势与爆品指数并落库，返回处理条数。"""
    now = datetime.now(timezone.utc)
    products = db.products()
    series = db.snapshot_series(days=60)
    rows: list[dict] = []
    for p in products:
        product_id = p["product_id"]
        pts = series.get(product_id, [])
        sold_7d = _delta(pts, now, 7)
        sold_30d = _delta(pts, now, 30)
        if sold_7d is None:
            sold_7d = p.get("sale_7d_cnt") or 0
        if sold_30d is None:
            sold_30d = p.get("sale_30d_cnt") or 0

        prev_7d = max(0, sold_30d - sold_7d)
        prev_daily = prev_7d / 23.0 if sold_30d else 0.0
        growth_7d = round(sold_7d / (7 * prev_daily), 2) if prev_daily > 0 else 0.0

        age_days = 999.0
        first_seen = _parse_dt(p.get("first_seen_at") or "")
        if first_seen is not None:
            age_days = max(0.0, (now - first_seen).total_seconds() / 86400.0)

        is_new = 1 if (age_days <= NEW_DAYS and sold_7d >= NEW_MIN_7D) else 0

        velocity = round(_log_norm(sold_7d) * 100, 1)
        momentum = _momentum(growth_7d)
        novelty = _novelty(age_days)
        hot = round(0.5 * velocity + 0.3 * momentum + 0.2 * novelty, 1)

        rows.append({
            "product_id": product_id,
            "sold_7d": int(sold_7d),
            "sold_30d": int(sold_30d),
            "growth_7d": growth_7d,
            "is_new": is_new,
            "is_hot": 1 if hot >= 60 else 0,
            "velocity_score": velocity,
            "momentum_score": momentum,
            "novelty_score": novelty,
            "hot_score": hot,
        })
    db.save_trends(rows)
    return len(rows)
