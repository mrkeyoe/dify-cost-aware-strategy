"""相对成本估算器（纯标准库）。

成本模型只回答一个问题：**这次工具调用相对于同一套配置下的其他调用，有多贵？**
它不访问数据库、不执行 EXPLAIN、不发网络请求，因此可以零延迟地在每次工具调用前
跑一次。

数据库类工具的成本 = 行数量级 × 访问方式选择性 × 输出规模
  * 行数量级：``5 * log10(rows)``，即 1e3 行≈15 点、1e6 行≈30 点、1e9 行≈45 点。
  * 选择性：命中索引等值 > 分区裁剪 > 普通等值 > 范围 > LIKE/无过滤。
  * 输出规模：无聚合时的 ``LIMIT`` 按 ``limit/rows`` 折减；聚合不折减扫描成本
    （``count(*)`` 依然要扫全表，这是刻意的诚实取舍）。
非数据库工具使用静态权重（``cost_overrides`` 优先，其次 ``default_tool_cost``）。
估算不出来时返回**默认高成本信号**（``unknown_cost``，默认 40 点），
让预算机制倾向于先降级而不是让不可知的高成本调用悄悄通过。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from core.config import StrategyConfig
from core.metadata import MetadataRegistry, TableMetadata
from core.sql import QueryShape, analyze_sql, looks_like_sql, normalize_table_name

__all__ = ["CostEstimate", "CostEstimator", "SQL_FIELDS", "TABLE_FIELDS"]

SQL_FIELDS = ("sql", "query", "statement", "sql_query", "query_sql", "sqlText")
# 这些字段名本身就表示 SQL，无需二次确认
_STRICT_SQL_FIELDS = ("sql", "statement", "sql_query", "query_sql", "sqlText")
_FROM_CLAUSE = re.compile(r"\bfrom\b", re.I)
TABLE_FIELDS = ("table", "table_name", "target_table", "dataset_table", "from_table")

_DB_NAME_HINT = re.compile(
    r"(sql|query|database|\bdb\b|warehouse|bigquery|clickhouse|postgres|mysql|hive"
    r"|spark|trino|snowflake|duckdb|sqlite|redshift|doris|starrocks)",
    re.I,
)

_MIN_COST = 0.5
_ROWS_CAP = 60.0


@dataclass(frozen=True)
class CostEstimate:
    """单次调用的相对成本估算结果。"""

    tool_name: str
    cost: float
    basis: str
    confidence: str
    factors: Mapping[str, float] = field(default_factory=dict)
    table: Optional[str] = None
    note: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.basis == "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "cost": round(self.cost, 2),
            "basis": self.basis,
            "confidence": self.confidence,
            "factors": {key: round(value, 4) for key, value in self.factors.items()},
            "table": self.table,
            "note": self.note,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rows_factor(row_count: int) -> float:
    """行数量级 -> 成本基数。单调递增，且大表之间差异被对数压缩。"""
    rows = max(int(row_count), 10)
    return _clamp(5.0 * math.log10(rows), 1.0, _ROWS_CAP)


class CostEstimator:
    """把 (工具名, 工具入参) 映射成 :class:`CostEstimate`。"""

    def __init__(self, config: StrategyConfig, registry: Optional[MetadataRegistry] = None):
        self._config = config
        self._registry = registry if registry is not None else config.metadata

    # ------------------------------------------------------------ 公开接口
    def estimate(
        self, tool_name: str, tool_input: Optional[Mapping[str, Any]] = None
    ) -> CostEstimate:
        name = str(tool_name or "").strip()
        payload = dict(tool_input or {})

        override = self._config.cost_overrides.get(name)
        if override is not None:
            return CostEstimate(
                tool_name=name,
                cost=float(override),
                basis="override",
                confidence="high",
                note="按 cost_overrides 配置的静态权重",
            )

        if self.is_database_tool(name, payload):
            return self._estimate_database(name, payload)

        return CostEstimate(
            tool_name=name,
            cost=float(self._config.default_tool_cost),
            basis="static",
            confidence="medium",
            note="非数据库工具，按 default_tool_cost 的静态权重计费",
        )

    def is_database_tool(self, tool_name: str, tool_input: Optional[Mapping[str, Any]] = None) -> bool:
        name = str(tool_name or "")
        if name in self._config.database_tools:
            return True
        payload = tool_input or {}
        if any(looks_like_sql(payload.get(key)) for key in _STRICT_SQL_FIELDS):
            return True
        # 通用 `query` 字段歧义大（web_search 也用它），要求同时出现 FROM 才认作 SQL
        generic = payload.get("query")
        if looks_like_sql(generic) and _FROM_CLAUSE.search(generic):  # type: ignore[arg-type]
            return True
        return bool(_DB_NAME_HINT.search(name))

    # ------------------------------------------------------------ 内部实现
    def _estimate_database(self, name: str, payload: Mapping[str, Any]) -> CostEstimate:
        sql = next((payload[key] for key in SQL_FIELDS if isinstance(payload.get(key), str) and payload[key].strip()), "")
        shape = analyze_sql(sql) if sql else QueryShape()

        explicit_table = next(
            (str(payload[key]) for key in TABLE_FIELDS if payload.get(key)), None
        )
        table = explicit_table or shape.primary_table

        if not table:
            return self._unknown(name, note="数据库工具，但入参无法确定目标表", table=None)

        meta = self._registry.get(table)
        if meta is None:
            return self._unknown(
                name,
                note=f"表 {normalize_table_name(table)} 未在 table_metadata 中登记行数，按未知高成本处理",
                table=table,
            )

        return self._cost_from_shape(name, meta, shape)

    def _unknown(self, name: str, *, note: str, table: Optional[str]) -> CostEstimate:
        return CostEstimate(
            tool_name=name,
            cost=float(self._config.unknown_cost),
            basis="unknown",
            confidence="low",
            factors={"unknown_cost": float(self._config.unknown_cost)},
            table=table,
            note=note,
        )

    def _cost_from_shape(
        self, name: str, meta: TableMetadata, shape: QueryShape
    ) -> CostEstimate:
        rows_factor = _rows_factor(meta.row_count)
        selectivity, access = self._selectivity(meta, shape)
        limit_factor = self._limit_factor(meta, shape)
        column_factor = 1.15 if (shape.select_star and not shape.has_aggregate) else 1.0

        cost = round(
            _clamp(rows_factor * selectivity * limit_factor * column_factor, _MIN_COST, 1e6), 2
        )
        return CostEstimate(
            tool_name=name,
            cost=cost,
            basis="table_rows",
            confidence="high" if shape.all_filter_columns else "medium",
            factors={
                "rows_factor": rows_factor,
                "selectivity": selectivity,
                "limit_factor": limit_factor,
                "column_factor": column_factor,
                "row_count": float(meta.row_count),
            },
            table=meta.name,
            note=access,
        )

    @staticmethod
    def _selectivity(meta: TableMetadata, shape: QueryShape) -> tuple[float, str]:
        """返回 (选择性系数, 可读的访问方式说明)。系数越小越便宜。"""
        if not shape.has_where:
            return 1.0, "无 WHERE，按全表扫描计费"

        if any(meta.is_partition_column(col) for col in shape.all_filter_columns):
            return 0.12, "命中分区裁剪"

        if any(meta.is_indexed(col) for col in shape.equality_columns):
            return 0.08, "命中索引等值查找"

        if any(meta.is_indexed(col) for col in shape.in_columns):
            return 0.15, "命中索引 IN 列表"

        if shape.equality_columns:
            return 0.25, "等值过滤但未命中索引"

        if shape.range_columns:
            return 0.35, "范围过滤（可能走索引范围扫描）"

        if shape.in_columns:
            return 0.4, "IN 列表过滤且未命中索引"

        if shape.like_columns:
            return 0.7, "LIKE 模糊匹配，通常无法走索引"

        return 0.6, "有过滤条件但无法识别过滤列"

    @staticmethod
    def _limit_factor(meta: TableMetadata, shape: QueryShape) -> float:
        if not shape.has_limit or shape.limit_value is None:
            return 1.0
        if shape.has_aggregate:
            # 聚合仍需扫描全表，LIMIT 只截断输出，不折减扫描成本
            return 1.0
        if meta.row_count <= 0:
            return 1.0
        return _clamp(shape.limit_value / float(meta.row_count), 0.02, 1.0)