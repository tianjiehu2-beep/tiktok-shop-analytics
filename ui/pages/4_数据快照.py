import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from datetime import date
from pathlib import Path

import streamlit as st

from ui.common import (export_db_bytes, get_db, get_settings, require_password,
                       reset_db_cache, run_full_analysis, setup_env)

st.set_page_config(page_title="数据快照", page_icon="💾", layout="wide")
setup_env()
require_password()

st.title("💾 数据快照")
st.caption("云端免费空间的文件会在重启后重置：用「导出快照」把数据下载到本地，换设备后「导入快照」恢复。")

db = get_db()
settings = get_settings()

st.subheader("① 导出数据快照")
if st.button("打包当前数据库"):
    try:
        data = export_db_bytes(db)
        st.session_state["snapshot_bytes"] = data
        st.session_state["snapshot_name"] = f"tiktok_shop_{date.today().isoformat()}.db"
        st.success("打包完成，点击下方按钮下载。")
    except Exception as exc:
        st.error(f"打包失败：{exc}")
if st.session_state.get("snapshot_bytes"):
    st.download_button("⬇️ 下载快照 (.db)", st.session_state["snapshot_bytes"],
                       file_name=st.session_state["snapshot_name"],
                       mime="application/octet-stream")

st.subheader("② 导入数据快照")
uploaded = st.file_uploader("上传之前导出的 .db 快照", type=["db"])
if uploaded is not None and st.button("恢复数据（将覆盖当前数据库）"):
    try:
        db_path = Path(settings.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
        db_path.write_bytes(uploaded.getvalue())
        reset_db_cache()
        st.success("已恢复。可到「总览」页查看数据规模，或先重新生成分析报告。")
    except Exception as exc:
        st.error(f"恢复失败：{exc}")

st.subheader("③ 分析报告")
report_path = Path(settings.report_dir) / "tiktok_shop_report.html"
if st.button("重新生成分析报告"):
    with st.spinner("正在计算选品评分 / 毛利 / 趋势 / 竞品 / 异动……"):
        try:
            run_full_analysis(db, settings)
            st.success("分析完成，报告已刷新。")
        except Exception as exc:
            st.error(f"分析失败：{exc}")

if report_path.exists():
    st.download_button("⬇️ 下载分析报告 (HTML)", report_path.read_bytes(),
                       file_name="tiktok_shop_report.html", mime="text/html")
    top_csv = report_path.parent / "top_products.csv"
    if top_csv.exists():
        st.download_button("⬇️ 下载 Top 商品 CSV", top_csv.read_bytes(),
                           file_name="top_products.csv", mime="text/csv")
else:
    st.info("还没有报告，点上面「重新生成分析报告」。")

st.divider()
st.caption("定时更新：本地版支持 `python main.py schedule --time 08:30 --source api --category-id <ID>`；云端版打开页面即可手动采集。")
