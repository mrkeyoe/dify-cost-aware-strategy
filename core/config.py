"""策略配置：默认值、参数解析与校验（纯标准库）。

配置来源是 Dify 策略参数（``dict``），因此所有解析都必须防御性处理：
宿主可能把数字传成字符串、把 JSON 传成文本、把缺省项整个省略。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

from core.metadata import MetadataRegistry

__all__ = ["StrategyConfig", "ConfigurationError"]


class ConfigurationError(ValueError):
    """参数非法。错误信息面向最终用户，可直接回显到 Dify 日志。"""


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):  # bool 是 int 的子类，必须先拦
        raise ConfigurationError(f"参数 {field_name} 需要数字，收到布尔值 {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"参数 {field_name} 需要数字，收到 {value!r}") from exc


def _as_int(value: Any, field_name: str) -> int:
    number = _as_float(value, field_name)
    if number != int(number):
        raise ConfigurationError(f"参数 {field_name} 需要整数，收到 {value!r}")
    return int(number)


def _as_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ConfigurationError(f"参数 {field_name} 需要布尔值，收到 {value!r}")


def _as_float_map(value: Any, field_name: str) -> dict[str, float]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"参数 {field_name} 需要 JSON 对象，例如 {{\"db.scan\": 50}}；解析失败: {exc}"
            ) from exc
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"参数 {field_name} 需要 JSON 对象，收到 {type(value).__name__}")
    result: dict[str, float] = {}
    for key, raw in value.items():
        name = str(key).strip()
        if not name:
            raise ConfigurationError(f"参数 {field_name} 存在空工具名")
        cost = _as_float(raw, f"{field_name}.{name}")
        if cost < 0:
            raise ConfigurationError(f"参数 {field_name}.{name} 不能为负: {cost}")
        result[name] = cost
    return result


def _as_str_set(value: Any) -> frozenset[str]:
    if value is None or value == "":
        return frozenset()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in value.split(",") if part.strip()]
        value = parsed
    if isinstance(value, Mapping):
        value = list(value.keys())
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(item).strip() for item in value if str(item).strip())


@dataclass(frozen=True)
class StrategyConfig:
    """单次 Agent 运行使用的策略配置。

    所有成本都是**相对单位**（不是货币）：只要同一套配置内可比较即可，
    默认把 ``total_budget`` 视作 100 点，方便直觉化（"这次运行最多花 100 点"）。
    """

    total_budget: float = 100.0
    cost_overrides: Mapping[str, float] = field(default_factory=dict)
    degrade_enabled: bool = True
    max_degrade_attempts: int = 3
    unknown_cost: float = 40.0
    default_tool_cost: float = 15.0
    max_iterations: int = 8
    database_tools: frozenset[str] = field(default_factory=frozenset)
    metadata: MetadataRegistry = field(default_factory=MetadataRegistry)

    # ---------------------------------------------------------------- 校验
    def validate(self) -> "StrategyConfig":
        if self.total_budget <= 0:
            raise ConfigurationError(f"total_budget 必须大于 0，收到 {self.total_budget}")
        if self.max_degrade_attempts < 0:
            raise ConfigurationError(
                f"max_degrade_attempts 不能为负，收到 {self.max_degrade_attempts}"
            )
        if self.unknown_cost < 0:
            raise ConfigurationError(f"unknown_cost 不能为负，收到 {self.unknown_cost}")
        if self.default_tool_cost < 0:
            raise ConfigurationError(f"default_tool_cost 不能为负，收到 {self.default_tool_cost}")
        if not 1 <= self.max_iterations <= 50:
            raise ConfigurationError(
                f"max_iterations 必须在 1..50 之间，收到 {self.max_iterations}"
            )
        for name, cost in self.cost_overrides.items():
            if float(cost) < 0:
                raise ConfigurationError(f"cost_overrides.{name} 不能为负: {cost}")
        return self

    # ---------------------------------------------------------------- 解析
    @classmethod
    def from_parameters(cls, parameters: Optional[Mapping[str, Any]]) -> "StrategyConfig":
        """从 Dify 策略参数字典构建配置。未知键被忽略（向前兼容）。"""
        params = dict(parameters or {})

        total_budget = _as_float(params.get("total_budget", cls.total_budget), "total_budget")
        cost_overrides = _as_float_map(params.get("cost_overrides"), "cost_overrides")
        degrade_enabled = _as_bool(
            params.get("degrade_enabled", cls.degrade_enabled), "degrade_enabled"
        )
        max_degrade_attempts = _as_int(
            params.get("max_degrade_attempts", cls.max_degrade_attempts), "max_degrade_attempts"
        )
        unknown_cost = _as_float(params.get("unknown_cost", cls.unknown_cost), "unknown_cost")
        default_tool_cost = _as_float(
            params.get("default_tool_cost", cls.default_tool_cost), "default_tool_cost"
        )
        max_iterations = _as_int(params.get("max_iterations", cls.max_iterations), "max_iterations")
        metadata = MetadataRegistry.from_config(
            params.get("table_metadata", params.get("metadata"))
        )

        return cls(
            total_budget=total_budget,
            cost_overrides=dict(cost_overrides),
            degrade_enabled=degrade_enabled,
            max_degrade_attempts=max_degrade_attempts,
            unknown_cost=unknown_cost,
            default_tool_cost=default_tool_cost,
            max_iterations=max_iterations,
            database_tools=_as_str_set(params.get("database_tools")),
            metadata=metadata,
        ).validate()

    def replace(self, **changes: Any) -> "StrategyConfig":
        return replace(self, **changes)