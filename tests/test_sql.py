"""SQL 形状解析的单元测试。"""

from __future__ import annotations

import unittest

from core.sql import analyze_sql, looks_like_sql, normalize_table_name


class TestAnalyzeSql(unittest.TestCase):
    def test_full_scan_without_where(self):
        shape = analyze_sql("SELECT * FROM orders")
        self.assertEqual(shape.tables, ("orders",))
        self.assertFalse(shape.has_where)
        self.assertTrue(shape.select_star)
        self.assertFalse(shape.has_limit)
        self.assertFalse(shape.has_aggregate)

    def test_table_alias_with_schema_and_quotes(self):
        shape = analyze_sql('select id from `analytics`.`orders` o join "dim_user" u on o.uid = u.id')
        self.assertEqual(shape.tables, ("orders", "dim_user"))

    def test_comments_are_ignored(self):
        sql = """
        -- from fake_table
        select count(*) from orders /* join ignored */ where dt = '2026-01-01'
        """
        shape = analyze_sql(sql)
        self.assertEqual(shape.tables, ("orders",))
        self.assertTrue(shape.has_aggregate)

    def test_filters_are_classified(self):
        shape = analyze_sql(
            "select id from orders where id = 42 and dt between '2026-01-01' and '2026-02-01' "
            "and status in ('paid') and note like '%x%'"
        )
        self.assertTrue(shape.has_where)
        self.assertIn("id", shape.equality_columns)
        self.assertIn("dt", shape.range_columns)
        self.assertIn("status", shape.in_columns)
        self.assertIn("note", shape.like_columns)

    def test_limit_value(self):
        shape = analyze_sql("select * from orders limit 250")
        self.assertTrue(shape.has_limit)
        self.assertEqual(shape.limit_value, 250)

    def test_order_by_limit_not_confused_with_where_tail(self):
        shape = analyze_sql("select * from orders where status = 'paid' order by id limit 5")
        self.assertTrue(shape.has_where)
        self.assertIn("status", shape.equality_columns)

    def test_empty_and_invalid_input(self):
        self.assertEqual(analyze_sql("").tables, ())
        self.assertEqual(analyze_sql(None).tables, ())  # type: ignore[arg-type]
        self.assertEqual(analyze_sql("   ").tables, ())

    def test_duplicate_tables_are_deduplicated(self):
        shape = analyze_sql("select * from orders o join orders p on o.id = p.parent_id")
        self.assertEqual(shape.tables, ("orders",))


class TestHelpers(unittest.TestCase):
    def test_looks_like_sql(self):
        self.assertTrue(looks_like_sql("  SELECT 1"))
        self.assertTrue(looks_like_sql("with t as (select 1) select * from t"))
        self.assertFalse(looks_like_sql("hello world"))
        self.assertFalse(looks_like_sql(None))

    def test_normalize_table_name(self):
        self.assertEqual(normalize_table_name("`Analytics`.`Orders`"), "orders")
        self.assertEqual(normalize_table_name("ORDERS"), "orders")


if __name__ == "__main__":
    unittest.main()