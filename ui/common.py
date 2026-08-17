"""TikTok Shop 数据分析 UI - 公共模块。

密钥注入（云端 secrets -> 环境变量，本地仍读 data/api_key.txt）、
访问密码门、数据库连接、采集落库与结果展示辅助。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from ttshop.config import Settings
from ttshop.db import Database


def setup_env() -> None:
    """把云端 secrets 注入环境变量；本地环境继续走 data/api_key.txt。"""
    try:
        secrets = st.secrets
    except Exception:
        secrets = {}
    for env_key, secret_key in (
        ("TTSHOP_API_KEY", "TTSHOP_API_KEY"),
        ("TTSHOP_API_KEYS", "TTSHOP_API_KEYS"),
        ("TTSHOP_API_PROVIDER", "TTSHOP_API_PROVIDER"),
    ):
        if os.environ.get(env_key):
            continue
        try:
            val = secrets.get(secret_key, "")
        except Exception:
            val = ""
        if val:
            os.environ[env_key] = str(val)


def require_password() -> None:
    """访问密码门：只有云端配置了 APP_PASSWORD 才启用，本地预览不受影响。"""
    try:
        expected = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        return
    if st.session_state.get("authed"):
        return
    st.title("TikTok Shop 数据分析")
    entered = st.text_input("访问密码", type="password")
    if entered == expected:
        st.session_state["authed"] = True
        st.rerun()
    else:
        st.warning("请输入正确的访问密码")
        st.stop()


@st.cache_resource(show_spinner=False)
def get_settings() -> Settings:
    """Settings：数据库/报告路径固定指向仓库根目录，避免受启动目录影响。"""
    s = Settings()
    _region = os.environ.get("TTSHOP_REGION")
    if _region:
        s.region = _region
    _currency = os.environ.get("TTSHOP_CURRENCY")
    if _currency:
        s.currency = _currency
    if not Path(s.db_path).is_absolute():
        s.db_path = str(ROOT / s.db_path)
    if not Path(s.report_dir).is_absolute():
        s.report_dir = str(ROOT / s.report_dir)
    return s


@st.cache_resource(show_spinner=False)
def get_db() -> Database:
    db = Database(get_settings().db_path)
    db.init_schema()
    return db


def reset_db_cache() -> None:
    """导入快照后清空数据库缓存，强制重新连接。"""
    get_db.clear()


def get_api_source(**kwargs):
    """创建 API 数据源：走 get_source，本地自动读 data/api_key.txt，云端读环境变量。"""
    from ttshop.sources import get_source

    provider = kwargs.pop("provider", None)
    if provider:
        kwargs["api_provider"] = provider
    return get_source("api", get_settings(), **kwargs)


def collect_and_store(products, label: str) -> int:
    """采集结果写入数据库并同步卖家维度，返回写入条数。"""
    if not products:
        st.warning("本次没有采到数据：类目/ID 可能无效，或接口额度用尽。")
        return 0
    db = get_db()
    written = db.upsert_products(products)
    sellers = db.sync_sellers()
    st.success(f"{label}完成：获取 {len(products)} 条，写入 {written} 条，同步卖家 {sellers} 家")
    return written


DISPLAY_COLUMNS = [
    ("product_id", "商品ID"),
    ("title", "标题"),
    ("category", "类目"),
    ("price", "价格$"),
    ("sold_count", "总销量"),
    ("sale_7d_cnt", "近7天销量"),
    ("sale_30d_cnt", "近30天销量"),
    ("gmv_total", "总GMV$"),
    ("rating", "评分"),
    ("review_count", "评论数"),
    ("seller_name", "店铺"),
]


def show_products(products: list[dict], label: str = "采集结果") -> None:
    if not products:
        return
    rows = [{zh: p.get(en) for en, zh in DISPLAY_COLUMNS} for p in products]
    st.subheader(label)
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def price_sold_chart(products: list[dict]) -> None:
    if len(products) < 2:
        return
    import plotly.express as px

    df = pd.DataFrame(products)
    top = df.sort_values("sold_count", ascending=False).head(15)
    fig = px.bar(top, x="title", y="sold_count", color="sold_count",
                 labels={"title": "", "sold_count": "总销量"}, title="Top 商品销量")
    fig.update_layout(xaxis_tickangle=-30, height=420)
    st.plotly_chart(fig, width="stretch")

    if "price" in df and "sold_count" in df:
        fig2 = px.scatter(df, x="price", y="sold_count", hover_name="title",
                          labels={"price": "价格($)", "sold_count": "总销量"},
                          title="价格 - 销量分布")
        st.plotly_chart(fig2, width="stretch")


def export_db_bytes(db) -> bytes:
    """把数据库打包成一致的 .db 快照（VACUUM INTO），返回字节。"""
    import sqlite3

    src = Path(db.db_path)
    tmp = src.parent / f"snapshot_export_{datetime.now():%Y%m%d_%H%M%S}.db"
    con = sqlite3.connect(str(src))
    try:
        con.execute(f"VACUUM INTO '{str(tmp).replace(chr(39), chr(39) * 2)}'")
    finally:
        con.close()
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return data


def run_full_analysis(db, settings) -> Path:
    """复用 CLI 的分析流水线：评分/趋势/预测/竞品/店铺/异动 + 生成报告。"""
    from ttshop.analysis.alerts import compute_alerts
    from ttshop.analysis.competitor import compute_competitors
    from ttshop.analysis.forecast import compute_forecasts
    from ttshop.analysis.scoring import run_analysis
    from ttshop.analysis.shop import compute_shop_alerts
    from ttshop.analysis.trend import compute_trends
    from ttshop.report.html_report import build_report

    run_analysis(db, settings)
    compute_trends(db, settings)
    compute_forecasts(db, settings)
    compute_competitors(db)
    compute_shop_alerts(db)
    compute_alerts(db)
    return build_report(db, settings,
                        Path(settings.report_dir) / "tiktok_shop_report.html", source="api")
