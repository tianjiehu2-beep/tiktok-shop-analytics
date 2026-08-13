"""TikTok Shop 美区数据分析 - 总览看板（入口页）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from ui.common import get_db, require_password, setup_env

st.set_page_config(page_title="TikTok Shop 数据分析", page_icon="📊", layout="wide")
setup_env()
require_password()

st.title("📊 TikTok Shop 美区数据分析")
st.caption("类目选品 / 商品追踪 / 店铺监控 · 数据来自 EchoTik OpenAPI")

db = get_db()
stats = db.stats()

c1, c2, c3 = st.columns(3)
c1.metric("在库商品", stats.get("products", 0))
c2.metric("销量快照", stats.get("snapshots", 0))
c3.metric("分析记录", stats.get("analyses", 0))
st.divider()

left, right = st.columns(2)
with left:
    st.subheader("🏆 Top 店铺（按累计销量）")
    sellers = db.top_sellers(limit=8)
    if sellers:
        st.dataframe(pd.DataFrame([{
            "店铺ID": s["seller_id"],
            "店铺": s["seller_name"],
            "商品数": s["product_cnt"],
            "总销量": s["total_sold"],
            "GMV$": round(s["total_gmv"], 0),
        } for s in sellers]), width="stretch", hide_index=True)
    else:
        st.info("暂无店铺数据，去「店铺采集」页采集一个店铺试试")

with right:
    st.subheader("🔥 Top 商品（按销量）")
    products = db.products(limit=8)
    if products:
        st.dataframe(pd.DataFrame([{
            "商品ID": p["product_id"],
            "标题": (p["title"] or "")[:28],
            "价格$": p["price"],
            "总销量": p["sold_count"],
            "近7天": p.get("sale_7d_cnt", 0),
        } for p in products]), width="stretch", hide_index=True)
    else:
        st.info("暂无商品数据，去「类目采集」页选个类目试试")

st.divider()
with st.expander("⚠️ 今日异动（价格变动 / 销量激增 / 新品上榜）", expanded=False):
    from ttshop.analysis.alerts import ALERT_LABELS, today_alerts

    alerts = today_alerts(db, limit=30)
    if alerts:
        for a in alerts:
            label = ALERT_LABELS.get(a["alert_type"], a["alert_type"])
            st.markdown(f"- **[{label}]** {a['message']}")
    else:
        st.info("今日暂无异动。")

st.caption("操作指引：左侧「类目采集 / 商品采集 / 店铺采集」采集数据；「数据快照」可导出/导入数据、重新生成分析报告。")
