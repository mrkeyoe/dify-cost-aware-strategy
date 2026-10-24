"""测试替身：不依赖 Dify SDK 的 LLM 与工具执行器。"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from core.loop import Plan, ToolResult


class FakeLLM:
    """按剧本返回计划，并记录每次规划时拿到的成本提示。"""

    def __init__(self, plans: Sequence[Plan], *, default_answer: str = "done"):
        self._plans = list(plans)
        self.default_answer = default_answer
        self.calls: list[dict[str, Any]] = []

    def plan(
        self,
        *,
        query: str,
        transcript: Sequence[Mapping[str, Any]],
        cost_hint: Optional[str],
        remaining_budget: float,
    ) -> Plan:
        self.calls.append(
            {
                "query": query,
                "transcript": tuple(transcript),
                "cost_hint": cost_hint,
                "remaining_budget": remaining_budget,
            }
        )
        if self._plans:
            return self._plans.pop(0)
        return Plan(final_answer=self.default_answer)


class RecordingExecutor:
    """记录被真正执行的工具调用；可配置失败。"""

    def __init__(self, *, ok: bool = True, content: str = "rows: 3", error: Optional[str] = None):
        self.ok = ok
        self.content = content
        self.error = error
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def execute(self, tool_name: str, tool_input: Mapping[str, Any]) -> ToolResult:
        self.calls.append((tool_name, dict(tool_input)))
        return ToolResult(ok=self.ok, content=self.content, error=self.error)