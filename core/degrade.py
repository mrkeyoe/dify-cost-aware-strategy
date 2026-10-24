"""结构化降级提示（纯标准库）。

提示不是一句自然语言，而是一个**带固定字段的块**：
LLM 可以先读``status``/``remaining``/``deficit`` 判断约束，再读 ``alternatives``
重新规划；同时末尾附一行 JSON，方便宿主或测试做机器校验。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from core.estimator import CostEstimate

__all__ = [
    "DegradeAlternative",
    "DEFAULT_ALTERNATIVES",
    "build_degrade_prompt",
    "parse_degrade_prompt",
    "PROMPT_MARKER",
]

PROMPT_MARKER = "[BUDGET_CONSTRAINT]"


@dataclass(frozen=True)
class DegradeAlternative:
    key: str
    title: str
    hint: str


DEFAULT_ALTERNATIVES: tuple[DegradeAlternative, ...] = (
    DegradeAlternative(
        key="sample",
        title="采样查询",
        hint="加 LIMIT / TABLESAMPLE 只取少量样本，先验证假设再决定是否需要全量",
    ),
    DegradeAlternative(
        key="preaggregate",
        title="聚合表",
        hint="改读已预聚合的宽表子集或物化视图，避免明细全表扫描",
    ),
    DegradeAlternative(
        key="narrow_time_range",
        title="缩小时间范围",
        hint="补充分区过滤条件（如 dt BETWEEN ...），只扫描必要分区",
    ),
)


def _format_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def build_degrade_prompt(
    *,
    tool_name: str,
    estimate: CostEstimate,
    consumed: float,
    total_budget: float,
    attempt: int,
    max_attempts: int,
    alternatives: Optional[Sequence[DegradeAlternative]] = None,
    extra_note: str = "",
) -> str:
    """生成阻断提示。字段顺序固定，便于 LLM 与日志稳定解析。"""
    remaining = max(total_budget - consumed, 0.0)
    deficit = max(estimate.cost - remaining, 0.0)
    chosen = tuple(alternatives) if alternatives is not None else DEFAULT_ALTERNATIVES

    payload: dict[str, Any] = {
        "type": "budget_blocked",
        "version": 1,
        "tool": tool_name,
        "status": "blocked",
        "estimated_cost": round(estimate.cost, 2),
        "cost_basis": estimate.basis,
        "cost_confidence": estimate.confidence,
        "consumed": round(consumed, 2),
        "total_budget": round(total_budget, 2),
        "remaining": round(remaining, 2),
        "deficit": round(deficit, 2),
        "degrade_attempt": attempt,
        "max_degrade_attempts": max_attempts,
        "alternatives": [alt.key for alt in chosen],
        "escalation": "halt" if attempt >= max_attempts else "retry_allowed",
    }

    lines = [
        f"{PROMPT_MARKER} 本次工具调用被预算策略阻止，未执行。",
        f"status: blocked",
        f"tool: {tool_name}",
        f"estimated_cost: {_format_number(estimate.cost)} ({estimate.basis}, confidence={estimate.confidence})",
        f"consumed: {_format_number(consumed)} / total_budget: {_format_number(total_budget)}",
        f"remaining: {_format_number(remaining)} | deficit: {_format_number(deficit)}",
        f"degrade_attempt: {attempt}/{max_attempts}",
    ]
    if estimate.note:
        lines.append(f"cost_note: {estimate.note}")
    if extra_note:
        lines.append(f"note: {extra_note}")

    lines.append("alternatives:")
    for alt in chosen:
        lines.append(f"  - {alt.key}: {alt.title} — {alt.hint}")
    lines.append(
        "plan_instruction: 不要重复提交同一调用；请从 alternatives 中选择一个成本更低的方案，"
        "或直接基于已获得的结果作答。若再次超预算且达到 max_degrade_attempts，本次运行会终止。"
    )
    lines.append(f"constraint: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)


def parse_degrade_prompt(text: str) -> dict[str, Any]:
    """从提示中取回末尾的 JSON 约束块（供宿主/测试校验，失败返回空 dict）。"""
    if not isinstance(text, str):
        return {}
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("constraint:"):
            try:
                return json.loads(stripped[len("constraint:") :].strip())
            except json.JSONDecodeError:
                return {}
    return {}