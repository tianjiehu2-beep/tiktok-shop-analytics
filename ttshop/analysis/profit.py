"""毛利测算：跨境小包直发模型。

成本构成（默认值见 config.Settings，可按品类调整）：
  售价 = 采购成本 + 平台佣金 + 物流 + 广告 + 其他固定成本 + 利润
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfitEstimate:
    price: float
    purchase_cost: float
    commission: float
    shipping_cost: float
    ad_cost: float
    other_cost: float
    total_cost: float
    profit: float
    margin: float  # 毛利率


def estimate_profit(price: float, settings) -> ProfitEstimate:
    purchase_cost = price * settings.purchase_cost_ratio
    commission = price * settings.platform_commission
    ad_cost = price * settings.ad_spend_ratio
    total_cost = purchase_cost + commission + settings.shipping_cost + ad_cost + settings.other_cost
    profit = price - total_cost
    margin = profit / price if price else 0.0
    return ProfitEstimate(
        price=price,
        purchase_cost=round(purchase_cost, 2),
        commission=round(commission, 2),
        shipping_cost=settings.shipping_cost,
        ad_cost=round(ad_cost, 2),
        other_cost=settings.other_cost,
        total_cost=round(total_cost, 2),
        profit=round(profit, 2),
        margin=round(margin, 4),
    )
