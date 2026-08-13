"""数据模型定义。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Influencer:
    user_id: str
    nick_name: str
    avatar: str = ""
    signature: str = ""
    region: str = "US"
    followers_cnt: int = 0
    followers_30d_cnt: int = 0
    post_video_cnt: int = 0
    digg_cnt: int = 0
    likes_cnt: int = 0
    interaction_rate: float = 0.0
    ec_score: float = 0.0
    sale_cnt: int = 0
    sale_gmv_amt: float = 0.0
    sale_gmv_30d_amt: float = 0.0
    product_cnt: int = 0
    live_cnt: int = 0
    per_video_views_avg_7d: float = 0.0
    category: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        now = utc_now()
        if not data.get("first_seen_at"):
            data["first_seen_at"] = now
        if not data.get("last_seen_at"):
            data["last_seen_at"] = now
        return data


@dataclass
class KeywordTrend:
    keyword: str
    video_num: int = 0
    popularity: int = 0
    trend: list = None
    region: str = "US"
    source: str = "ranking"
    captured_at: str = ""

    def __post_init__(self) -> None:
        if self.captured_at == "":
            self.captured_at = utc_now()


@dataclass
class LiveSession:
    """直播带货场次：一场直播主推一个商品的销售表现。"""
    session_id: str
    seller_name: str
    seller_id: str
    product_id: str = ""
    product_title: str = ""
    category: str = ""
    live_title: str = ""
    gmv_amt: float = 0.0
    sold_cnt: int = 0
    viewers_peak: int = 0
    duration_min: int = 0
    live_at: str = ""
    captured_at: str = ""

    def __post_init__(self) -> None:
        if not self.captured_at:
            self.captured_at = utc_now()



@dataclass
class Product:
    product_id: str
    title: str
    category: str
    price: float
    original_price: float
    sold_count: int
    rating: float
    review_count: int
    seller_name: str
    seller_id: str
    commission_rate: float
    video_views: int
    video_likes: int
    listed_at: str
    sale_7d_cnt: int = 0
    sale_30d_cnt: int = 0
    gmv_total: float = 0.0
    influencer_cnt: int = 0
    video_cnt: int = 0
    category_id: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        now = utc_now()
        if not data.get("first_seen_at"):
            data["first_seen_at"] = now
        if not data.get("last_seen_at"):
            data["last_seen_at"] = now
        return data
