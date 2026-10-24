"""预算账本（纯标准库）。

单次 Agent 运行内维护，**不持久化**。所有金额都是相对成本单位。
账本只做加减法与边界判断，不做策略决策——决策在 :mod:`core.policy`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["BudgetLedger", "BudgetExhaustedError"]

_EPSILON = 1e-9


class BudgetExhaustedError(RuntimeError):
    """记账失败（例如退款金额超过已消费金额）。"""


@dataclass
class BudgetLedger:
    total_budget: float
    consumed: float = 0.0

    def __post_init__(self) -> None:
        if self.total_budget <= 0:
            raise ValueError(f"total_budget 必须大于 0，收到 {self.total_budget}")
        if self.consumed < 0:
            raise ValueError(f"consumed 不能为负，收到 {self.consumed}")

    @property
    def remaining(self) -> float:
        return max(self.total_budget - self.consumed, 0.0)

    @property
    def usage_ratio(self) -> float:
        return self.consumed / self.total_budget

    def can_afford(self, cost: float) -> bool:
        """边界语义：``cost + consumed == total_budget`` 仍算可支付（刚好花完）。"""
        if cost < 0:
            raise ValueError(f"cost 不能为负，收到 {cost}")
        return cost + self.consumed <= self.total_budget + _EPSILON

    def charge(self, cost: float) -> float:
        """扣费并返回扣费后的累计值。"""
        if cost < 0:
            raise ValueError(f"cost 不能为负，收到 {cost}")
        self.consumed += float(cost)
        return self.consumed

    def refund(self, cost: float) -> float:
        """工具执行失败时退款。不允许把 consumed 退成负数。"""
        if cost < 0:
            raise ValueError(f"cost 不能为负，收到 {cost}")
        if cost > self.consumed + _EPSILON:
            raise BudgetExhaustedError(
                f"退款 {cost} 超过已消费 {self.consumed}，账本可能已被复用"
            )
        self.consumed = max(self.consumed - float(cost), 0.0)
        return self.consumed

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_budget": round(self.total_budget, 4),
            "consumed": round(self.consumed, 4),
            "remaining": round(self.remaining, 4),
            "usage_ratio": round(self.usage_ratio, 4),
        }