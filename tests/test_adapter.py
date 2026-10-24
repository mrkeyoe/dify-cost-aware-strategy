"""适配层测试：无 SDK 也能导入，schema 可构造，未接线时给出明确错误。

注意：这些测试**不能**证明 Dify 运行时集成可用（本机没有 dify_plugin）。
它们只覆盖"接线本身"——参数翻译、事件翻译、错误信息。
"""

from __future__ import annotations

import unittest

from core.loop import Plan, PlannedToolCall, ToolResult
from strategies import _sdk
from strategies.cost_aware import CostAwareAgentStrategy, IntegrationNotWired
from strategies.provider import CostAwareStrategyProvider
from strategies.schema import PARAMETER_SPECS, build_agent_strategy_config
from tests.fakes import FakeLLM, RecordingExecutor


class TestSdkShim(unittest.TestCase):
    def test_importable_without_sdk(self):
        # 本机预期值为 False；若将来装了 SDK 也应当能导入，不应报错。
        self.assertIn(_sdk.SDK_AVAILABLE, (True, False))
        self.assertTrue(hasattr(_sdk, "AgentStrategyBase"))

    def test_message_factories_return_something(self):
        text = _sdk.make_text_message("hi")
        payload = _sdk.make_json_message({"a": 1})
        self.assertTrue(text is not None)
        self.assertTrue(payload is not None)


class TestSchema(unittest.TestCase):
    def test_required_parameters_present(self):
        names = {spec["name"] for spec in PARAMETER_SPECS}
        for required in (
            "total_budget",
            "cost_overrides",
            "degrade_enabled",
            "max_degrade_attempts",
        ):
            self.assertIn(required, names)

    def test_every_parameter_has_labels(self):
        for spec in PARAMETER_SPECS:
            with self.subTest(spec=spec["name"]):
                self.assertIn("en_US", spec["label"])
                self.assertIn("zh_Hans", spec["label"])
                self.assertIn("en_US", spec["help"])

    def test_config_is_buildable(self):
        config = build_agent_strategy_config()
        self.assertGreaterEqual(len(config.parameters), 4)


class TestStrategyConstruction(unittest.TestCase):
    def test_constructs_without_sdk(self):
        strategy = CostAwareAgentStrategy()
        self.assertIsNotNone(strategy.get_provider_schema())

    def test_for_testing_injects_dependencies(self):
        strategy = CostAwareAgentStrategy.for_testing(FakeLLM([]), RecordingExecutor())
        self.assertIs(strategy._build_llm({}), strategy._llm)


class TestProvider(unittest.TestCase):
    def test_provider_lists_strategy(self):
        provider = CostAwareStrategyProvider()
        strategies = provider.get_strategies()
        self.assertEqual(len(strategies), 1)
        self.assertIs(strategies[0], CostAwareAgentStrategy)


class TestUnwiredInvoke(unittest.TestCase):
    def test_missing_bridges_raise_clear_error(self):
        strategy = CostAwareAgentStrategy()
        with self.assertRaises(IntegrationNotWired) as ctx:
            list(strategy._invoke({"total_budget": 10}))
        message = str(ctx.exception)
        self.assertIn("LLM", message)
        self.assertIn("dify_plugin", message)

    def test_missing_executor_also_raises(self):
        strategy = CostAwareAgentStrategy(llm=FakeLLM([]))
        with self.assertRaises(IntegrationNotWired) as ctx:
            list(strategy._invoke({"total_budget": 10}))
        self.assertIn("ToolExecutorProtocol", str(ctx.exception))


class TestWiredInvoke(unittest.TestCase):
    """注入 fake 后的端到端接线：参数 -> 拦截 -> 放行 -> 报告消息。"""

    def _strategy(self):
        llm = FakeLLM(
            [
                Plan(tool_calls=(PlannedToolCall("c1", "db.scan_query", {"sql": "select * from orders"}),)),
                Plan(tool_calls=(PlannedToolCall("c2", "db.sample_query", {"sql": "select * from orders limit 100"}),)),
                Plan(final_answer="done"),
            ]
        )
        executor = RecordingExecutor()
        strategy = CostAwareAgentStrategy.for_testing(llm, executor)
        parameters = {
            "query": "统计订单",
            # 预算 20：50 的扫描被拦，10 的采样放行（对应验收标准 1/2 的接线验证）
            "total_budget": 20,
            "cost_overrides": '{"db.scan_query": 50, "db.sample_query": 10}',
            "degrade_enabled": True,
        }
        return strategy, executor, parameters

    def test_messages_cover_block_allow_and_report(self):
        strategy, executor, parameters = self._strategy()
        messages = list(strategy._invoke(parameters))

        self.assertEqual([name for name, _ in executor.calls], ["db.sample_query"])

        text_blobs = [
            m.get("content") for m in messages if isinstance(m, dict) and m.get("type") == "text"
        ]
        json_blobs = [
            m.get("content") for m in messages if isinstance(m, dict) and m.get("type") == "json"
        ]
        joined = "\n".join(str(blob) for blob in text_blobs)
        self.assertIn("[BUDGET_CONSTRAINT]", joined)
        self.assertIn("[cost-aware] 放行 db.sample_query", joined)
        self.assertIn("成本报告 / cost report", joined)

        self.assertTrue(any(isinstance(b, dict) and b.get("action") == "degrade" for b in json_blobs))
        reports = [b for b in json_blobs if isinstance(b, dict) and "degraded" in b]
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0]["degraded"])
        self.assertEqual(reports[0]["consumed"], 10)

    def test_elapsed_seconds_is_filled(self):
        strategy, _, parameters = self._strategy()
        messages = list(strategy._invoke(parameters))
        reports = [
            m["content"]
            for m in messages
            if isinstance(m, dict)
            and m.get("type") == "json"
            and isinstance(m.get("content"), dict)
            and "degraded" in m["content"]
        ]
        self.assertIsNotNone(reports[0]["elapsed_seconds"])


class _ExplodingLLM:
    def plan(self, **kwargs):
        raise RuntimeError("llm offline")


class TestExceptionPath(unittest.TestCase):
    def test_partial_report_is_emitted_before_raising(self):
        strategy = CostAwareAgentStrategy.for_testing(_ExplodingLLM(), RecordingExecutor())
        collected = []
        with self.assertRaises(RuntimeError):
            for message in strategy._invoke({"total_budget": 50, "query": "q"}):
                collected.append(message)
        blobs = [m.get("content") for m in collected if isinstance(m, dict)]
        joined = "\n".join(str(b) for b in blobs)
        self.assertIn("运行异常", joined)
        self.assertIn("RuntimeError", joined)
        self.assertTrue(any(isinstance(b, dict) and "total_budget" in b for b in blobs))


if __name__ == "__main__":
    unittest.main()