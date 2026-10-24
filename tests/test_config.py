"""配置解析测试：类型转换、默认值、非法值。"""

from __future__ import annotations

import unittest

from core.config import ConfigurationError, StrategyConfig


class TestDefaults(unittest.TestCase):
    def test_defaults(self):
        config = StrategyConfig()
        self.assertEqual(config.total_budget, 100.0)
        self.assertEqual(config.max_degrade_attempts, 3)
        self.assertTrue(config.degrade_enabled)
        self.assertEqual(config.cost_overrides, {})
        self.assertEqual(config.unknown_cost, 40.0)
        self.assertEqual(config.default_tool_cost, 15.0)
        self.assertEqual(config.max_iterations, 8)


class TestFromParameters(unittest.TestCase):
    def test_numbers_and_bools_are_coerced(self):
        config = StrategyConfig.from_parameters(
            {
                "total_budget": "50",
                "max_degrade_attempts": "2",
                "degrade_enabled": False,
                "unknown_cost": 25,
            }
        )
        self.assertEqual(config.total_budget, 50.0)
        self.assertEqual(config.max_degrade_attempts, 2)
        self.assertFalse(config.degrade_enabled)
        self.assertEqual(config.unknown_cost, 25.0)

    def test_bool_strings(self):
        for raw, expected in (("true", True), ("false", False), ("1", True), ("0", False)):
            with self.subTest(raw=raw):
                config = StrategyConfig.from_parameters({"degrade_enabled": raw})
                self.assertEqual(config.degrade_enabled, expected)

    def test_json_strings_are_parsed(self):
        config = StrategyConfig.from_parameters(
            {
                "cost_overrides": '{"db.scan": 42}',
                "database_tools": '["db.query"]',
                "table_metadata": '{"orders": {"row_count": 1000}}',
            }
        )
        self.assertEqual(config.cost_overrides, {"db.scan": 42.0})
        self.assertEqual(config.database_tools, frozenset({"db.query"}))
        self.assertIn("orders", config.metadata.table_names())

    def test_unknown_keys_are_ignored(self):
        config = StrategyConfig.from_parameters({"total_budget": 10, "wat": "x"})
        self.assertEqual(config.total_budget, 10.0)

    def test_empty_and_none(self):
        self.assertEqual(StrategyConfig.from_parameters(None), StrategyConfig())
        self.assertEqual(StrategyConfig.from_parameters({}), StrategyConfig())

    def test_invalid_numbers_raise(self):
        for bad in ({"total_budget": "abc"}, {"total_budget": 0}, {"total_budget": -5}):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigurationError):
                    StrategyConfig.from_parameters(bad)

    def test_invalid_json_raises(self):
        with self.assertRaises(ConfigurationError):
            StrategyConfig.from_parameters({"cost_overrides": "{not json"})

    def test_invalid_bool_raises(self):
        with self.assertRaises(ConfigurationError):
            StrategyConfig.from_parameters({"degrade_enabled": "maybe"})

    def test_negative_attempts_raise(self):
        with self.assertRaises(ConfigurationError):
            StrategyConfig.from_parameters({"max_degrade_attempts": -1})


class TestValidateAndReplace(unittest.TestCase):
    def test_validate_catches_bad_values(self):
        with self.assertRaises(ConfigurationError):
            StrategyConfig(total_budget=0).validate()
        with self.assertRaises(ConfigurationError):
            StrategyConfig(cost_overrides={"db": -1}).validate()

    def test_replace_returns_new_instance(self):
        base = StrategyConfig(total_budget=100)
        changed = base.replace(total_budget=10)
        self.assertEqual(base.total_budget, 100.0)
        self.assertEqual(changed.total_budget, 10.0)

    def test_frozen(self):
        config = StrategyConfig()
        with self.assertRaises(Exception):
            config.total_budget = 5  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()