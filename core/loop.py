"""Agent 规划循环（纯标准库，协议驱动）。

把"感知成本 → 决定放行/降级/终止"的循环从 Dify SDK 里剥离出来：

* :class:`LLMProtocol` —— 一次规划（返回若干工具调用，或最终答案）；
* :class:`ToolExecutorProtocol` —— 真正执行工具（唯一会产生副作用的地方）。

因此 adapter 只负责把 Dify 的消息/模型桥接成这两个协议，循环本身可以脱离 SDK 单测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Protocol, Sequence, Union

from core.policy import (
    CostAwarePolicy,
    Decision,
    DecisionAction,
    REASON_DEGRADE_LIMIT,
    REASON_DEGRADE_DISABLED,
    REASON_MAX_ITERATIONS,
)
from core.report import CostReport

__all__ = [
    "PlannedToolCall",
    "Plan",
    "ToolResult",
    "LLMProtocol",
    "ToolExecutorProtocol",
    "TextEvent",
    "BudgetBlockedEvent",
    "ToolExecutedEvent",
    "HaltEvent",
    "ReportEvent",
    "LoopEvent",
    "CostAwareAgentLoop",
]


@dataclass(frozen=True)
class PlannedToolCall:
    call_id: str
    tool_name: str
    tool_input: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    tool_calls: tuple[PlannedToolCall, ...] = ()
    final_answer: Optional[str] = None
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    error: Optional[str] = None


class LLMProtocol(Protocol):
    def plan(
        self,
        *,
        query: str,
        transcript: Sequence[Mapping[str, Any]],
        cost_hint: Optional[str],
        remaining_budget: float,
    ) -> Plan:
        """返回下一步计划。``cost_hint`` 为本轮需要额外注入的系统提示（降级约束）。"""
        ...


class ToolExecutorProtocol(Protocol):
    def execute(self, tool_name: str, tool_input: Mapping[str, Any]) -> ToolResult:
        ...


# --------------------------------------------------------------------- 事件
@dataclass(frozen=True)
class TextEvent:
    text: str


@dataclass(frozen=True)
class BudgetBlockedEvent:
    """一次调用被降级拦截（工具未执行）。"""

    decision: Decision
    prompt: str


@dataclass(frozen=True)
class ToolExecutedEvent:
    call: PlannedToolCall
    decision: Decision
    result: ToolResult


@dataclass(frozen=True)
class HaltEvent:
    decision: Optional[Decision]
    reason_code: str
    reason_text: str


@dataclass(frozen=True)
class ReportEvent:
    report: CostReport


LoopEvent = Union[
    TextEvent, BudgetBlockedEvent, ToolExecutedEvent, HaltEvent, ReportEvent
]


class CostAwareAgentLoop:
    """单次运行的编排器。``run()`` 是生成器，事件流可被 adapter 直接转发。"""

    def __init__(
        self,
        policy: CostAwarePolicy,
        llm: LLMProtocol,
        executor: ToolExecutorProtocol,
        *,
        max_iterations: Optional[int] = None,
        clock=time.monotonic,
    ) -> None:
        self.policy = policy
        self.llm = llm
        self.executor = executor
        self.max_iterations = max_iterations or policy.config.max_iterations
        self._clock = clock
        self._last_report: Optional[CostReport] = None

    @property
    def last_report(self) -> Optional[CostReport]:
        return self._last_report

    def run(self, query: str) -> Iterator[LoopEvent]:
        started = self._clock()
        transcript: list[dict[str, Any]] = []
        pending_hint: Optional[str] = None
        halted = False

        for _ in range(self.max_iterations):
            plan = self.llm.plan(
                query=query,
                transcript=tuple(transcript),
                cost_hint=pending_hint,
                remaining_budget=self.policy.remaining,
            )
            pending_hint = None

            if not plan.wants_tools:
                yield TextEvent(plan.final_answer or "")
                break

            for call in plan.tool_calls:
                decision = self.policy.before_call(call.tool_name, call.tool_input)

                if decision.action is DecisionAction.DEGRADE:
                    prompt = decision.degrade_prompt or ""
                    # 提示同时进入下次规划的显式槽位与对话历史（宿主可见）
                    pending_hint = prompt
                    transcript.append(
                        {
                            "role": "system",
                            "content": prompt,
                            "tool_attempt": call.tool_name,
                        }
                    )
                    yield BudgetBlockedEvent(decision=decision, prompt=prompt)
                    continue

                if decision.action is DecisionAction.HALT:
                    halted = True
                    yield HaltEvent(
                        decision=decision,
                        reason_code=decision.reason_code,
                        reason_text=self._halt_text(decision),
                    )
                    break

                result = self.executor.execute(call.tool_name, call.tool_input)
                if not result.ok:
                    self.policy.on_tool_failure(decision, error=result.error or "")
                transcript.append(
                    {
                        "role": "tool",
                        "name": call.tool_name,
                        "tool_call_id": call.call_id,
                        "content": result.content,
                        "ok": result.ok,
                    }
                )
                yield ToolExecutedEvent(call=call, decision=decision, result=result)

            if halted:
                break
        else:
            # for 循环没有被 break：达到最大迭代数
            self.policy.halt(
                REASON_MAX_ITERATIONS,
                f"达到最大迭代数 {self.max_iterations}，提前结束",
            )
            halted = True
            yield HaltEvent(
                decision=None,
                reason_code=REASON_MAX_ITERATIONS,
                reason_text=f"达到最大迭代数 {self.max_iterations}，提前结束",
            )

        report = self.policy.report(elapsed_seconds=self._clock() - started)
        self._last_report = report
        yield ReportEvent(report=report)

    @staticmethod
    def _halt_text(decision: Decision) -> str:
        if decision.reason_code == REASON_DEGRADE_LIMIT:
            return (
                f"已连续 {decision.degrade_attempt} 次降级仍无法在预算内完成规划，"
                f"本次运行终止（不会继续调用 {decision.tool_name}）"
            )
        if decision.reason_code == REASON_DEGRADE_DISABLED:
            return (
                f"预算不足且降级被关闭：{decision.tool_name} 估算成本 "
                f"{decision.estimated_cost:.2f} 超出剩余 {decision.remaining_before:.2f}"
            )
        return f"预算策略终止运行：{decision.reason_code}"