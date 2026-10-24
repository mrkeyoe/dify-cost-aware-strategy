"""表元数据注册表（纯标准库）。

成本估算**只读元数据 + 查询文本**，不访问数据库、不执行 EXPLAIN。
元数据由调用方（插件参数 / 宿主）以字面量形式提供；缺失时估算器返回"未知高成本信号"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Union

from core.sql import normalize_table_name

__all__ = ["TableMetadata", "MetadataRegistry"]


@dataclass(frozen=True)
class TableMetadata:
    """单表的静态元数据。行数是唯一必需项。"""

    name: str
    row_count: int
    indexed_columns: tuple[str, ...] = ()
    partition_column: Optional[str] = None

    def __post_init__(self) -> None:
        if self.row_count < 0:
            raise ValueError(f"row_count 不能为负: {self.name}={self.row_count}")

    @property
    def partitioned(self) -> bool:
        return bool(self.partition_column)

    def is_indexed(self, column: str) -> bool:
        target = column.lower().split(".")[-1]
        return target in {c.lower().split(".")[-1] for c in self.indexed_columns}

    def is_partition_column(self, column: str) -> bool:
        if not self.partition_column:
            return False
        return column.lower().split(".")[-1] == self.partition_column.lower().split(".")[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row_count": self.row_count,
            "indexed_columns": list(self.indexed_columns),
            "partition_column": self.partition_column,
        }


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, Iterable):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


class MetadataRegistry:
    """按表名（大小写/schema 前缀不敏感）索引的元数据集合。"""

    def __init__(
        self,
        tables: Optional[Union[Mapping[str, TableMetadata], Iterable[TableMetadata]]] = None,
    ) -> None:
        self._tables: dict[str, TableMetadata] = {}
        if tables is None:
            return
        if isinstance(tables, Mapping):
            for key, meta in tables.items():
                self.register(meta if isinstance(meta, TableMetadata) else TableMetadata(**meta))
                if isinstance(meta, TableMetadata) and key != meta.name:
                    # 允许别名键指向同一张表
                    self._tables[normalize_table_name(key)] = meta
            return
        for meta in tables:
            self.register(meta if isinstance(meta, TableMetadata) else TableMetadata(**meta))

    # ------------------------------------------------------------ 值语义
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MetadataRegistry):
            return NotImplemented
        return self._tables == other._tables

    def __hash__(self) -> int:  # 与 __eq__ 配套，便于放进 set/dict
        return hash(tuple(sorted(self._tables.items())))

    def __repr__(self) -> str:
        return f"MetadataRegistry(tables={list(self.table_names())!r})"

    def register(self, meta: TableMetadata) -> None:
        self._tables[normalize_table_name(meta.name)] = meta

    def get(self, table: str) -> Optional[TableMetadata]:
        if not table:
            return None
        return self._tables.get(normalize_table_name(table))

    def __contains__(self, table: object) -> bool:
        return isinstance(table, str) and self.get(table) is not None

    def __len__(self) -> int:
        # 别名可能造成重复引用，按对象去重计数
        return len({id(meta) for meta in self._tables.values()})

    def table_names(self) -> tuple[str, ...]:
        return tuple(sorted({meta.name for meta in self._tables.values()}))

    @classmethod
    def from_config(cls, raw: Any) -> "MetadataRegistry":
        """从 ``{"表名": {"row_count": 1000000, "indexed_columns": ["id"]}}`` 构建。

        容错：``raw`` 可以是 JSON 字符串、dict，或 None；非法条目被跳过而不是抛错，
        因为元数据缺失本身已被估算器当成"高成本未知"处理。
        """
        if raw is None or raw == "":
            return cls()
        if isinstance(raw, str):
            import json

            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return cls()
        if isinstance(raw, list):
            raw = {str(item.get("name", f"table_{i}")): item for i, item in enumerate(raw) if isinstance(item, dict)}
        if not isinstance(raw, Mapping):
            return cls()

        tables: dict[str, TableMetadata] = {}
        for name, spec in raw.items():
            if isinstance(spec, TableMetadata):
                tables[name] = spec
                continue
            if not isinstance(spec, Mapping):
                continue
            row_count = spec.get("row_count", spec.get("rows"))
            if row_count is None:
                # 没有行数的条目不可用于成本估算；跳过它，让估算器走"未知高成本"
                continue
            try:
                table = TableMetadata(
                    name=str(spec.get("name") or name),
                    row_count=_coerce_int(row_count, 0),
                    indexed_columns=_coerce_str_tuple(
                        spec.get("indexed_columns", spec.get("indexes"))
                    ),
                    partition_column=(
                        str(spec["partition_column"]) if spec.get("partition_column") else None
                    ),
                )
            except (TypeError, ValueError):
                # 单条元数据非法只跳过该条：缺元数据的表会被估算器按"未知高成本"处理
                continue
            tables[name] = table
        return cls(tables)