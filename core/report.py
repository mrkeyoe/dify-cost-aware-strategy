"""运行结束的成本报告（纯标准库）。

报告是策略层唯一的"副作用产物"：不写库、不落盘，随 ``_invoke`` 结束一起交给宿主。
字段刻意保持扁平，方便 Dify 日志展示与外部采集。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

__all__ = ["DegradeEvent", "CostReport"]


@dataclass(frozen=True)
class DegradeEvent:
    """一次被降级拦下的调用尝试。"""

    tool_name: str
    estimated_cost: float
    consumed_at_block: float
    attempt: int
    max_attempts: int
    reason_code: str
    reason_text: str
    cost_basis: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "estimated_cost": round(self.estimated_cost, 2),
            "consumed_at_block": round(self.consumed_at_block, 2),
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "reason_code": self.reason_code,
            "reason": self.reason_text,
            "cost_basis": self.cost_basis,
        }


@dataclass
class CostReport:
    total_budget: float
    consumed: float
    degrade_events: tuple[DegradeEvent, ...] = ()
    calls_allowed: int = 0
    calls_blocked: int = 0
    calls_failed: int = 0
    tool_costs: Mapping[str, float] = field(default_factory=dict)
    halted: bool = False
    halt_reason: Optional[str] = None
    elapsed_seconds: Optional[float] = None

    @property
    def degraded(self) -> bool:
        """本次运行是否发生过降级阻断。"""
        return bool(self.degrade_events)

    @property
    def degrade_count(self) -> int:
        return len(self.degrade_events)

    @property
    def remaining(self) -> float:
        return max(self.total_budget - self.consumed, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "degraded": self.degraded,
            "degrade_count": self.degrade_count,
            "degrade_reasons": [event.reason_text for event in self.degrade_events],
            "degrade_events": [event.to_dict() for event in self.degrade_events],
            "total_budget": round(self.total_budget, 2),
            "consumed": round(self.consumed, 2),
            "remaining": round(self.remaining, 2),
            "usage_ratio": round(self.consumed / self.total_budget, 4),
            "calls_allowed": self.calls_allowed,
            "calls_blocked": self.calls_blocked,
            "calls_failed": self.calls_failed,
            "tool_costs": {name: round(cost, 2) for name, cost in sorted(self.tool_costs.items())},
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "elapsed_seconds": (
                round(self.elapsed_seconds, 3) if self.elapsed_seconds is not None else None
            ),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)

    def to_text(self) -> str:
        """人读版本：适合直接作为一条 Dify 文本消息回显。"""
        status = "degraded" if self.degraded else "normal"
        lines = [
            "成本报告 / cost report",
            f"- status: {status}",
            f"- consumed: {self.consumed:.2f} / {self.total_budget:.2f} "
            f"(remaining {self.remaining:.2f})",
            f"- degrade_count: {self.degrade_count}",
            f"- calls: allowed={self.calls_allowed} blocked={self.calls_blocked} "
            f"failed={self.calls_failed}",
        ]
        if self.tool_costs:
            detail = ", ".join(f"{name}={cost:.2f}" for name, cost in sorted(self.tool_costs.items()))
            lines.append(f"- tool_costs: {detail}")
        if self.degraded:
            lines.append("- degrade_reasons:")
            for event in self.degrade_events:
                lines.append(
                    f"  - [{event.reason_code}] {event.tool_name} "
                    f"(estimated {event.estimated_cost:.2f}, attempt "
                    f"{event.attempt}/{event.max_attempts}): {event.reason_text}"
                )
        if self.halted:
            lines.append(f"- halted: {self.halt_reason}")
        if self.elapsed_seconds is not None:
            lines.append(f"- elapsed_seconds: {self.elapsed_seconds:.3f}")
        return "\n".join(lines)