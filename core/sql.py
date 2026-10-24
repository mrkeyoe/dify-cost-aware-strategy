"""轻量 SQL 形状解析（纯标准库）。

这里刻意**不做**完整 SQL 语法解析，也**不执行** EXPLAIN：只靠关键词与正则抽取
"目标表 / 过滤条件形状 / 是否 LIMIT / 是否聚合"这几类特征，供相对成本分使用。

设计取舍：解析器越"聪明"，误判造成的成本偏差越大，而本插件的目标是**可比较**的
相对成本分，不是精确基数估计。因此宁可保守（拿不到特征就按高成本处理）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "QueryShape",
    "analyze_sql",
    "looks_like_sql",
    "normalize_table_name",
]

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# FROM / JOIN 后面紧跟的标识符 token（支持 schema.table、带引号/反引号/方括号；
# 子查询以 "(" 开头，会在 normalize 阶段被丢弃）
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([^\s,;()]+)", re.I)
# 合法表引用（可带 schema 与引号）；用于过滤子查询等非表 token
_TABLE_TOKEN = re.compile(
    r"^[`\"\[]?[A-Za-z_][\w$`\"\[\]]*(?:\.[`\"\[]?[A-Za-z_][\w$`\"\[\]]*)*$"
)

_WHERE = re.compile(r"\bwhere\b", re.I)
_LIMIT = re.compile(r"\blimit\s+(\d+)", re.I)
_AGGREGATE = re.compile(r"\b(?:count|sum|avg|min|max|approx_count_distinct|stddev)\s*\(", re.I)
_SELECT_STAR = re.compile(r"\bselect\s+(?:distinct\s+)?\*", re.I)

_EQUALITY = re.compile(r"\b([A-Za-z_][\w.]*)\s*=(?!=)\s*[^\s,)]", re.I)
_IN_LIST = re.compile(r"\b([A-Za-z_][\w.]*)\s+in\s*\(", re.I)
_RANGE = re.compile(
    r"\b([A-Za-z_][\w.]*)\s*(?:>=|<=|>|<|between\b)",
    re.I,
)
_LIKE = re.compile(r"\b([A-Za-z_][\w.]*)\s+(?:not\s+)?like\b", re.I)

_WHERE_TAIL = re.compile(r"\b(?:group\s+by|order\s+by|limit|having|union|;)\b", re.I)

_SQL_HEAD = re.compile(r"^\s*(?:select|with|show|describe|desc|explain)\b", re.I)


def looks_like_sql(value: object) -> bool:
    """判断一个字段值看起来是不是 SQL 语句。"""
    return isinstance(value, str) and bool(_SQL_HEAD.search(value))


def normalize_table_name(name: str) -> str:
    """去掉引号/反引号/方括号、schema 前缀与大小写差异，用于元数据查表。

    ``"`Analytics`.`Orders`"`` -> ``"orders"``；``"db.schema.t"`` -> ``"t"``。
    """
    if not isinstance(name, str):
        return ""
    parts = [part.strip().strip("`\"[]").strip() for part in name.split(".")]
    parts = [part for part in parts if part]
    return parts[-1].lower() if parts else ""


@dataclass(frozen=True)
class QueryShape:
    """从 SQL 文本抽取出来的、与成本相关的形状特征。"""

    tables: tuple[str, ...] = ()
    has_where: bool = False
    equality_columns: tuple[str, ...] = ()
    in_columns: tuple[str, ...] = ()
    range_columns: tuple[str, ...] = ()
    like_columns: tuple[str, ...] = ()
    has_limit: bool = False
    limit_value: Optional[int] = None
    has_aggregate: bool = False
    select_star: bool = False

    @property
    def all_filter_columns(self) -> tuple[str, ...]:
        return (
            *self.equality_columns,
            *self.in_columns,
            *self.range_columns,
            *self.like_columns,
        )

    @property
    def primary_table(self) -> Optional[str]:
        return self.tables[0] if self.tables else None


def _strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def _where_clause(sql: str) -> str:
    match = _WHERE.search(sql)
    if not match:
        return ""
    tail = sql[match.end() :]
    stop = _WHERE_TAIL.search(tail)
    return tail[: stop.start()] if stop else tail


def _unique(values) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        key = normalize_table_name(value)
        if key and key not in seen:
            seen[key] = None
    return tuple(seen)


def analyze_sql(sql: str) -> QueryShape:
    """把 SQL 文本解析成 :class:`QueryShape`（永不抛异常）。"""
    if not isinstance(sql, str) or not sql.strip():
        return QueryShape()

    cleaned = _strip_comments(sql)
    where = _where_clause(cleaned)
    limit_match = _LIMIT.search(cleaned)

    return QueryShape(
        tables=_unique(
            m.group(1) for m in _TABLE_REF.finditer(cleaned) if _TABLE_TOKEN.match(m.group(1))
        ),
        has_where=bool(where.strip()),
        equality_columns=_unique(m.group(1) for m in _EQUALITY.finditer(where)),
        in_columns=_unique(m.group(1) for m in _IN_LIST.finditer(where)),
        range_columns=_unique(m.group(1) for m in _RANGE.finditer(where)),
        like_columns=_unique(m.group(1) for m in _LIKE.finditer(where)),
        has_limit=bool(limit_match),
        limit_value=int(limit_match.group(1)) if limit_match else None,
        has_aggregate=bool(_AGGREGATE.search(cleaned)),
        select_star=bool(_SELECT_STAR.search(cleaned)),
    )