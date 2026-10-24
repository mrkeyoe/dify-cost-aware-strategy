"""三条验收标准的可执行版本（策略层 + 循环层）。

验收标准原文：
1. 配置 total_budget=100，已消耗 80 后，再尝试调用成本 50 的查询 → 阻止执行并注入降级提示；
2. 降级提示触发后，LLM 选择成本 10 的采样查询 → 放行并累计 consumed=90；
3. 运行结束报告含 degraded=true 与具体降级原因。
"""

from __future__ import annotations

import unittest

from core.config import StrategyConfig
from core.degrade import parse_degrade_prompt
from core.estimator import CostEstimator
from core.loop import (
    BudgetBlockedEvent,
    CostAwareAgentLoop,
    HaltEvent,
    Plan,
    PlannedToolCall,
    ReportEvent,
    TextEvent,
    ToolExecutedEvent,
)
from core.policy import CostAwarePolicy, DecisionAction, REASON_DEGRADE_LIMIT
from tests.fakes import FakeLLM, RecordingExecutor

BUDGET = 100
CONSUMED_BEFORE = 80
SCAN_COST = 50
SAMPLE_COST = 10


def _config(**overrides) -> StrategyConfig:
    defaults = dict(
        total_budget=BUDGET,
        cost_overrides={"db.scan_query": SCAN_COST, "db.sample_query": SAMPLE_COST},
        degrade_enabled=True,
        max_degrade_attempts=3,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


class TestCriterion1BlockAndInject(unittest.TestCase):
    def test_over_budget_call_is_blocked_and_prompt_injected(self):
        policy = CostAwarePolicy(_config(), CostEstimator(_config()))
        policy.consume(CONSUMED_BEFORE)

        decision = policy.before_call("db.scan_query", {"sql": "select * from orders"})

        self.assertIs(decision.action, DecisionAction.DEGRADE)
        self.assertEqual(decision.estimated_cost, SCAN_COST)
        self.assertEqual(policy.consumed, CONSUMED_BEFORE, "被拦下的调用不得计费")
        prompt = decision.degrade_prompt or ""
        payload = parse_degrade_prompt(prompt)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["tool"], "db.scan_query")
        self.assertEqual(payload["estimated_cost"], SCAN_COST)
        self.assertEqual(payload["remaining"], 20.0)
        for word in ("采样查询", "聚合表", "缩小时间范围"):
            self.assertIn(word, prompt)


class TestCriterion2SamplerIsAllowed(unittest.TestCase):
    def test_sampler_after_degrade_is_allowed_and_accumulates(self):
        policy = CostAwarePolicy(_config(), CostEstimator(_config()))
        policy.consume(CONSUMED_BEFORE)

        blocked = policy.before_call("db.scan_query", {"sql": "select * from orders"})
        self.assertIs(blocked.action, DecisionAction.DEGRADE)

        allowed = policy.before_call("db.sample_query", {"sql": "select * from orders limit 100"})
        self.assertIs(allowed.action, DecisionAction.ALLOW)
        self.assertEqual(allowed.estimated_cost, SAMPLE_COST)
        self.assertEqual(policy.consumed, 90)
        self.assertEqual(policy.remaining, 10)


class TestCriterion3Report(unittest.TestCase):
    def test_report_contains_degraded_flag_and_reason(self):
        policy = CostAwarePolicy(_config(), CostEstimator(_config()))
        policy.consume(CONSUMED_BEFORE)
        policy.before_call("db.scan_query", {"sql": "select * from orders"})
        policy.before_call("db.sample_query", {"sql": "select * from orders limit 100"})

        report = policy.report(elapsed_seconds=0.01)
        payload = report.to_dict()

        self.assertTrue(payload["degraded"])
        self.assertTrue(payload["degrade_reasons"])
        self.assertIn("db.scan_query", payload["degrade_reasons"][0])
        self.assertIn("50", payload["degrade_reasons"][0])
        self.assertEqual(payload["degrade_events"][0]["reason_code"], "budget_exceeded")
        self.assertEqual(payload["total_budget"], BUDGET)
        self.assertEqual(payload["consumed"], 90)
        self.assertIn("status: degraded", report.to_text())


class TestEndToEndLoopAcceptance(unittest.TestCase):
    """把三条标准串成一次完整运行：计划 -> 拦截 -> 重规划 -> 放行 -> 报告。"""

    def _run(self):
        llm = FakeLLM(
            [
                Plan(tool_calls=(PlannedToolCall("c1", "db.scan_query", {"sql": "select * from orders"}),)),
                Plan(tool_calls=(PlannedToolCall("c2", "db.sample_query", {"sql": "select * from orders limit 100"}),)),
                Plan(final_answer="基于 100 行样本，结论是 X"),
            ]
        )
        executor = RecordingExecutor()
        config = _config()
        policy = CostAwarePolicy(config, CostEstimator(config))
        policy.consume(CONSUMED_BEFORE)
        events = list(CostAwareAgentLoop(policy, llm, executor).run("统计订单分布"))
        return llm, executor, events

    def test_full_run(self):
        llm, executor, events = self._run()

        # 标准 1：昂贵的全表扫描从未真正执行
        self.assertEqual([name for name, _ in executor.calls], ["db.sample_query"])
        blocked = [e for e in events if isinstance(e, BudgetBlockedEvent)]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].decision.tool_name, "db.scan_query")
        self.assertIn("[BUDGET_CONSTRAINT]", blocked[0].prompt)

        # 注入的提示确实进入了下一轮规划
        self.assertIsNone(llm.calls[0]["cost_hint"])
        self.assertIn("[BUDGET_CONSTRAINT]", llm.calls[1]["cost_hint"] or "")
        self.assertEqual(llm.calls[1]["remaining_budget"], 20)

        # 标准 2：采样查询被放行且累计到 90
        executed = [e for e in events if isinstance(e, ToolExecutedEvent)]
        self.assertEqual([e.call.tool_name for e in executed], ["db.sample_query"])
        self.assertEqual(executed[0].decision.consumed_after, 90)

        # 标准 3：报告含 degraded=true 与具体原因
        report_events = [e for e in events if isinstance(e, ReportEvent)]
        self.assertEqual(len(report_events), 1)
        report = report_events[0].report
        self.assertTrue(report.degraded)
        self.assertEqual(report.consumed, 90)
        self.assertEqual(report.degrade_count, 1)
        self.assertIn("db.scan_query", report.degrade_events[0].reason_text)
        self.assertEqual(report.calls_allowed, 1)
        self.assertEqual(report.calls_blocked, 1)

        self.assertTrue(any(isinstance(e, TextEvent) for e in events))
        self.assertFalse(any(isinstance(e, HaltEvent) for e in events))

    def test_events_order_is_stable(self):
        _, _, events = self._run()
        # 具体顺序：blocked -> executed -> text -> report
        names = [type(e).__name__ for e in events]
        self.assertEqual(
            names,
            ["BudgetBlockedEvent", "ToolExecutedEvent", "TextEvent", "ReportEvent"],
        )


class TestDegradeLoopGuard(unittest.TestCase):
    def test_repeated_over_budget_calls_halt_instead_of_looping(self):
        expensive = Plan(
            tool_calls=(PlannedToolCall("c1", "db.scan_query", {"sql": "select * from orders"}),)
        )
        llm = FakeLLM([expensive] * 6)
        executor = RecordingExecutor()
        config = _config(total_budget=10, max_degrade_attempts=2)
        policy = CostAwarePolicy(config, CostEstimator(config))

        events = list(CostAwareAgentLoop(policy, llm, executor, max_iterations=6).run("q"))

        self.assertEqual(executor.calls, [], "超预算工具一次都不能执行")
        blocked = [e for e in events if isinstance(e, BudgetBlockedEvent)]
        halts = [e for e in events if isinstance(e, HaltEvent)]
        self.assertEqual(len(blocked), 2)
        self.assertEqual(len(halts), 1)
        self.assertEqual(halts[0].reason_code, REASON_DEGRADE_LIMIT)
        self.assertIn("连续 2 次降级", halts[0].reason_text)
        self.assertIn("本次运行终止", halts[0].reason_text)

        report = [e for e in events if isinstance(e, ReportEvent)][0].report
        self.assertTrue(report.halted)
        self.assertEqual(report.halt_reason, REASON_DEGRADE_LIMIT)
        self.assertEqual(report.degrade_count, 2)
        self.assertEqual(len(llm.calls), 3, "降级 2 次 + 终止那一轮，不应无限重规划")


class TestIterationGuard(unittest.TestCase):
    def test_max_iterations_stops_run(self):
        cheap = Plan(tool_calls=(PlannedToolCall("c", "db.cheap", {"sql": "select 1 from dim"}),))
        llm = FakeLLM([cheap] * 10)
        executor = RecordingExecutor()
        config = _config(cost_overrides={"db.cheap": 1}, max_iterations=3)
        policy = CostAwarePolicy(config, CostEstimator(config))

        events = list(CostAwareAgentLoop(policy, llm, executor, max_iterations=3).run("q"))
        report = [e for e in events if isinstance(e, ReportEvent)][0].report

        self.assertEqual(len(executor.calls), 3)
        self.assertTrue(report.halted)
        self.assertEqual(report.halt_reason, "max_iterations_reached")
        self.assertEqual(report.consumed, 3)

    def test_failed_tool_refunds_in_loop(self):
        call = Plan(tool_calls=(PlannedToolCall("c", "db.query", {"sql": "select * from orders"}),))
        llm = FakeLLM([call, Plan(final_answer="fallback")])
        executor = RecordingExecutor(ok=False, content="", error="timeout")
        config = _config(cost_overrides={"db.query": 30})
        policy = CostAwarePolicy(config, CostEstimator(config))

        events = list(CostAwareAgentLoop(policy, llm, executor).run("q"))
        report = [e for e in events if isinstance(e, ReportEvent)][0].report
        self.assertEqual(report.consumed, 0)
        self.assertEqual(report.calls_failed, 1)


if __name__ == "__main__":
    unittest.main()