import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import streamlit as st

from ui.common import (collect_and_store, get_api_source, price_sold_chart,
                       require_password, setup_env, show_products)

st.set_page_config(page_title="类目采集", page_icon="🗂️", layout="wide")
setup_env()
require_password()

st.title("🗂️ 按类目采集")
st.caption("先搜索类目关键词（如 yoga / fitness），在结果里选择类目，再点「开始采集」。")

SORT_LABELS = {
    "sales": "总销量降序", "sales7d": "近7天销量降序", "sales30d": "近30天销量降序",
    "gmv": "总GMV降序", "gmv7d": "近7天GMV降序", "gmv30d": "近30天GMV降序",
    "price": "价格降序",
}

with st.form("category_form"):
    term = st.text_input("搜索类目关键词", placeholder="例如 yoga / fitness / home")
    sort_field = st.selectbox("排序方式", list(SORT_LABELS.keys()),
                              format_func=lambda v: SORT_LABELS[v])
    c1, c2, c3 = st.columns(3)
    pages = c1.number_input("翻页数（每页最多10条）", min_value=1, max_value=20, value=2)
    min_sales = c2.number_input("最低总销量", min_value=0, value=0)
    max_price = c3.number_input("最高均价($)", min_value=0.0, value=0.0, step=5.0)
    enrich = st.checkbox("采集后补全商品详情（评分/评论/GMV）", value=True)
    submitted = st.form_submit_button("搜索类目")

if submitted and term.strip():
    source = get_api_source(provider="echotik")
    matches = source.search_categories(term.strip(), limit=50)
    if not matches:
        st.warning(f"没有找到包含 {term.strip()!r} 的类目，换个英文关键词试试。")
        st.stop()
    st.session_state["cat_matches"] = matches

matches = st.session_state.get("cat_matches", [])
if matches:
    options = {f"[{m['level']}] {m['path']}  (ID: {m['category_id']})": m["category_id"] for m in matches}
    choice = st.selectbox(f"共 {len(matches)} 个匹配类目，选择要采集的类目：", list(options.keys()))
    cat_id = options[choice]
    if st.button(f"开始采集该分类（{cat_id}）", type="primary"):
        with st.spinner("正在从 EchoTik 采集数据，请稍候……"):
            try:
                api = get_api_source(provider="echotik")
                api.category_id = cat_id
                api.pages = int(pages)
                api.sort_field = sort_field
                api.min_sales = int(min_sales) or None
                api.max_price = float(max_price) or None
                api.enrich = bool(enrich)
                result = api.fetch(limit=None)
            except Exception as exc:
                st.error(f"采集失败：{exc}")
                st.stop()
        collect_and_store(result.products, "按类目采集")
        st.session_state["last_products"] = [x.to_dict() for x in result.products]
        st.session_state["last_label"] = f"类目 {cat_id} 采集结果"

if st.session_state.get("last_products"):
    show_products(st.session_state["last_products"], st.session_state["last_label"])
    price_sold_chart(st.session_state["last_products"])
