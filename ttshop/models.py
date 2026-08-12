"""数据模型定义。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
