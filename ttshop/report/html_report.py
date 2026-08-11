"""生成自包含的 HTML 分析看板（离线可用，无外部 JS 依赖）。"""

from __future__ import annotations

import csv
import html
import statistics
from collections import defaultdict
from pathlib import Path

from ..db import Database


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _money(value, symbol: str = "$") -> str:
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return _esc(value)


def _pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return _esc(value)


def _short(title: str, limit: int = 26) -> str:
    title = str(title)
    return title if len(title) <= limit else title[: limit - 1] + "…"


def _svg_bars(labels, values, color: str = "#4f46e5", width: int = 720, height: int = 300) -> str:
    if not labels:
        return "<p>暂无数据</p>"
    pad_l, pad_t, pad_b, pad_r = 52, 26, 52, 16
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vmax = max(values) or 1
    n = len(labels)
    gap = plot_w / n
    bw = min(gap * 0.6, 64)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Segoe UI, Arial, sans-serif">',
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{width - pad_r}" y2="{pad_t + plot_h}" stroke="#cbd5e1" stroke-width="1"/>',
    ]
    for i, (label, value) in enumerate(zip(labels, values)):
        x = pad_l + gap * i + (gap - bw) / 2
        h = plot_h * value / vmax
        y = pad_t + plot_h - h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(h, 2):.1f}" rx="4" fill="{color}"/>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle" font-size="12" fill="#334155">{value:,.0f}</text>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{pad_t + plot_h + 20:.1f}" text-anchor="middle" font-size="11" fill="#64748b">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_hbars(items, color: str = "#10b981", width: int = 720) -> str:
    """items: [(label, value_text, value)]"""
    if not items:
        return "<p>暂无数据</p>"
    row_h = 30
    pad_l, pad_r = 18, 104
    height = len(items) * row_h + 40
    plot_w = width - pad_l - pad_r
    vmax = max(v for _, _, v in items) or 1
    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Segoe UI, Arial, sans-serif">',
    ]
    for i, (label, value_text, value) in enumerate(items):
        y = 22 + i * row_h
        bw = plot_w * value / vmax
        parts.append(f'<rect x="{pad_l}" y="{y - 10}" width="{bw:.1f}" height="16" rx="4" fill="{color}"/>')
        parts.append(f'<text x="{pad_l + bw + 8:.1f}" y="{y + 2}" font-size="12" fill="#334155">{_esc(value_text)}</text>')
        parts.append(f'<text x="{pad_l - 8}" y="{y + 2}" text-anchor="end" font-size="11" fill="#475569">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_lines(x_labels, series, width: int = 760, height: int = 320) -> str:
    """series: [{"name", "color", "values"}]"""
    if not x_labels or not series:
        return "<p>暂无数据</p>"
    pad_l, pad_t, pad_b, pad_r = 56, 26, 46, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_v = [v for s in series for v in s["values"]]
    vmax = max(all_v) or 1
    n = len(x_labels)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Segoe UI, Arial, sans-serif">',
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{width - pad_r}" y2="{pad_t + plot_h}" stroke="#cbd5e1" stroke-width="1"/>',
    ]
    for s in series:
        points = []
        for i, v in enumerate(s["values"]):
            x = pad_l + plot_w * i / max(n - 1, 1)
            y = pad_t + plot_h - plot_h * v / vmax
            points.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{s["color"]}" stroke-width="2.5" stroke-linejoin="round"/>')
    for i, label in enumerate(x_labels):
        x = pad_l + plot_w * i / max(n - 1, 1)
        parts.append(f'<text x="{x:.1f}" y="{pad_t + plot_h + 20:.1f}" text-anchor="middle" font-size="11" fill="#64748b">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _category_stats(products: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        groups[p["category"]].append(p)
    rows = []
    for cat, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        prices = [p["price"] for p in items if p["price"]]
        sold = [p["sold_count"] for p in items if p["sold_count"]]
        ratings = [p["rating"] for p in items if p["rating"]]
        rows.append({
            "category": cat,
            "count": len(items),
            "avg_price": statistics.mean(prices) if prices else 0.0,
            "avg_sold": statistics.mean(sold) if sold else 0.0,
            "avg_rating": statistics.mean(ratings) if ratings else 0.0,
        })
    return rows

def _table_rows(analysis: list[dict], symbol: str) -> str:
    rows = []
    for i, a in enumerate(analysis, 1):
        rows.append(
            f"<tr><td>{i}</td>"
            f"<td title='{_esc(a['title'])}'>{_esc(_short(a['title']))}</td>"
            f"<td><span class='tag'>{_esc(a['category'])}</span></td>"
            f"<td class='num'>{_money(a['price'], symbol)}</td>"
            f"<td class='num'>{a['sold_count']:,}</td>"
            f"<td class='num'>{a['rating']}</td>"
            f"<td class='num'>{a['review_count']:,}</td>"
            f"<td class='num'>{_money(a['est_profit'], symbol)}</td>"
            f"<td class='num'>{_pct(a['est_margin'])}</td>"
            f"<td class='num score'>{a['selection_score']}</td></tr>"
        )
    return "\n".join(rows)


def _trend_block(trends: list[dict]) -> str:
    if not trends:
        return "<p>暂无趋势数据（重复执行采集后会出现销量趋势）</p>"
    palette = ["#4f46e5", "#0ea5e9", "#f59e0b"]
    x_labels: list[str] = []
    series = []
    for idx, t in enumerate(trends):
        pts = sorted(t["points"], key=lambda x: x["captured_at"])
        if idx == 0:
            x_labels = [p["captured_at"][5:10] for p in pts]
        series.append({
            "name": _short(t["title"], 18),
            "color": palette[idx % len(palette)],
            "values": [p["sold_count"] for p in pts],
        })
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;margin-right:16px;font-size:12px;color:#475569">'
        f'<span style="display:inline-block;width:14px;height:3px;background:{s["color"]};margin-right:6px;border-radius:2px"></span>'
        f'{_esc(s["name"])}</span>'
        for s in series
    )
    return f'<div class="legend">{legend}</div>' + _svg_lines(x_labels, series)


CSS = """
:root { --bg:#f1f5f9; --card:#fff; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --brand:#4f46e5; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
header h1 { font-size: 24px; margin: 0 0 4px; }
header p { color: var(--muted); margin: 0 0 20px; font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
.card .k { font-size: 12px; color: var(--muted); }
.card .v { font-size: 22px; font-weight: 600; margin-top: 6px; }
.card .s { font-size: 12px; color: #16a34a; margin-top: 4px; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
@media (max-width: 800px) { .cols2 { grid-template-columns: 1fr; } }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 18px; margin-bottom: 24px; }
.panel h2 { font-size: 16px; margin: 0 0 14px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; }
.score { font-weight: 700; color: var(--brand); }
tr:hover td { background: #f8fafc; }
.tag { display:inline-block; padding:2px 8px; border-radius:999px; background:#eef2ff; color:#4338ca; font-size:12px; }
.legend { margin-bottom: 8px; }
.scroll { overflow-x: auto; }
.footer { color: var(--muted); font-size: 12px; margin-top: 24px; text-align:center; }
"""


def build_report(db: Database, settings, output_path: str | Path) -> Path:
    """读取数据库，生成 HTML 看板与 Top 商品 CSV。返回报告文件路径。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    products = db.products()
    analysis = db.latest_analysis()
    trends = db.sales_trend(top=3)
    stats = db.stats()
    symbol = settings.currency_symbol
    generated_at = utc_now_display()

    margins = [a["est_margin"] for a in analysis if a.get("est_margin") is not None]
    avg_margin = statistics.mean(margins) if margins else 0.0
    opportunities = [a for a in analysis if (a["selection_score"] or 0) >= 75]
    opp_by_cat: dict[str, int] = defaultdict(int)
    for a in opportunities:
        opp_by_cat[a["category"]] += 1

    cat_rows = _category_stats(products)
    cat_labels = [r["category"] for r in cat_rows]
    cat_sold = [round(r["avg_sold"]) for r in cat_rows]

    top10 = [
        (_short(a["title"], 20), f"{a['selection_score']:.0f} 分", a["selection_score"])
        for a in analysis[:10]
    ]

    cat_table = "\n".join(
        f"<tr><td>{_esc(r['category'])}</td>"
        f"<td class='num'>{r['count']}</td>"
        f"<td class='num'>{_money(r['avg_price'], symbol)}</td>"
        f"<td class='num'>{r['avg_sold']:,.0f}</td>"
        f"<td class='num'>{r['avg_rating']:.1f}</td>"
        f"<td class='num'><span class='badge'>{opp_by_cat.get(r['category'], 0)}</span></td></tr>"
        for r in cat_rows
    )

    table_rows = _table_rows(analysis[:20], symbol)
    trend_html = _trend_block(trends)

    body = f"""
<header>
  <h1>TikTok Shop 爆品监测与选品分析看板</h1>
  <p>目标市场：{_esc(settings.region)} ｜ 生成时间：{_esc(generated_at)} ｜ 数据为演示/公开数据，仅用于学习研究</p>
</header>

<section class="cards">
  <div class="card"><div class="k">监测商品数</div><div class="v">{stats['products']:,}</div><div class="s">活跃商品</div></div>
  <div class="card"><div class="k">数据快照数</div><div class="v">{stats['snapshots']:,}</div><div class="s">价格/销量历史</div></div>
  <div class="card"><div class="k">平均毛利率</div><div class="v">{avg_margin * 100:.1f}%</div><div class="s">小包直发模型</div></div>
  <div class="card"><div class="k">机会商品数</div><div class="v">{len(opportunities)}</div><div class="s">选品分 ≥ 75</div></div>
</section>

<section class="cols2">
  <div class="panel"><h2>类目平均销量</h2>{_svg_bars(cat_labels, cat_sold)}</div>
  <div class="panel"><h2>Top 10 选品评分</h2>{_svg_hbars(top10)}</div>
</section>

<section class="panel"><h2>销量趋势 Top 3（近 30 天）</h2>{trend_html}</section>

<section class="panel">
  <h2>类目洞察</h2>
  <div class="scroll"><table>
    <tr><th>类目</th><th class="num">商品数</th><th class="num">均价</th><th class="num">平均销量</th><th class="num">平均评分</th><th class="num">高潜商品</th></tr>
    {cat_table}
  </table></div>
</section>

<section class="panel">
  <h2>爆品榜 Top 20（按选品分排序）</h2>
  <div class="scroll"><table>
    <tr><th>#</th><th>商品</th><th>类目</th><th class="num">售价</th><th class="num">已售</th><th class="num">评分</th><th class="num">评论数</th><th class="num">预估毛利</th><th class="num">毛利率</th><th class="num">选品分</th></tr>
    {table_rows}
  </table></div>
</section>

<div class="footer">TikTok Shop Analytics · 仅采集公开数据 · 控制抓取频率 · 遵守平台规则</div>
"""

    doc = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>TikTok Shop 选品分析看板</title><style>{CSS}</style></head>"
        f"<body><div class='wrap'>{body}</div></body></html>"
    )
    output_path.write_text(doc, encoding="utf-8")

    _export_csv(db, analysis, output_path.parent / "top_products.csv", symbol)
    return output_path


def _export_csv(db: Database, analysis: list[dict], csv_path: Path, symbol: str) -> None:
    fields = ["rank", "product_id", "title", "category", "price", "sold_count", "rating",
              "review_count", "video_views", "est_profit", "est_margin",
              "demand_score", "competition_score", "profit_score", "selection_score"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, a in enumerate(analysis[:50], 1):
            writer.writerow({
                "rank": i,
                "product_id": a["product_id"],
                "title": a["title"],
                "category": a["category"],
                "price": a["price"],
                "sold_count": a["sold_count"],
                "rating": a["rating"],
                "review_count": a["review_count"],
                "video_views": a["video_views"],
                "est_profit": a["est_profit"],
                "est_margin": a["est_margin"],
                "demand_score": a["demand_score"],
                "competition_score": a["competition_score"],
                "profit_score": a["profit_score"],
                "selection_score": a["selection_score"],
            })


def utc_now_display() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
