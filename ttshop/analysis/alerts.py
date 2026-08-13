"""监控告警：每日异动检测（降价 / 爆量 / 新品上榜）。

规则：
- price_drop：最近两次快照价格下降 >= price_drop_pct 且绝对降幅 >= min_price_drop
- surge：近 7 天销量 >= min_surge 且 7 天增速 >= growth_threshold（趋势模块产物）
- new_hot：14 天内新品且爆品指数 >= 60（趋势模块产物）

异动按 (product_id, alert_type, alert_date) 去重落库，重复执行不产生重复告警。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..db import Database

ALERT_LABELS = {
    "price_drop": "降价",
    "surge": "爆量",
    "new_hot": "新品上榜",
    "comp_price_drop": "竞品降价",
    "comp_surge": "竞品爆量",
    "shop_new": "店铺上新",
}
ALERT_SEVERITY = {"price_drop": 2, "surge": 3, "new_hot": 1, "comp_price_drop": 2, "comp_surge": 3, "shop_new": 1}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute_alerts(db: Database, min_surge: int = 100,
                   growth_threshold: float = 1.5, price_drop_pct: float = 0.05,
                   min_price_drop: float = 1.0) -> int:
    """生成今日异动清单并落库（按天去重）。返回今日新增异动条数。"""
    alert_date = _today()
    products = db.products()
    series = db.snapshot_series(days=30)
    trends = {t["product_id"]: t for t in db.latest_trends(limit=9999)}

    alerts: list[dict] = []
    for p in products:
        pid = p["product_id"]
        pts = series.get(pid, [])
        t = trends.get(pid)

        # 1) 降价：最近两次快照
        if len(pts) >= 2:
            last, prev = pts[-1], pts[-2]
            old_price = prev.get("price") or 0
            new_price = last.get("price") or 0
            if old_price and new_price and new_price <= old_price * (1 - price_drop_pct)                     and (old_price - new_price) >= min_price_drop:
                drop_pct = (old_price - new_price) / old_price * 100
                alerts.append({
                    "product_id": pid,
                    "alert_type": "price_drop",
                    "message": f"{p['title'][:60]} 降价 ${old_price:.2f} → ${new_price:.2f}（-{drop_pct:.0f}%）",
                    "severity": ALERT_SEVERITY["price_drop"],
                })

        # 2) 爆量：近7天销量 + 增速
        if t and t["sold_7d"] >= min_surge and t["growth_7d"] >= growth_threshold:
            alerts.append({
                "product_id": pid,
                "alert_type": "surge",
                "message": f"{p['title'][:60]} 近7天销量 {t['sold_7d']:,}（增速 {t['growth_7d']:.1f}x）",
                "severity": ALERT_SEVERITY["surge"],
            })

        # 3) 新品上榜：新品 + 爆品指数
        if t and t["is_new"] and t["hot_score"] >= 60:
            alerts.append({
                "product_id": pid,
                "alert_type": "new_hot",
                "message": f"{p['title'][:60]} 新品起量（爆品指数 {t['hot_score']:.0f}）",
                "severity": ALERT_SEVERITY["new_hot"],
            })

    return db.save_alerts(alerts, alert_date)


def today_alerts(db: Database, alert_date: str | None = None, limit: int = 60) -> list[dict]:
    return db.alerts_by_date(alert_date=alert_date or _today(), limit=limit)


def export_markdown(alerts: list[dict], output_dir: str | Path) -> Path:
    """把异动清单写成 Markdown（供推送/留档）。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"alerts_{_today()}.md"
    lines = [f"# TikTok Shop 异动清单（{_today()}）", ""]
    if not alerts:
        lines.append("今日暂无监控异动。")
    else:
        lines.append(f"共 {len(alerts)} 条异动：")
        lines.append("")
        for i, a in enumerate(alerts, 1):
            label = ALERT_LABELS.get(a["alert_type"], a["alert_type"])
            stars = "★" * a.get("severity", 0)
            lines.append(f"{i}. **[{label}] {stars}** {a['message']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def push_webhook(webhook_url: str, alerts: list[dict]) -> bool:
    """POST 异动清单到任意 webhook（钉钉/企业微信/飞书兼容 JSON）。"""
    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": "\n".join(f"[{ALERT_LABELS.get(a['alert_type'], a['alert_type'])}] {a['message']}" for a in alerts)
                 or "TikTok Shop 今日暂无监控异动"},
    }, ensure_ascii=False).encode("utf-8")
    request = Request(webhook_url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=15) as resp:
            return resp.status < 300
    except (URLError, OSError):
        return False
