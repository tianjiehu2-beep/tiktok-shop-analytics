"""SQLite 数据层：建表、写入、查询、历史快照。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Influencer, KeywordTrend, Product, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    category        TEXT,
    price           REAL,
    original_price  REAL,
    sold_count      INTEGER,
    rating          REAL,
    review_count    INTEGER,
    seller_name     TEXT,
    seller_id       TEXT,
    commission_rate REAL,
    video_views     INTEGER,
    video_likes     INTEGER,
    listed_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    price       REAL,
    sold_count  INTEGER,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id        TEXT NOT NULL,
    analyzed_at       TEXT NOT NULL,
    demand_score      REAL,
    competition_score REAL,
    profit_score      REAL,
    selection_score   REAL,
    est_profit        REAL,
    est_margin        REAL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_product ON price_snapshots (product_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_analysis_product ON analysis_snapshots (product_id, analyzed_at);

CREATE TABLE IF NOT EXISTS influencers (
    user_id                 TEXT PRIMARY KEY,
    nick_name               TEXT,
    avatar                  TEXT,
    signature               TEXT,
    region                  TEXT,
    followers_cnt           INTEGER NOT NULL DEFAULT 0,
    followers_30d_cnt       INTEGER NOT NULL DEFAULT 0,
    post_video_cnt          INTEGER NOT NULL DEFAULT 0,
    digg_cnt                INTEGER NOT NULL DEFAULT 0,
    likes_cnt               INTEGER NOT NULL DEFAULT 0,
    interaction_rate        REAL NOT NULL DEFAULT 0,
    ec_score                REAL NOT NULL DEFAULT 0,
    sale_cnt                INTEGER NOT NULL DEFAULT 0,
    sale_gmv_amt            REAL NOT NULL DEFAULT 0,
    sale_gmv_30d_amt        REAL NOT NULL DEFAULT 0,
    product_cnt             INTEGER NOT NULL DEFAULT 0,
    live_cnt                INTEGER NOT NULL DEFAULT 0,
    per_video_views_avg_7d  REAL NOT NULL DEFAULT 0,
    category                TEXT,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keyword_trends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    video_num   INTEGER NOT NULL DEFAULT 0,
    popularity  INTEGER NOT NULL DEFAULT 0,
    trend_json  TEXT,
    region      TEXT,
    source      TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_influencers (
    product_id    TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    nick_name     TEXT,
    followers_cnt INTEGER NOT NULL DEFAULT 0,
    per_sale_cnt  INTEGER NOT NULL DEFAULT 0,
    per_gmv_amt   REAL NOT NULL DEFAULT 0,
    captured_at   TEXT NOT NULL,
    PRIMARY KEY (product_id, user_id, captured_at)
);

CREATE TABLE IF NOT EXISTS product_trends (
    product_id     TEXT PRIMARY KEY,
    sold_7d        INTEGER NOT NULL DEFAULT 0,
    sold_30d       INTEGER NOT NULL DEFAULT 0,
    growth_7d      REAL NOT NULL DEFAULT 0,
    is_new         INTEGER NOT NULL DEFAULT 0,
    is_hot         INTEGER NOT NULL DEFAULT 0,
    velocity_score REAL NOT NULL DEFAULT 0,
    momentum_score REAL NOT NULL DEFAULT 0,
    novelty_score  REAL NOT NULL DEFAULT 0,
    hot_score      REAL NOT NULL DEFAULT 0,
    computed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    message     TEXT,
    severity    INTEGER NOT NULL DEFAULT 0,
    alert_date  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (product_id, alert_type, alert_date)
);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @contextmanager
    def conn(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    EXTRA_COLUMNS = {
        "sale_7d_cnt": "INTEGER NOT NULL DEFAULT 0",
        "sale_30d_cnt": "INTEGER NOT NULL DEFAULT 0",
        "gmv_total": "REAL NOT NULL DEFAULT 0",
        "influencer_cnt": "INTEGER NOT NULL DEFAULT 0",
        "video_cnt": "INTEGER NOT NULL DEFAULT 0",
        "category_id": "TEXT",
    }

    def init_schema(self) -> None:
        with self.conn() as conn:
            conn.executescript(SCHEMA)
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
            for col, ddl in self.EXTRA_COLUMNS.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE products ADD COLUMN {col} {ddl}")

    def upsert_products(self, products: list[Product]) -> int:
        """新增或更新商品，并写入一条当前价格/销量快照。"""
        now = utc_now()
        written = 0
        with self.conn() as conn:
            for p in products:
                conn.execute(
                    """INSERT INTO products
                       (product_id, title, category, price, original_price, sold_count,
                        rating, review_count, seller_name, seller_id, commission_rate,
                        video_views, video_likes, listed_at, sale_7d_cnt, sale_30d_cnt,
                        gmv_total, influencer_cnt, video_cnt, category_id,
                        first_seen_at, last_seen_at, is_active)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                       ON CONFLICT(product_id) DO UPDATE SET
                        title=excluded.title, category=excluded.category, price=excluded.price,
                        original_price=excluded.original_price, sold_count=excluded.sold_count,
                        rating=excluded.rating, review_count=excluded.review_count,
                        seller_name=excluded.seller_name, seller_id=excluded.seller_id,
                        commission_rate=excluded.commission_rate, video_views=excluded.video_views,
                        video_likes=excluded.video_likes, listed_at=excluded.listed_at,
                        sale_7d_cnt=excluded.sale_7d_cnt, sale_30d_cnt=excluded.sale_30d_cnt,
                        gmv_total=excluded.gmv_total, influencer_cnt=excluded.influencer_cnt,
                        video_cnt=excluded.video_cnt, category_id=excluded.category_id,
                        last_seen_at=excluded.last_seen_at, is_active=1""",
                    (p.product_id, p.title, p.category, p.price, p.original_price,
                     p.sold_count, p.rating, p.review_count, p.seller_name, p.seller_id,
                     p.commission_rate, p.video_views, p.video_likes, p.listed_at,
                     p.sale_7d_cnt, p.sale_30d_cnt, p.gmv_total, p.influencer_cnt,
                     p.video_cnt, p.category_id, p.first_seen_at or now, now),
                )
                conn.execute(
                    """INSERT INTO price_snapshots (product_id, price, sold_count, captured_at)
                       VALUES (?,?,?,?)""",
                    (p.product_id, p.price, p.sold_count, now),
                )
                written += 1
        return written

    def add_history(self, product_id: str, price: float, sold_count: int, captured_at: str) -> None:
        with self.conn() as conn:
            conn.execute(
                "INSERT INTO price_snapshots (product_id, price, sold_count, captured_at) VALUES (?,?,?,?)",
                (product_id, price, sold_count, captured_at),
            )

    def upsert_influencers(self, influencers: list[Influencer]) -> int:
        now = utc_now()
        written = 0
        with self.conn() as conn:
            for inf in influencers:
                conn.execute(
                    """INSERT INTO influencers
                       (user_id, nick_name, avatar, signature, region, followers_cnt,
                        followers_30d_cnt, post_video_cnt, digg_cnt, likes_cnt,
                        interaction_rate, ec_score, sale_cnt, sale_gmv_amt, sale_gmv_30d_amt,
                        product_cnt, live_cnt, per_video_views_avg_7d, category,
                        first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(user_id) DO UPDATE SET
                        nick_name=excluded.nick_name, avatar=excluded.avatar,
                        signature=excluded.signature, region=excluded.region,
                        followers_cnt=excluded.followers_cnt,
                        followers_30d_cnt=excluded.followers_30d_cnt,
                        post_video_cnt=excluded.post_video_cnt,
                        digg_cnt=excluded.digg_cnt, likes_cnt=excluded.likes_cnt,
                        interaction_rate=excluded.interaction_rate, ec_score=excluded.ec_score,
                        sale_cnt=excluded.sale_cnt, sale_gmv_amt=excluded.sale_gmv_amt,
                        sale_gmv_30d_amt=excluded.sale_gmv_30d_amt,
                        product_cnt=excluded.product_cnt, live_cnt=excluded.live_cnt,
                        per_video_views_avg_7d=excluded.per_video_views_avg_7d,
                        category=excluded.category, last_seen_at=excluded.last_seen_at""",
                    (inf.user_id, inf.nick_name, inf.avatar, inf.signature, inf.region,
                     inf.followers_cnt, inf.followers_30d_cnt, inf.post_video_cnt,
                     inf.digg_cnt, inf.likes_cnt, inf.interaction_rate, inf.ec_score,
                     inf.sale_cnt, inf.sale_gmv_amt, inf.sale_gmv_30d_amt,
                     inf.product_cnt, inf.live_cnt, inf.per_video_views_avg_7d,
                     inf.category, inf.first_seen_at or now, now),
                )
                written += 1
        return written

    def upsert_keyword_trends(self, trends: list[KeywordTrend]) -> int:
        written = 0
        with self.conn() as conn:
            for t in trends:
                conn.execute(
                    """INSERT INTO keyword_trends (keyword, video_num, popularity, trend_json, region, source, captured_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (t.keyword, t.video_num, t.popularity,
                     json.dumps(t.trend or [], ensure_ascii=False),
                     t.region, t.source, t.captured_at),
                )
                written += 1
        return written

    def save_product_influencers(self, rows: list[dict]) -> int:
        """rows: product_id, user_id, nick_name, followers_cnt, per_sale_cnt, per_gmv_amt"""
        now = utc_now()
        written = 0
        with self.conn() as conn:
            for r in rows:
                conn.execute(
                    """INSERT INTO product_influencers
                       (product_id, user_id, nick_name, followers_cnt, per_sale_cnt, per_gmv_amt, captured_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (r["product_id"], r["user_id"], r.get("nick_name", ""),
                     r.get("followers_cnt") or 0, r.get("per_sale_cnt") or 0,
                     r.get("per_gmv_amt") or 0.0, now),
                )
                written += 1
        return written

    def top_influencers(self, limit: int = 20) -> list[dict]:
        with self.conn() as conn:
            rows = conn.execute(
                """SELECT * FROM influencers ORDER BY sale_gmv_amt DESC LIMIT ?""", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_keyword_trends(self, source: str = "ranking", limit: int = 20) -> list[dict]:
        with self.conn() as conn:
            rows = conn.execute(
                """SELECT * FROM keyword_trends WHERE source = ?
                   ORDER BY id DESC LIMIT ?""", (source, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def product_influencers(self, product_id: str, limit: int = 5) -> list[dict]:
        with self.conn() as conn:
            rows = conn.execute(
                """SELECT * FROM product_influencers WHERE product_id = ?
                   ORDER BY per_sale_cnt DESC LIMIT ?""", (product_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def snapshot_series(self, days: int = 60) -> dict[str, list[dict]]:
        """近 days 天内每个商品的销量快照序列 {product_id: [ {sold_count, captured_at} ]}。"""
        with self.conn() as conn:
            rows = conn.execute(
                """SELECT product_id, price, sold_count, captured_at
                   FROM price_snapshots
                   WHERE captured_at >= date('now', ?)
                   ORDER BY product_id, captured_at""",
                (f"-{days} days",),
            ).fetchall()
        series: dict[str, list[dict]] = {}
        for r in rows:
            series.setdefault(r["product_id"], []).append({
                "price": r["price"], "sold_count": r["sold_count"], "captured_at": r["captured_at"]})
        return series

    def save_trends(self, rows: list[dict]) -> int:
        now = utc_now()
        written = 0
        with self.conn() as conn:
            for t in rows:
                conn.execute(
                    """INSERT INTO product_trends
                       (product_id, sold_7d, sold_30d, growth_7d, is_new, is_hot,
                        velocity_score, momentum_score, novelty_score, hot_score, computed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(product_id) DO UPDATE SET
                        sold_7d=excluded.sold_7d, sold_30d=excluded.sold_30d,
                        growth_7d=excluded.growth_7d, is_new=excluded.is_new,
                        is_hot=excluded.is_hot, velocity_score=excluded.velocity_score,
                        momentum_score=excluded.momentum_score, novelty_score=excluded.novelty_score,
                        hot_score=excluded.hot_score, computed_at=excluded.computed_at""",
                    (t["product_id"], t["sold_7d"], t["sold_30d"], t["growth_7d"],
                     t["is_new"], t["is_hot"], t["velocity_score"], t["momentum_score"],
                     t["novelty_score"], t["hot_score"], now),
                )
                written += 1
        return written

    def latest_trends(self, limit: int = 20, only_hot: bool = False) -> list[dict]:
        sql = """SELECT t.*, p.title, p.category, p.price, p.sold_count, p.sale_7d_cnt,
                        p.influencer_cnt, p.video_cnt
                 FROM product_trends t JOIN products p ON p.product_id = t.product_id"""
        if only_hot:
            sql += " WHERE t.is_hot = 1"
        sql += " ORDER BY t.hot_score DESC LIMIT ?"
        with self.conn() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def save_alerts(self, alerts: list[dict], alert_date: str) -> int:
        now = utc_now()
        written = 0
        with self.conn() as conn:
            for a in alerts:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO alerts
                       (product_id, alert_type, message, severity, alert_date, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (a["product_id"], a["alert_type"], a.get("message", ""),
                     a.get("severity", 0), alert_date, now),
                )
                written += cur.rowcount
        return written

    def alerts_by_date(self, alert_date: str | None = None, limit: int = 60) -> list[dict]:
        sql = """SELECT a.*, p.title, p.category, p.price
                 FROM alerts a JOIN products p ON p.product_id = a.product_id"""
        params: list = []
        if alert_date:
            sql += " WHERE a.alert_date = ?"
            params.append(alert_date)
        sql += " ORDER BY a.severity DESC, a.id DESC LIMIT ?"
        params.append(limit)
        with self.conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def products(self, category: str | None = None, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM products WHERE is_active = 1"
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY sold_count DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self.conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def categories(self) -> list[str]:
        with self.conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM products WHERE is_active = 1 AND category IS NOT NULL ORDER BY category"
            ).fetchall()
        return [r["category"] for r in rows]

    def latest_analysis(self) -> list[dict]:
        """关联商品信息的最新一次选品分析结果（按选品分降序）。"""
        with self.conn() as conn:
            rows = conn.execute(
                """SELECT a.*, p.title, p.category, p.price, p.original_price,
                          p.sold_count, p.rating, p.review_count, p.video_views
                   FROM analysis_snapshots a
                   JOIN products p ON p.product_id = a.product_id
                   WHERE a.analyzed_at = (SELECT MAX(analyzed_at) FROM analysis_snapshots)
                   ORDER BY a.selection_score DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def sales_trend(self, top: int = 3, days: int = 30) -> list[dict]:
        """近 days 天内销量增长最快的 top N 商品，用于趋势图。"""
        with self.conn() as conn:
            rows = conn.execute(
                """SELECT s.product_id, p.title, s.captured_at, s.sold_count
                   FROM price_snapshots s
                   JOIN products p ON p.product_id = s.product_id
                   WHERE s.captured_at >= date('now', ?)
                   ORDER BY s.product_id, s.captured_at""",
                (f"-{days} days",),
            ).fetchall()
        series: dict[str, list[dict]] = {}
        for r in rows:
            series.setdefault(r["product_id"], []).append(dict(r))
        ranked = []
        for pid, pts in series.items():
            if len(pts) < 2:
                continue
            first, last = pts[0]["sold_count"], pts[-1]["sold_count"]
            growth = (last - first) / first if first else 0.0
            ranked.append({"product_id": pid, "title": pts[-1]["title"], "growth": growth, "points": pts})
        ranked.sort(key=lambda x: x["growth"], reverse=True)
        return ranked[:top]

    def stats(self) -> dict:
        with self.conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM products WHERE is_active=1").fetchone()["c"]
            snapshots = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
            analyses = conn.execute("SELECT COUNT(*) c FROM analysis_snapshots").fetchone()["c"]
        return {"products": total, "snapshots": snapshots, "analyses": analyses}

    def clear_all(self) -> None:
        with self.conn() as conn:
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM price_snapshots")
            conn.execute("DELETE FROM analysis_snapshots")
