"""今日变动：对比昨日状态，输出新上架 / 价格异动 / 销量激增三类变化。

- 新上架：今日（UTC）首次入库的商品，按销量排序。
- 价格异动：每个商品最近两次快照的价格变动百分比（涨/跌 Top N）。
- 销量激增：product_trends 中 7 天增速 >= 阈值且近 7 天有一定量级的商品。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db import Database

PRICE_MOVE_LIMIT = 10     # 价格异动列表条数
SURGE_LIMIT = 10          # 销量激增列表条数
SURGE_THRESHOLD = 1.5     # 7天增速阈值（倍）
SURGE_MIN_7D = 100        # 激增商品最低近7天销量


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def new_products(db: Database, days: int = 1, limit: int = 10) -> list[dict]:
    """近 days 天首次入库的商品（按销量降序）。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = []
    for p in db.products(limit=9999):
        fs = _parse_dt(p.get("first_seen_at") or "")
        if fs is not None and fs >= _parse_dt(cutoff):
            rows.append(p)
    rows.sort(key=lambda r: int(r.get("sold_count") or 0), reverse=True)
    return rows[:limit]


def price_moves(db: Database, limit: int = PRICE_MOVE_LIMIT) -> list[dict]:
    """最近两次快照的价格变动（按绝对变动幅度降序）。"""
    series = db.snapshot_series(days=60)
    moves: list[dict] = []
    for pid, pts in series.items():
        if len(pts) < 2:
            continue
        old = float(pts[-2].get("price") or 0)
        new = float(pts[-1].get("price") or 0)
        if old <= 0:
            continue
        pct = round((new - old) / old * 100, 2)
        if pct == 0:
            continue
        moves.append({
            "product_id": pid,
            "old_price": old,
            "new_price": new,
            "pct": pct,
            "title": "",
            "category": "",
        })
    moves.sort(key=lambda m: abs(m["pct"]), reverse=True)
    products = {p["product_id"]: p for p in db.products(limit=9999)}
    for m in moves[:limit]:
        p = products.get(m["product_id"]) or {}
        m["title"] = p.get("title") or ""
        m["category"] = p.get("category") or ""
    return moves[:limit]


def surge_products(db: Database, limit: int = SURGE_LIMIT) -> list[dict]:
    """7 天增速 >= 阈值且有一定量级的商品（按近7天销量降序）。"""
    rows = []
    for t in db.latest_trends(limit=9999):
        if (t.get("growth_7d") or 0) >= SURGE_THRESHOLD and (t.get("sold_7d") or 0) >= SURGE_MIN_7D:
            rows.append(t)
    rows.sort(key=lambda r: int(r.get("sold_7d") or 0), reverse=True)
    return rows[:limit]


def compute_changes(db: Database) -> dict:
    """汇总今日变动三类数据。"""
    return {
        "new": new_products(db),
        "price": price_moves(db),
        "surge": surge_products(db),
    }
