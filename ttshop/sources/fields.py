"""商品字段解析工具（第三方 API 与采集器共用）。"""

from __future__ import annotations

import re


def first_key(data: dict, keys: list[str], default=None):
    """按优先级取第一个非空字段值。"""
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def parse_count(value) -> int:
    """解析 '2.3K' / '1.2M' / '1.5万' / 1234 这类销量文本。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    match = re.match(r"([\d.]+)\s*([KkMm万]?)", text)
    if not match:
        return 0
    num = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "k":
        num *= 1000
    elif unit == "m":
        num *= 1_000_000
    elif unit == "万":
        num *= 10_000
    return int(num)


def parse_price(value) -> float:
    """解析 '12.99' / '$12.99' / 'US$ 12.99' 这类价格文本。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[\d]+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else 0.0


def parse_rating(value) -> float:
    """解析 4.6 / '4.6 分' 这类评分文本。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else 0.0
