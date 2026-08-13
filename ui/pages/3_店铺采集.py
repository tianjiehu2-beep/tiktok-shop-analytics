import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import pandas as pd
import streamlit as st

from ui.common import (collect_and_store, get_api_source, get_db, price_sold_chart,
                       require_password, setup_env, show_products)

st.set_page_config(page_title="店铺采集", page_icon="🏪", layout="wide")
setup_env()
require_password()

st.title("🏪 按店铺采集")
st.caption("粘贴 TikTok Shop 店铺主页链接里的店铺 ID（.../shop/7496125336660249320），翻页抓取该店在售商品。")

seller_id = st.text_input("店铺 ID", placeholder="7496125336660249320")
pages = st.number_input("翻页数（每页最多10条，店铺商品多可加大）", min_value=1, max_value=50, value=3)

if st.button("开始采集", type="primary"):
    sid = seller_id.strip()
    if not sid:
        st.warning("请先输入店铺 ID")
        st.stop()
    with st.spinner(f"正在采集店铺 {sid} 的商品……"):
        try:
            api = get_api_source(provider="echotik", seller_id=sid, pages=int(pages))
            result = api.fetch(limit=None)
        except Exception as exc:
            st.error(f"采集失败：{exc}")
            st.stop()
    collect_and_store(result.products, "按店铺采集")
    st.session_state["last_products"] = [x.to_dict() for x in result.products]
    st.session_state["last_label"] = f"店铺 {sid} 采集结果"

st.divider()
st.subheader("👀 店铺监控")
db = get_db()
watch_ids = [w["seller_id"] for w in db.shop_watch_list()]
st.caption("已关注：" + ("、".join(watch_ids) if watch_ids else "暂无"))

sid = seller_id.strip()
if sid and sid not in watch_ids:
    if st.button(f"关注店铺 {sid}"):
        db.add_shop_watch(sid)
        st.success("已加入监控。")
        st.rerun()

if st.button("查看已关注店铺 7 天上新"):
    listings = db.shop_new_listings(days=7, limit=20)
    if listings:
        st.dataframe(pd.DataFrame([{
            "店铺": l["seller_name"], "标题": (l["title"] or "")[:40],
            "价格$": l["price"], "已售": l["sold_count"],
            "首次出现": (l["first_seen_at"] or "")[:10],
        } for l in listings]), width="stretch", hide_index=True)
    else:
        st.info("近 7 天暂无上新。")

if st.session_state.get("last_products"):
    show_products(st.session_state["last_products"], st.session_state["last_label"])
    price_sold_chart(st.session_state["last_products"])
