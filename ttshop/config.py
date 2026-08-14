"""项目配置。命令行参数优先于此处默认值。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Settings:
    # 数据存储
    db_path: str = "data/tiktok_shop.db"
    report_dir: str = "reports"

    # 目标市场（TikTok Shop 数据按区域隔离）
    region: str = "TH"  # 默认市场：泰国（东南亚），可用 TTSHOP_REGION 覆盖
    currency: str = "USD"  # EchoTik 接口统一返回美元价

    # 真实采集（scraper 数据源）
    headless: bool = True
    slow_mo_ms: int = 800
    max_products_per_run: int = 100

    # 代理（scraper 数据源使用，如 socks5://127.0.0.1:40000；空则直连）
    proxy: str = ""

    # 第三方数据 API（api 数据源，如 Kalodata/EchoTik/FastMoss）
    api_provider: str = "echotik"
    api_base: str = ""          # 留空则用内置的示例地址（以平台官方文档为准）
    api_key: str = ""           # 留空时从 TTSHOP_API_KEY 环境变量读取
    api_timeout: int = 30       # 请求超时（秒）

    # 毛利测算参数（跨境小包直发模型，可按品类调整）
    purchase_cost_ratio: float = 0.25   # 采购成本 / 售价（1688 拿货估算）
    platform_commission: float = 0.05   # TikTok Shop 平台佣金比例
    shipping_cost: float = 4.0          # 头程 + 尾程物流，美元/单
    ad_spend_ratio: float = 0.15        # 广告投放 / 售价
    other_cost: float = 1.0             # 包装、汇损等固定成本，美元/单

    # 选品评分权重（合计 1.0）
    weight_demand: float = 0.40
    weight_competition: float = 0.30
    weight_profit: float = 0.30

    @property
    def currency_symbol(self) -> str:
        return {"USD": "$", "GBP": "£", "SGD": "S$", "MYR": "RM", "THB": "฿", "IDR": "Rp", "VND": "₫", "PHP": "₱"}.get(self.currency, self.currency + " ")
