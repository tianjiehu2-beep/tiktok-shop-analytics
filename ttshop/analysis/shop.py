"""店铺监控：卖家维度聚合 + 关注店铺上新检测 + 店铺上新告警。

- sync_sellers：把商品表按 seller_id 聚合成卖家维度（商品数/累计销量/GMV/均价）。
- 店铺关注池为空时自动关注销量 Top 3 卖家，保证看板开箱即用。
- 关注店铺近 7 天新上架商品会写入 alerts（店铺上新），复用「今日异动」面板与推送。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db import Database
from .alerts import ALERT_SEVERITY

AUTO_WATCH_TOP = 3     # 店铺关注池为空时自动关注销量 Top3 卖家
NEW_LISTING_DAYS = 7   # 多久内上架视为「新上架」


def ensure_shop_watch(db: Database, top: int = AUTO_WATCH_TOP) -> list[dict]:
    """店铺关注池为空时自动关注销量 Top N 卖家，返回关注列表。"""
    watched = db.shop_watch_list()
    if not watched:
        for s in db.top_sellers(limit=top):
            db.add_shop_watch(s["seller_id"])
        watched = db.shop_watch_list()
    return watched


def compute_shop_alerts(db: Database, days: int = NEW_LISTING_DAYS) -> tuple[int, int]:
    """同步卖家维度 + 检测关注店铺上新。返回 (卖家数, 新增告警数)。"""
    sellers = db.sync_sellers()
    ensure_shop_watch(db)
    listings = db.shop_new_listings(days=days, limit=50)
    alerts = [{
        "product_id": r["product_id"],
        "alert_type": "shop_new",
        "message": (f"关注店铺「{r['seller_name']}」新上架「{r['title'][:30]}」"
                    f"（售价 ${r['price']:.2f}，已售 {r['sold_count']:,}）"),
        "severity": ALERT_SEVERITY["shop_new"],
    } for r in listings]
    alert_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_alerts = db.save_alerts(alerts, alert_date) if alerts else 0
    return sellers, new_alerts
