"""竞品监控：围绕关注商品池识别同赛道竞品，追踪价格/销量变化并告警。

- 竞品定义：同属一级类目 + 售价在关注商品 ±50% 价带内的在售商品。
- 变化追踪：基于 price_snapshots 计算竞品近 2 天价格变动与近 7 天销量。
- 告警：竞品降价 >=5% 或竞品爆量（7天销量>=100 且增速>=1.5x）时写入 alerts，
  复用「今日异动」面板与 webhook 推送。
- 关注池为空时自动关注销量 Top3，保证看板开箱即用。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db import Database
from .alerts import ALERT_SEVERITY
from .trend import _delta  # 复用销量增量计算

PRICE_BAND = 0.5          # 同价带：±50%
PRICE_DROP_ALERT = 0.05   # 竞品降价告警阈值（5%）
MIN_SURGE = 100           # 竞品爆量：近7天最低销量
SURGE_GROWTH = 1.5        # 竞品爆量：最低 7 天增速
AUTO_WATCH_TOP = 3        # 关注池为空时自动关注销量 Top3


def _l1(category) -> str:
    if not category:
        return ""
    return str(category).split(">")[0].strip()


def _price_change_pct(pts: list[dict]) -> float:
    """近两次快照的价格变动百分比（%）。"""
    if len(pts) < 2:
        return 0.0
    old = pts[-2].get("price") or 0
    new = pts[-1].get("price") or 0
    if not old:
        return 0.0
    return round((new - old) / old * 100, 2)


def find_competitors(db: Database, product: dict, products: list[dict], series: dict,
                     limit: int = 8) -> list[dict]:
    """为一个商品识别竞品：同一级类目 + 同价格带，按近 7 天销量排序。"""
    l1 = _l1(product.get("category"))
    price = product.get("price") or 0
    if not l1 or price <= 0:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for c in products:
        if c["product_id"] == product["product_id"]:
            continue
        if _l1(c.get("category")) != l1:
            continue
        cp = c.get("price") or 0
        if cp <= 0 or cp < price * (1 - PRICE_BAND) or cp > price * (1 + PRICE_BAND):
            continue
        pts = series.get(c["product_id"], [])
        sold_7d = _delta(pts, now, 7)
        if sold_7d is None:
            sold_7d = int(c.get("sale_7d_cnt") or 0)
        out.append({
            "product_id": product["product_id"],
            "competitor_id": c["product_id"],
            "price_gap_pct": round((cp - price) / price * 100, 1),
            "price_change_pct": _price_change_pct(pts),
            "sold_7d": int(sold_7d),
            "rating": c.get("rating") or 0,
            "review_count": int(c.get("review_count") or 0),
            "competitor_title": c["title"],
        })
    out.sort(key=lambda x: x["sold_7d"], reverse=True)
    return out[:limit]


def compute_competitors(db: Database, per_watch: int = 8) -> tuple[int, int]:
    """计算全部关注商品的竞品并落库 + 检测竞品异动告警。返回 (竞品数, 新增告警数)。"""
    products = db.products()
    series = db.snapshot_series(days=60)
    trends = {t["product_id"]: t for t in db.latest_trends(limit=9999)}

    watched = db.watch_list()
    if not watched:
        top = sorted(products, key=lambda p: p.get("sold_count") or 0,
                     reverse=True)[:AUTO_WATCH_TOP]
        for p in top:
            db.add_watch(p["product_id"])
        watched = db.watch_list()

    rows: list[dict] = []
    move_alerts: list[dict] = []
    alert_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for w in watched:
        prod = next((p for p in products if p["product_id"] == w["product_id"]), None)
        if not prod:
            continue
        for c in find_competitors(db, prod, products, series, limit=per_watch):
            rows.append({k: c[k] for k in (
                "product_id", "competitor_id", "price_gap_pct",
                "price_change_pct", "sold_7d", "rating", "review_count")})
            if c["price_change_pct"] <= -PRICE_DROP_ALERT * 100:
                move_alerts.append({
                    "product_id": c["competitor_id"],
                    "alert_type": "comp_price_drop",
                    "message": (f"关注商品「{w['title'][:30]}」的竞品"
                                f"「{c['competitor_title'][:30]}」降价 {c['price_change_pct']:.0f}%"),
                    "severity": ALERT_SEVERITY["comp_price_drop"],
                })
            t = trends.get(c["competitor_id"]) or {}
            if c["sold_7d"] >= MIN_SURGE and (t.get("growth_7d") or 0) >= SURGE_GROWTH:
                move_alerts.append({
                    "product_id": c["competitor_id"],
                    "alert_type": "comp_surge",
                    "message": (f"关注商品「{w['title'][:30]}」的竞品"
                                f"「{c['competitor_title'][:30]}」近7天爆量 {c['sold_7d']:,}"
                                f"（增速 {t['growth_7d']:.1f}x）"),
                    "severity": ALERT_SEVERITY["comp_surge"],
                })

    saved_rows = db.save_competitors(rows) if rows else 0
    new_alerts = db.save_alerts(move_alerts, alert_date) if move_alerts else 0
    return saved_rows, new_alerts
