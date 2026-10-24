"""元数据注册表测试。"""

from __future__ import annotations

import unittest

from core.metadata import MetadataRegistry, TableMetadata


class TestTableMetadata(unittest.TestCase):
    def test_basic_fields(self):
        table = TableMetadata("orders", 1_000_000, indexed_columns=("id",), partition_column="dt")
        self.assertEqual(table.row_count, 1_000_000)
        self.assertTrue(table.is_indexed("id"))
        self.assertFalse(table.is_indexed("status"))
        self.assertTrue(table.is_partition_column("dt"))
        self.assertTrue(table.partitioned)

    def test_negative_rows_rejected(self):
        with self.assertRaises(ValueError):
            TableMetadata("orders", -1)

    def test_to_dict(self):
        payload = TableMetadata("orders", 10, indexed_columns=("id",)).to_dict()
        self.assertEqual(payload["name"], "orders")
        self.assertEqual(payload["row_count"], 10)
        self.assertIn("indexed_columns", payload)
        self.assertIn("partition_column", payload)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = MetadataRegistry(
            [
                TableMetadata("orders", 1_000_000, indexed_columns=("id",)),
                TableMetadata("dim_user", 1_000),
            ]
        )

    def test_get_is_case_insensitive(self):
        self.assertIsNotNone(self.registry.get("ORDERS"))
        self.assertIsNotNone(self.registry.get("Orders"))

    def test_schema_prefix_is_ignored(self):
        self.assertIsNotNone(self.registry.get("analytics.orders"))
        self.assertIsNotNone(self.registry.get("`Analytics`.`Orders`"))

    def test_missing_returns_none(self):
        self.assertIsNone(self.registry.get("nope"))
        self.assertIsNone(self.registry.get(""))

    def test_register_and_contains(self):
        self.registry.register(TableMetadata("events", 500))
        self.assertIn("events", self.registry)
        self.assertEqual(len(self.registry), 3)
        self.assertIn("events", self.registry.table_names())

    def test_duplicate_register_overrides(self):
        self.registry.register(TableMetadata("orders", 5))
        self.assertEqual(self.registry.get("orders").row_count, 5)

    def test_from_config_accepts_dict(self):
        registry = MetadataRegistry.from_config(
            {"orders": {"row_count": 100, "indexed_columns": ["id"], "partition_column": "dt"}}
        )
        table = registry.get("orders")
        self.assertEqual(table.row_count, 100)
        self.assertTrue(table.is_indexed("id"))
        self.assertTrue(table.is_partition_column("dt"))

    def test_from_config_accepts_list(self):
        registry = MetadataRegistry.from_config(
            [{"name": "orders", "row_count": 10}, {"name": "dim", "row_count": 1}]
        )
        self.assertEqual(len(registry), 2)

    def test_from_config_accepts_json_string(self):
        registry = MetadataRegistry.from_config('{"orders": {"row_count": 7}}')
        self.assertEqual(registry.get("orders").row_count, 7)

    def test_from_config_skips_invalid_entries(self):
        registry = MetadataRegistry.from_config(
            {"ok": {"row_count": 1}, "bad_rows": {"row_count": -3}, "no_rows": {}}
        )
        self.assertEqual(list(registry.table_names()), ["ok"])

    def test_from_config_handles_none_and_garbage(self):
        for raw in (None, "", "{", 42, [], "[]"):
            with self.subTest(raw=raw):
                self.assertEqual(len(MetadataRegistry.from_config(raw)), 0)


if __name__ == "__main__":
    unittest.main()