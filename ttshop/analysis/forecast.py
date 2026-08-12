"""销量预测与商品生命周期：基于 price_snapshots 时间序列。

对每个活跃商品：
- 用最小二乘线性回归拟合「累计销量 ~ 时间」的日均增速（斜率），外推未来 7/30 天销量增量；
- 结合 7 天增速、相对日均增速与商品年龄判断生命周期（导入期/成长期/成熟期/衰退期）；
- 生成可解释的选品推荐理由（需求/竞争/利润/生命周期/预测）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db import Database

NEW_DAYS = 21             # 导入期窗口（天）
GROWTH_THRESHOLD = 1.3    # 7天增速 >= 该值 -> 成长期
DECLINE_THRESHOLD = 0.7   # 0 < 7天增速 < 该值 -> 衰退期
SLOPE_GROWTH = 0.05       # 相对日均增速 >= 该值 -> 成长期
SLOPE_DECLINE = -0.05     # 相对日均增速 <= 该值 -> 衰退期


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_days(first_seen_at: str | None, now: datetime) -> float:
    ts = _parse_dt(first_seen_at or "")
    if ts is None:
        return 999.0
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def _lin_fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """(x=天数, y=累计销量) 最小二乘线性回归，返回 (斜率, R2)。"""
    n = len(points)
    if n < 2:
        return 0.0, 0.0
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    if sxx == 0:
        return 0.0, 0.0
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = sxy / sxx
    r2 = 0.0
    if n >= 3:
        ss_tot = sum((y - mean_y) ** 2 for _, y in points)
        if ss_tot > 0:
            ss_res = sum((y - (mean_y + slope * (x - mean_x))) ** 2 for x, y in points)
            r2 = max(0.0, min(1.0, 1 - ss_res / ss_tot))
    return slope, r2


def classify_lifecycle(growth_7d: float, rel_slope: float, age_days: float) -> str:
    """生命周期判断：导入期 -> 衰退期 -> 成长期 -> 成熟期（7天增速优先，斜率微调）。"""
    if age_days <= NEW_DAYS:
        return "导入期"
    if 0 < growth_7d < DECLINE_THRESHOLD:
        return "衰退期"
    if growth_7d >= GROWTH_THRESHOLD:
        return "成长期"
    if rel_slope <= SLOPE_DECLINE:
        return "衰退期"
    if rel_slope >= SLOPE_GROWTH:
        return "成长期"
    return "成熟期"


def _confidence(n: int, r2: float) -> float:
    """预测置信度：样本点越多、拟合越好越高（10-95）。"""
    if n < 2:
        return 10.0
    return round(min(95.0, 30 + n * 8 + r2 * 20), 1)


def _reason(p: dict, growth_7d: float, sold_7d: int, lifecycle: str,
            pred_7d: int, slope: float, analysis: dict | None) -> str:
    a = analysis or {}
    bits = [f"近7天销量{sold_7d:,}"]
    if growth_7d >= GROWTH_THRESHOLD:
        bits.append(f"增速{growth_7d:.1f}x（加速）")
    elif 0 < growth_7d < DECLINE_THRESHOLD:
        bits.append(f"增速{growth_7d:.1f}x（回落）")
    bits.append(f"处于{lifecycle}")
    demand = a.get("demand_score") or 0
    competition = a.get("competition_score") or 0
    profit = a.get("est_profit") or 0
    margin = a.get("est_margin") or 0
    if demand >= 60:
        bits.append(f"需求强({demand:.0f}分)")
    elif 0 < demand < 40:
        bits.append(f"需求偏弱({demand:.0f}分)")
    if competition >= 60:
        bits.append("竞争低(评论稀疏)")
    if profit > 0:
        bits.append(f"预估毛利${profit:.2f}({margin * 100:.0f}%)")
    if pred_7d > 0:
        bits.append(f"预测7天增量≈{pred_7d:,}件")
    elif slope < 0:
        bits.append("销量正在回落")
    return "，".join(bits) + "。"


def compute_forecasts(db: Database, settings=None) -> int:
    """计算全部活跃商品的销量预测/生命周期/推荐理由并落库，返回处理条数。"""
    now = datetime.now(timezone.utc)
    products = db.products()
    series = db.snapshot_series(days=90)
    trends = {t["product_id"]: t for t in db.latest_trends(limit=9999)}
    analysis = {a["product_id"]: a for a in db.latest_analysis()}
    rows: list[dict] = []
    for p in products:
        pid = p["product_id"]
        points: list[tuple[float, float]] = []
        for pt in series.get(pid, []):
            ts = _parse_dt(pt["captured_at"])
            if ts is not None:
                day = (ts - now).total_seconds() / 86400.0
                points.append((day, pt["sold_count"] or 0))
        points.sort(key=lambda x: x[0])
        slope, r2 = _lin_fit(points)
        pred_7d = max(0, round(slope * 7))
        pred_30d = max(0, round(slope * 30))

        t = trends.get(pid, {})
        growth_7d = t.get("growth_7d") or 0.0
        sold_7d = int(t.get("sold_7d") or p.get("sale_7d_cnt") or 0)
        age_days = _age_days(p.get("first_seen_at"), now)
        latest_sold = points[-1][1] if points else 0
        daily_rate = latest_sold / max(age_days, 1.0)
        rel_slope = slope / daily_rate if daily_rate > 0 else 0.0
        lifecycle = classify_lifecycle(growth_7d, rel_slope, age_days)
        confidence = _confidence(len(points), r2)
        reason = _reason(p, growth_7d, sold_7d, lifecycle, pred_7d, slope, analysis.get(pid))

        rows.append({
            "product_id": pid,
            "predicted_7d": pred_7d,
            "predicted_30d": pred_30d,
            "daily_slope": round(slope, 2),
            "confidence": confidence,
            "lifecycle": lifecycle,
            "reason": reason,
        })
    db.save_forecasts(rows)
    return len(rows)
