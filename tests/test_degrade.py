"""结构化降级提示测试。"""

from __future__ import annotations

import unittest

from core.degrade import (
    DEFAULT_ALTERNATIVES,
    DegradeAlternative,
    PROMPT_MARKER,
    build_degrade_prompt,
    parse_degrade_prompt,
)
from core.estimator import CostEstimate


def _estimate(cost: float = 50.0, basis: str = "override") -> CostEstimate:
    return CostEstimate(
        tool_name="db.scan_query",
        cost=cost,
        basis=basis,
        confidence="high",
        note="测试用估算",
    )


class TestDegradePrompt(unittest.TestCase):
    def _prompt(self, **overrides):
        kwargs = dict(
            tool_name="db.scan_query",
            estimate=_estimate(),
            consumed=80.0,
            total_budget=100.0,
            attempt=1,
            max_attempts=3,
        )
        kwargs.update(overrides)
        return build_degrade_prompt(**kwargs)

    def test_contains_required_fields(self):
        text = self._prompt()
        self.assertIn(PROMPT_MARKER, text)
        for field in (
            "status: blocked",
            "tool: db.scan_query",
            "estimated_cost: 50",
            "consumed: 80 / total_budget: 100",
            "remaining: 20 | deficit: 30",
            "degrade_attempt: 1/3",
            "plan_instruction:",
            "alternatives:",
        ):
            self.assertIn(field, text)

    def test_contains_three_alternatives(self):
        text = self._prompt()
        for alt in DEFAULT_ALTERNATIVES:
            self.assertIn(f"  - {alt.key}:", text)
        self.assertIn("采样查询", text)
        self.assertIn("聚合表", text)
        self.assertIn("缩小时间范围", text)

    def test_machine_readable_json_block(self):
        payload = parse_degrade_prompt(self._prompt())
        self.assertEqual(payload["type"], "budget_blocked")
        self.assertEqual(payload["tool"], "db.scan_query")
        self.assertEqual(payload["estimated_cost"], 50.0)
        self.assertEqual(payload["remaining"], 20.0)
        self.assertEqual(payload["deficit"], 30.0)
        self.assertEqual(payload["degrade_attempt"], 1)
        self.assertEqual(payload["escalation"], "retry_allowed")
        self.assertEqual(payload["alternatives"], ["sample", "preaggregate", "narrow_time_range"])

    def test_escalation_when_attempts_exhausted(self):
        payload = parse_degrade_prompt(self._prompt(attempt=3, max_attempts=3))
        self.assertEqual(payload["escalation"], "halt")

    def test_custom_alternatives_replace_defaults(self):
        text = self._prompt(
            alternatives=(DegradeAlternative(key="custom", title="查缓存", hint="先读缓存"),)
        )
        self.assertIn("  - custom: 查缓存", text)
        self.assertNotIn("  - sample:", text)

    def test_extra_note_and_cost_note_are_rendered(self):
        text = self._prompt(extra_note="宿主补充说明")
        self.assertIn("cost_note: 测试用估算", text)
        self.assertIn("note: 宿主补充说明", text)

    def test_parse_rejects_plain_text(self):
        self.assertEqual(parse_degrade_prompt("hello"), {})
        self.assertEqual(parse_degrade_prompt(None), {})  # type: ignore[arg-type]
        self.assertEqual(parse_degrade_prompt("constraint: {bad json}"), {})
        self.assertEqual(parse_degrade_prompt("constraint: {\"a\": 1}"), {"a": 1})


if __name__ == "__main__":
    unittest.main()