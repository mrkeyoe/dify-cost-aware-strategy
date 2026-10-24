"""成本估算器测试：单调性、可比较性、未知信号与覆盖优先级。"""

from __future__ import annotations

import unittest

from core.config import StrategyConfig
from core.estimator import CostEstimator
from core.metadata import MetadataRegistry, TableMetadata


def _registry() -> MetadataRegistry:
    return MetadataRegistry(
        {
            "orders": TableMetadata(
                name="orders",
                row_count=1_000_000,
                indexed_columns=("id", "user_id"),
                partition_column="dt",
            ),
            "small_dim": TableMetadata(name="small_dim", row_count=1_000),
        }
    )


def _estimator(**overrides) -> CostEstimator:
    config = StrategyConfig(**overrides)
    return CostEstimator(config, _registry())


class TestMonotonicity(unittest.TestCase):
    """行数越大成本越高；同一查询形状下必须严格单调。"""

    def test_cost_increases_with_row_count(self):
        estimator = _estimator()
        costs = []
        for name, rows in (("t_small", 1_000), ("t_mid", 1_000_000), ("t_huge", 1_000_000_000)):
            estimator._registry.register(TableMetadata(name=name, row_count=rows))  # noqa: SLF001
            estimate = estimator.estimate("db.query", {"sql": f"select * from {name}"})
            self.assertEqual(estimate.basis, "table_rows")
            costs.append(estimate.cost)

        self.assertLess(costs[0], costs[1])
        self.assertLess(costs[1], costs[2])

    def test_same_input_is_deterministic(self):
        estimator = _estimator()
        payload = {"sql": "select id from orders where id = 1"}
        first = estimator.estimate("db.query", payload)
        second = estimator.estimate("db.query", dict(payload))
        self.assertEqual(first.cost, second.cost)
        self.assertEqual(first.basis, second.basis)


class TestSelectivity(unittest.TestCase):
    """访问方式的可比较性：索引等值 < 分区裁剪 < 普通等值 < 无过滤全扫。"""

    def test_access_paths_are_ordered(self):
        estimator = _estimator()
        indexed = estimator.estimate(
            "db.query", {"sql": "select id from orders where id = 42"}
        )
        partitioned = estimator.estimate(
            "db.query", {"sql": "select id from orders where dt = '2026-01-01'"}
        )
        unindexed = estimator.estimate(
            "db.query", {"sql": "select id from orders where status = 'paid'"}
        )
        full_scan = estimator.estimate("db.query", {"sql": "select * from orders"})

        self.assertLess(indexed.cost, partitioned.cost)
        self.assertLess(partitioned.cost, unindexed.cost)
        self.assertLess(unindexed.cost, full_scan.cost)
        self.assertIn("索引", indexed.note)
        self.assertIn("分区", partitioned.note)

    def test_limit_reduces_output_cost_for_plain_select(self):
        estimator = _estimator()
        unbounded = estimator.estimate("db.query", {"sql": "select * from orders"})
        sampled = estimator.estimate("db.query", {"sql": "select * from orders limit 100"})
        self.assertLess(sampled.cost, unbounded.cost)

    def test_aggregate_does_not_get_limit_discount(self):
        """count(*) 仍然要扫全表：LIMIT 不能把聚合成本打折（诚实取舍）。"""
        estimator = _estimator()
        aggregate = estimator.estimate("db.query", {"sql": "select count(*) from orders limit 100"})
        full_scan = estimator.estimate("db.query", {"sql": "select count(*) from orders"})
        self.assertEqual(aggregate.cost, full_scan.cost)

    def test_small_table_is_cheaper_than_large_table(self):
        estimator = _estimator()
        small = estimator.estimate("db.query", {"sql": "select * from small_dim"})
        large = estimator.estimate("db.query", {"sql": "select * from orders"})
        self.assertLess(small.cost, large.cost)


class TestUnknownAndStatic(unittest.TestCase):
    def test_unknown_table_yields_high_cost_signal(self):
        estimator = _estimator()
        estimate = estimator.estimate("db.query", {"sql": "select * from not_registered"})
        self.assertTrue(estimate.is_unknown)
        self.assertEqual(estimate.cost, 40.0)
        self.assertEqual(estimate.confidence, "low")

    def test_database_tool_without_table_yields_unknown(self):
        estimator = _estimator()
        estimate = estimator.estimate("db.query", {"sql": "select 1"})
        self.assertTrue(estimate.is_unknown)
        self.assertEqual(estimate.cost, 40.0)

    def test_non_database_tool_uses_static_weight(self):
        estimator = _estimator(default_tool_cost=7.5)
        estimate = estimator.estimate("web_search", {"query": "hello"})
        self.assertEqual(estimate.cost, 7.5)
        self.assertEqual(estimate.basis, "static")

    def test_cost_overrides_win_over_everything(self):
        estimator = _estimator(cost_overrides={"db.query": 55.0, "web_search": 3.0})
        self.assertEqual(estimator.estimate("db.query", {"sql": "select * from orders"}).cost, 55.0)
        self.assertEqual(estimator.estimate("web_search", {}).cost, 3.0)
        self.assertEqual(estimator.estimate("web_search", {}).basis, "override")

    def test_database_detection_by_name_and_sql_text(self):
        estimator = _estimator()
        self.assertTrue(estimator.is_database_tool("mysql.query", {}))
        self.assertTrue(estimator.is_database_tool("anything", {"query": "SELECT * FROM t"}))
        # 通用 query 字段里的 "select 1" 没有 FROM，不足以判定为数据库工具
        self.assertFalse(estimator.is_database_tool("anything", {"query": "SELECT 1"}))
        self.assertFalse(estimator.is_database_tool("web_search", {"query": "SELECT 1"}))
        self.assertTrue(estimator.is_database_tool("custom", {"sql": "select 1"}))

    def test_explicit_database_tool_list(self):
        estimator = _estimator(database_tools=frozenset({"custom.tool"}))
        self.assertTrue(estimator.is_database_tool("custom.tool", {}))
        estimate = estimator.estimate("custom.tool", {"sql": "select * from orders"})
        self.assertEqual(estimate.basis, "table_rows")

    def test_estimate_serializes(self):
        estimate = _estimator().estimate("db.query", {"sql": "select id from orders where id = 1"})
        payload = estimate.to_dict()
        self.assertEqual(set(payload), {"tool_name", "cost", "basis", "confidence", "factors", "table", "note"})
        self.assertIsInstance(payload["cost"], float)


if __name__ == "__main__":
    unittest.main()