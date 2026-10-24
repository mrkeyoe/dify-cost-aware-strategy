"""预算策略状态机测试：放行/降级/终止、连续次数与报告。"""

from __future__ import annotations

import unittest

from core.config import StrategyConfig
from core.estimator import CostEstimator
from core.policy import (
    REASON_BUDGET_EXCEEDED,
    REASON_DEGRADE_DISABLED,
    REASON_DEGRADE_LIMIT,
    CostAwarePolicy,
    DecisionAction,
)
from core.degrade import parse_degrade_prompt
from core.report import CostReport


def _policy(**overrides) -> CostAwarePolicy:
    config = StrategyConfig(**overrides)
    return CostAwarePolicy(config, CostEstimator(config))


class TestAllowPath(unittest.TestCase):
    def test_allow_charges_and_reports(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 30})
        decision = policy.before_call("db.query", {"sql": "select 1"})
        self.assertIs(decision.action, DecisionAction.ALLOW)
        self.assertEqual(decision.estimated_cost, 30)
        self.assertEqual(decision.consumed_before, 0)
        self.assertEqual(decision.consumed_after, 30)
        self.assertEqual(decision.reason_code, "within_budget")
        self.assertEqual(policy.consumed, 30)
        self.assertEqual(policy.remaining, 70)

    def test_can_spend_exactly_to_budget(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 100})
        self.assertIs(policy.before_call("db.query", {}).action, DecisionAction.ALLOW)
        self.assertEqual(policy.remaining, 0)

    def test_tool_costs_are_accumulated(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 10})
        policy.before_call("db.query", {})
        policy.before_call("db.query", {})
        report = policy.report()
        self.assertEqual(report.tool_costs, {"db.query": 20})
        self.assertEqual(report.calls_allowed, 2)


class TestDegradePath(unittest.TestCase):
    def test_over_budget_degrades_without_charging(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 50})
        policy.consume(80)
        decision = policy.before_call("db.query", {"sql": "select * from orders"})
        self.assertIs(decision.action, DecisionAction.DEGRADE)
        self.assertEqual(decision.reason_code, REASON_BUDGET_EXCEEDED)
        self.assertEqual(policy.consumed, 80)  # 未执行、未记账
        self.assertEqual(decision.remaining_before, 20)
        self.assertIn("deficit: 30", decision.degrade_prompt or "")

    def test_degrade_prompt_is_structured(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 50})
        policy.consume(80)
        decision = policy.before_call("db.query", {})
        payload = parse_degrade_prompt(decision.degrade_prompt or "")
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["tool"], "db.query")
        self.assertEqual(payload["remaining"], 20.0)

    def test_degrade_streak_counts_and_reports(self):
        policy = _policy(total_budget=10, cost_overrides={"db.query": 20}, max_degrade_attempts=3)
        first = policy.before_call("db.query", {})
        second = policy.before_call("db.query", {})
        self.assertEqual((first.degrade_attempt, second.degrade_attempt), (1, 2))
        report = policy.report()
        self.assertEqual(report.degrade_count, 2)
        self.assertTrue(report.degraded)
        self.assertEqual(report.calls_blocked, 2)

    def test_success_resets_streak(self):
        policy = _policy(
            total_budget=100,
            cost_overrides={"db.expensive": 60, "db.cheap": 10},
            max_degrade_attempts=5,
        )
        self.assertIs(policy.before_call("db.expensive", {}).action, DecisionAction.ALLOW)  # 60
        first = policy.before_call("db.expensive", {})  # 60+60 > 100 -> degrade #1
        self.assertIs(first.action, DecisionAction.DEGRADE)
        self.assertEqual(first.degrade_attempt, 1)
        self.assertIs(policy.before_call("db.cheap", {}).action, DecisionAction.ALLOW)  # 70
        self.assertEqual(policy.degrade_streak, 0)
        second = policy.before_call("db.expensive", {})  # 70+60 > 100 -> degrade #1 again
        self.assertEqual(second.degrade_attempt, 1)


class TestHaltPaths(unittest.TestCase):
    def test_degrade_limit_halts_to_prevent_loop(self):
        policy = _policy(total_budget=10, cost_overrides={"db.query": 20}, max_degrade_attempts=2)
        actions = [policy.before_call("db.query", {}).action for _ in range(4)]
        self.assertEqual(
            [a.value for a in actions],
            ["degrade", "degrade", "halt", "halt"],
        )
        self.assertTrue(policy.halted)
        self.assertEqual(policy.halt_reason, REASON_DEGRADE_LIMIT)
        self.assertIs(policy.before_call("db.query", {}).action, DecisionAction.HALT)

    def test_zero_max_attempts_halts_immediately(self):
        policy = _policy(total_budget=10, cost_overrides={"db.query": 20}, max_degrade_attempts=0)
        decision = policy.before_call("db.query", {})
        self.assertIs(decision.action, DecisionAction.HALT)
        self.assertEqual(decision.reason_code, REASON_DEGRADE_LIMIT)
        self.assertEqual(policy.report().degrade_count, 0)

    def test_degrade_disabled_halts_instead_of_prompting(self):
        policy = _policy(
            total_budget=10, cost_overrides={"db.query": 20}, degrade_enabled=False
        )
        decision = policy.before_call("db.query", {})
        self.assertIs(decision.action, DecisionAction.HALT)
        self.assertEqual(decision.reason_code, REASON_DEGRADE_DISABLED)
        self.assertIsNone(decision.degrade_prompt)

    def test_external_halt_is_idempotent(self):
        policy = _policy(total_budget=100)
        policy.halt("max_iterations_reached")
        decision = policy.before_call("db.query", {})
        self.assertIs(decision.action, DecisionAction.HALT)
        self.assertEqual(decision.reason_code, "max_iterations_reached")
        self.assertEqual(policy.report().calls_blocked, 0)


class TestFailureRefund(unittest.TestCase):
    def test_failed_tool_refunds_budget(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 30})
        decision = policy.before_call("db.query", {})
        self.assertEqual(policy.consumed, 30)
        policy.on_tool_failure(decision, error="timeout")
        self.assertEqual(policy.consumed, 0)
        report = policy.report()
        self.assertEqual(report.calls_failed, 1)
        self.assertEqual(report.tool_costs, {})


class TestReport(unittest.TestCase):
    def test_report_shape(self):
        policy = _policy(total_budget=100, cost_overrides={"db.query": 50})
        policy.consume(80)
        policy.before_call("db.query", {})
        report = policy.report(elapsed_seconds=0.125)
        self.assertIsInstance(report, CostReport)
        payload = report.to_dict()
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["degrade_count"], 1)
        self.assertEqual(payload["consumed"], 80)
        self.assertEqual(payload["total_budget"], 100)
        self.assertEqual(payload["remaining"], 20)
        self.assertEqual(payload["elapsed_seconds"], 0.125)
        self.assertIn("db.query", payload["degrade_events"][0]["tool"])
        self.assertTrue(payload["degrade_reasons"][0])


if __name__ == "__main__":
    unittest.main()