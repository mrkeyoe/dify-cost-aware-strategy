"""成本报告测试。"""

from __future__ import annotations

import json
import unittest

from core.report import CostReport, DegradeEvent


def _event(**overrides):
    payload = dict(
        tool_name="db.scan_query",
        estimated_cost=50.0,
        consumed_at_block=80.0,
        attempt=1,
        max_attempts=3,
        reason_code="budget_exceeded",
        reason_text="db.scan_query 估算成本 50.00（override）超出剩余预算 20.00，已阻止执行并要求重新规划",
        cost_basis="override",
    )
    payload.update(overrides)
    return DegradeEvent(**payload)


class TestEmptyReport(unittest.TestCase):
    def test_normal_run(self):
        report = CostReport(total_budget=100, consumed=90, calls_allowed=1)
        self.assertFalse(report.degraded)
        self.assertEqual(report.degrade_count, 0)
        self.assertEqual(report.remaining, 10)
        payload = report.to_dict()
        self.assertFalse(payload["degraded"])
        self.assertEqual(payload["degrade_reasons"], [])
        self.assertEqual(payload["usage_ratio"], 0.9)
        self.assertIn("status: normal", report.to_text())
        self.assertNotIn("degrade_reasons", report.to_text())

    def test_remaining_never_negative(self):
        report = CostReport(total_budget=10, consumed=99)
        self.assertEqual(report.remaining, 0)


class TestDegradedReport(unittest.TestCase):
    def test_degraded_flag_and_reasons(self):
        report = CostReport(
            total_budget=100,
            consumed=90,
            degrade_events=(_event(),),
            calls_allowed=1,
            calls_blocked=1,
        )
        self.assertTrue(report.degraded)
        payload = report.to_dict()
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["degrade_count"], 1)
        self.assertEqual(len(payload["degrade_reasons"]), 1)
        self.assertIn("db.scan_query", payload["degrade_reasons"][0])
        self.assertEqual(payload["degrade_events"][0]["tool"], "db.scan_query")
        self.assertEqual(payload["degrade_events"][0]["cost_basis"], "override")

    def test_to_text_lists_each_reason(self):
        report = CostReport(
            total_budget=100,
            consumed=90,
            degrade_events=(_event(), _event(attempt=2, tool_name="db.other")),
        )
        text = report.to_text()
        self.assertIn("status: degraded", text)
        self.assertIn("[budget_exceeded] db.scan_query", text)
        self.assertIn("[budget_exceeded] db.other", text)
        self.assertIn("attempt 2/3", text)

    def test_to_json_is_parseable_and_chinese_preserved(self):
        report = CostReport(total_budget=100, consumed=80, degrade_events=(_event(),))
        parsed = json.loads(report.to_json())
        self.assertTrue(parsed["degraded"])
        self.assertIn("超出剩余预算", parsed["degrade_reasons"][0])

    def test_halted_and_failed_fields(self):
        report = CostReport(
            total_budget=10,
            consumed=3,
            halted=True,
            halt_reason="degrade_limit_reached",
            calls_failed=1,
            tool_costs={"db.query": 3},
            elapsed_seconds=0.1234,
        )
        payload = report.to_dict()
        self.assertTrue(payload["halted"])
        self.assertEqual(payload["halt_reason"], "degrade_limit_reached")
        self.assertEqual(payload["calls_failed"], 1)
        self.assertEqual(payload["tool_costs"], {"db.query": 3})
        self.assertEqual(payload["elapsed_seconds"], 0.123)
        self.assertIn("- halted: degrade_limit_reached", report.to_text())


class TestDegradeEvent(unittest.TestCase):
    def test_rounding_and_keys(self):
        payload = _event(estimated_cost=50.005123, consumed_at_block=79.999).to_dict()
        self.assertEqual(
            set(payload),
            {
                "tool",
                "estimated_cost",
                "consumed_at_block",
                "attempt",
                "max_attempts",
                "reason_code",
                "reason",
                "cost_basis",
            },
        )
        self.assertEqual(payload["estimated_cost"], 50.01)
        self.assertEqual(payload["consumed_at_block"], 80.0)

    def test_frozen(self):
        event = _event()
        with self.assertRaises(Exception):
            event.attempt = 2  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()