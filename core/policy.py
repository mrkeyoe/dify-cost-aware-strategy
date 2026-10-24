"""预算决策层（纯标准库）。

:class:`CostAwarePolicy` 是策略的"中枢"：每次工具调用**前**问它一次，
它给出三类裁决之一：

* ``ALLOW``   —— 放行，并立即记账（预扣，避免并发/重复调用绕开预算）；
* ``DEGRADE`` —— 拒绝本次执行，返回结构化降级提示，让 LLM 重新规划；
* ``HALT``    —— 终止规划循环（预算硬阻断，或降级次数已用尽）。

降级次数是**连续**计数：一旦有一次调用被放行，计数清零；超过
``max_degrade_attempts`` 立即 ``HALT``，从机制上杜绝"反复请一个更便宜的方案
但每次都超预算"的死循环。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from core.budget import BudgetLedger
from core.config import StrategyConfig
from core.degrade import build_degrade_prompt
from core.estimator import CostEstimate, CostEstimator
from core.report import CostReport, DegradeEvent

__all__ = [
    "Decision",
    "DecisionAction",
    "CostAwarePolicy",
    "REASON_BUDGET_EXCEEDED",
    "REASON_DEGRADE_DISABLED",
    "REASON_DEGRADE_LIMIT",
    "REASON_MAX_ITERATIONS",
]


class DecisionAction(str, Enum):
    ALLOW = "allow"
    DEGRADE = "degrade"
    HALT = "halt"


REASON_BUDGET_EXCEEDED = "budget_exceeded"
REASON_DEGRADE_DISABLED = "budget_exceeded_degrade_disabled"
REASON_DEGRADE_LIMIT = "degrade_limit_reached"
REASON_MAX_ITERATIONS = "max_iterations_reached"


@dataclass(frozen=True)
class Decision:
    """一次调用前的裁决结果。"""

    action: DecisionAction
    tool_name: str
    estimate: CostEstimate
    consumed_before: float
    consumed_after: float
    total_budget: float
    reason_code: str = ""
    degrade_prompt: Optional[str] = None
    degrade_attempt: int = 0
    max_degrade_attempts: int = 0

    @property
    def allowed(self) -> bool:
        return self.action is DecisionAction.ALLOW

    @property
    def blocked(self) -> bool:
        return self.action is not DecisionAction.ALLOW

    @property
    def terminal(self) -> bool:
        return self.action is DecisionAction.HALT

    @property
    def estimated_cost(self) -> float:
        return self.estimate.cost

    @property
    def remaining_before(self) -> float:
        return max(self.total_budget - self.consumed_before, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "tool": self.tool_name,
            "estimated_cost": round(self.estimated_cost, 2),
            "estimate": self.estimate.to_dict(),
            "consumed_before": round(self.consumed_before, 2),
            "consumed_after": round(self.consumed_after, 2),
            "total_budget": round(self.total_budget, 2),
            "remaining_before": round(self.remaining_before, 2),
            "reason_code": self.reason_code,
            "degrade_attempt": self.degrade_attempt,
            "max_degrade_attempts": self.max_degrade_attempts,
        }


class CostAwarePolicy:
    """单次 Agent 运行内的预算状态机（不持久化、不跨运行复用）。"""

    def __init__(self, config: StrategyConfig, estimator: Optional[CostEstimator] = None):
        self.config = config.validate()
        self.ledger = BudgetLedger(total_budget=config.total_budget)
        self.estimator = estimator or CostEstimator(config)
        self._degrade_streak = 0
        self._degrade_events: list[DegradeEvent] = []
        self._calls_allowed = 0
        self._calls_blocked = 0
        self._calls_failed = 0
        self._tool_costs: dict[str, float] = {}
        self._halted = False
        self._halt_reason: Optional[str] = None

    # ------------------------------------------------------------ 状态读取
    @property
    def consumed(self) -> float:
        return self.ledger.consumed

    @property
    def total_budget(self) -> float:
        return self.ledger.total_budget

    @property
    def remaining(self) -> float:
        return self.ledger.remaining

    @property
    def degrade_streak(self) -> int:
        return self._degrade_streak

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> Optional[str]:
        return self._halt_reason

    # ------------------------------------------------------------ 记账辅助
    def consume(self, amount: float, *, note: str = "external") -> float:
        """登记运行内已发生的消耗（例如宿主先跑了固定步骤/测试预置状态）。"""
        return self.ledger.charge(amount)

    # ------------------------------------------------------------ 决策入口
    def before_call(
        self, tool_name: str, tool_input: Optional[Mapping[str, Any]] = None
    ) -> Decision:
        if self._halted:
            # 已终止的运行不再接受新调用，但保持幂等（不重复计数）
            return Decision(
                action=DecisionAction.HALT,
                tool_name=tool_name,
                estimate=CostEstimate(tool_name, 0.0, "halted", "high", note="运行已终止"),
                consumed_before=self.ledger.consumed,
                consumed_after=self.ledger.consumed,
                total_budget=self.ledger.total_budget,
                reason_code=self._halt_reason or REASON_BUDGET_EXCEEDED,
                max_degrade_attempts=self.config.max_degrade_attempts,
            )

        estimate = self.estimator.estimate(tool_name, tool_input)
        consumed_before = self.ledger.consumed

        if self.ledger.can_afford(estimate.cost):
            self.ledger.charge(estimate.cost)
            self._calls_allowed += 1
            self._tool_costs[tool_name] = self._tool_costs.get(tool_name, 0.0) + estimate.cost
            self._degrade_streak = 0
            return Decision(
                action=DecisionAction.ALLOW,
                tool_name=tool_name,
                estimate=estimate,
                consumed_before=consumed_before,
                consumed_after=self.ledger.consumed,
                total_budget=self.ledger.total_budget,
                reason_code="within_budget",
                max_degrade_attempts=self.config.max_degrade_attempts,
            )

        self._calls_blocked += 1

        if not self.config.degrade_enabled:
            return self._halt(
                tool_name=tool_name,
                estimate=estimate,
                consumed_before=consumed_before,
                reason_code=REASON_DEGRADE_DISABLED,
                reason_text=(
                    f"预算不足且 degrade_enabled=false：{tool_name} 估算成本 "
                    f"{estimate.cost:.2f} + 已消费 {consumed_before:.2f} 超过 "
                    f"total_budget {self.ledger.total_budget:.2f}，直接终止"
                ),
            )

        attempt = self._degrade_streak + 1
        if attempt > self.config.max_degrade_attempts:
            return self._halt(
                tool_name=tool_name,
                estimate=estimate,
                consumed_before=consumed_before,
                reason_code=REASON_DEGRADE_LIMIT,
                reason_text=(
                    f"连续降级 {self._degrade_streak} 次仍无法在预算内完成规划"
                    f"（max_degrade_attempts={self.config.max_degrade_attempts}），终止运行避免死循环"
                ),
            )

        self._degrade_streak = attempt
        prompt = build_degrade_prompt(
            tool_name=tool_name,
            estimate=estimate,
            consumed=consumed_before,
            total_budget=self.ledger.total_budget,
            attempt=attempt,
            max_attempts=self.config.max_degrade_attempts,
        )
        reason_text = (
            f"{tool_name} 估算成本 {estimate.cost:.2f}（{estimate.basis}）超出剩余预算 "
            f"{self.remaining:.2f}，已阻止执行并要求重新规划"
        )
        self._degrade_events.append(
            DegradeEvent(
                tool_name=tool_name,
                estimated_cost=estimate.cost,
                consumed_at_block=consumed_before,
                attempt=attempt,
                max_attempts=self.config.max_degrade_attempts,
                reason_code=REASON_BUDGET_EXCEEDED,
                reason_text=reason_text,
                cost_basis=estimate.basis,
            )
        )
        return Decision(
            action=DecisionAction.DEGRADE,
            tool_name=tool_name,
            estimate=estimate,
            consumed_before=consumed_before,
            consumed_after=self.ledger.consumed,
            total_budget=self.ledger.total_budget,
            reason_code=REASON_BUDGET_EXCEEDED,
            degrade_prompt=prompt,
            degrade_attempt=attempt,
            max_degrade_attempts=self.config.max_degrade_attempts,
        )

    # ------------------------------------------------------------ 调用后
    def on_tool_failure(self, decision: Decision, *, error: str = "") -> None:
        """工具执行失败：退款，避免"失败也计费"导致预算被白白吃掉。"""
        if decision.action is not DecisionAction.ALLOW:
            return
        self._calls_failed += 1
        self.ledger.refund(decision.estimated_cost)
        remaining = self._tool_costs.get(decision.tool_name, 0.0) - decision.estimated_cost
        if remaining > 0:
            self._tool_costs[decision.tool_name] = remaining
        else:
            self._tool_costs.pop(decision.tool_name, None)

    def halt(self, reason_code: str, reason_text: str = "") -> None:
        """由外循环（如达到最大迭代数）触发的终止。"""
        self._halted = True
        self._halt_reason = reason_code
        if reason_text:
            self._halted_detail = reason_text

    def _halt(
        self,
        *,
        tool_name: str,
        estimate: CostEstimate,
        consumed_before: float,
        reason_code: str,
        reason_text: str,
    ) -> Decision:
        self.halt(reason_code, reason_text)
        return Decision(
            action=DecisionAction.HALT,
            tool_name=tool_name,
            estimate=estimate,
            consumed_before=consumed_before,
            consumed_after=self.ledger.consumed,
            total_budget=self.ledger.total_budget,
            reason_code=reason_code,
            degrade_attempt=self._degrade_streak,
            max_degrade_attempts=self.config.max_degrade_attempts,
        )

    # ------------------------------------------------------------ 报告
    def report(self, *, elapsed_seconds: Optional[float] = None) -> CostReport:
        return CostReport(
            total_budget=self.ledger.total_budget,
            consumed=self.ledger.consumed,
            degrade_events=tuple(self._degrade_events),
            calls_allowed=self._calls_allowed,
            calls_blocked=self._calls_blocked,
            calls_failed=self._calls_failed,
            tool_costs=dict(self._tool_costs),
            halted=self._halted,
            halt_reason=self._halt_reason,
            elapsed_seconds=elapsed_seconds,
        )