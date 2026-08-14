import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import streamlit as st

from ui.common import collect_and_store, get_api_source, require_password, setup_env, show_products

st.set_page_config(page_title="商品采集", page_icon="🛍️", layout="wide")
setup_env()
require_password()

st.title("🛍️ 商品采集")
st.caption("按关键词 / 商品名称搜索（如 yoga mat）或按商品 ID 精准采集。数据来自 EchoTik OpenAPI。")

mode = st.radio("采集方式", ["按关键词 / 商品名称搜索", "按商品 ID 采集"], horizontal=True)

if mode.startswith("按关键词"):
    kw = st.text_input("关键词 / 商品名称（多个用英文逗号分隔）",
                       placeholder="yoga mat, massage gun, air fryer")
    limit = st.number_input("数量上限（每个关键词）", min_value=1, max_value=50, value=20)
    if st.button("开始采集", type="primary"):
        if not kw.strip():
            st.warning("请先输入关键词或商品名称")
            st.stop()
        with st.spinner("正在从 EchoTik 采集数据，请稍候……"):
            try:
                api = get_api_source(provider="echotik")
                result = api.fetch(keyword=kw.strip(), limit=int(limit))
            except Exception as exc:
                st.error(f"采集失败：{exc}")
                st.stop()
        collect_and_store(result.products, "按关键词搜索")
        st.session_state["last_products"] = [x.to_dict() for x in result.products]
        st.session_state["last_label"] = f"关键词 {kw.strip()} 采集结果"
else:
    ids_raw = st.text_area("商品 ID（多个用英文逗号分隔）",
                           placeholder="1729385586104308698, 1731306663137743592")
    ids = [i.strip() for i in ids_raw.replace("\n", ",").split(",") if i.strip()]

    if st.button("开始采集", type="primary"):
        if not ids:
            st.warning("请先输入至少一个商品 ID")
            st.stop()
        with st.spinner(f"正在采集 {len(ids)} 个商品……"):
            try:
                api = get_api_source(provider="echotik", product_ids=",".join(ids))
                result = api.fetch(limit=None)
            except Exception as exc:
                st.error(f"采集失败：{exc}")
                st.stop()
        collect_and_store(result.products, "按商品 ID 采集")
        st.session_state["last_products"] = [x.to_dict() for x in result.products]
        st.session_state["last_label"] = f"商品 {len(result.products)} 个 采集结果"

if st.session_state.get("last_products"):
    show_products(st.session_state["last_products"], st.session_state["last_label"])
