"""策略参数 schema 的单一真源。

``provider/cost_aware.yaml`` 与 Python 侧的 ``AgentStrategyConfig`` 都从这里生成，
避免两边漂移。所有字段名与 :class:`core.config.StrategyConfig` 对齐。
"""

from __future__ import annotations

from typing import Any

__all__ = ["PARAMETER_SPECS", "REQUIRED_PARAMETERS", "build_agent_strategy_config"]


def _spec(
    *,
    name: str,
    ptype: str,
    label_zh: str,
    label_en: str,
    help_zh: str,
    help_en: str,
    required: bool = False,
    default: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "type": ptype,
        "label": {"en_US": label_en, "zh_Hans": label_zh},
        "help": {"en_US": help_en, "zh_Hans": help_zh},
        "required": required,
        "default": default,
        **extra,
    }


PARAMETER_SPECS: tuple[dict[str, Any], ...] = (
    _spec(
        name="total_budget",
        ptype="number",
        label_zh="总预算（相对单位）",
        label_en="Total budget (relative units)",
        help_zh="单次 Agent 运行可消耗的相对成本上限。默认 100，推荐按“一次全表扫描≈40~60 点”标定。",
        help_en="Relative cost ceiling for one run. Default 100; calibrate so one full scan ≈ 40-60.",
        required=True,
        default=100,
        min=1,
        max=1000000,
    ),
    _spec(
        name="cost_overrides",
        ptype="string",
        label_zh="成本覆盖（JSON）",
        label_en="Cost overrides (JSON)",
        help_zh='按工具名覆盖默认成本，JSON 对象，例如 {"db.scan_query": 50}。留空表示不覆盖。',
        help_en='Per-tool static cost override as JSON object, e.g. {"db.scan_query": 50}.',
        required=False,
        default="{}",
    ),
    _spec(
        name="degrade_enabled",
        ptype="boolean",
        label_zh="启用预算降级",
        label_en="Enable budget degradation",
        help_zh="关闭后，一旦调用超预算就直接终止规划循环（不做“换个更便宜方案”的引导）。",
        help_en="When off, an over-budget call halts the loop instead of prompting for a cheaper plan.",
        required=True,
        default=True,
    ),
    _spec(
        name="max_degrade_attempts",
        ptype="number",
        label_zh="最大连续降级次数",
        label_en="Max consecutive degrade attempts",
        help_zh="连续降级达到该次数后终止运行，防止 LLM 反复索要更便宜方案形成死循环。",
        help_en="Halt after this many consecutive degradations to prevent an infinite replanning loop.",
        required=False,
        default=3,
        min=0,
        max=20,
    ),
    _spec(
        name="table_metadata",
        ptype="string",
        label_zh="表元数据（JSON）",
        label_en="Table metadata (JSON)",
        help_zh='数据库成本估算输入，JSON 对象：{"orders": {"row_count": 1000000, "indexed_columns": ["id"], "partition_column": "dt"}}。缺省时数据库调用按未知高成本处理。',
        help_en='JSON object of table metadata used by the estimator: {"orders": {"row_count": 1000000}}.',
        required=False,
        default="{}",
    ),
    _spec(
        name="database_tools",
        ptype="string",
        label_zh="数据库工具名单（JSON/逗号分隔）",
        label_en="Database tool names (JSON or comma separated)",
        help_zh='显式声明哪些工具属于数据库查询（名称里没有 sql/db 等关键词时使用），例如 ["mysql.query"]。',
        help_en='Explicitly mark database tools when their names are not self-describing.',
        required=False,
        default="[]",
    ),
    _spec(
        name="unknown_cost",
        ptype="number",
        label_zh="未知成本信号",
        label_en="Unknown-cost signal",
        help_zh="无法估算时的默认成本（默认 40，即“高成本”）。",
        help_en="Cost used when estimation is impossible (default 40, i.e. treat as expensive).",
        required=False,
        default=40,
        min=0,
        max=100000,
    ),
    _spec(
        name="default_tool_cost",
        ptype="number",
        label_zh="非数据库工具默认成本",
        label_en="Default non-DB tool cost",
        help_zh="未在 cost_overrides 中配置的非数据库工具，按此静态权重计费。",
        help_en="Static weight for non-database tools not listed in cost_overrides.",
        required=False,
        default=15,
        min=0,
        max=100000,
    ),
    _spec(
        name="max_iterations",
        ptype="number",
        label_zh="最大规划轮数",
        label_en="Max planning iterations",
        help_zh="规划循环上界，防止 LLM 无限重规划（1~50）。",
        help_en="Upper bound of the planning loop (1-50).",
        required=False,
        default=8,
        min=1,
        max=50,
    ),
)

REQUIRED_PARAMETERS: tuple[str, ...] = tuple(
    spec["name"] for spec in PARAMETER_SPECS if spec["required"]
)


def build_agent_strategy_config() -> Any:
    """构造 ``AgentStrategyConfig``（SDK 存在则用真类型，否则用 stub）。"""
    from strategies._sdk import AgentStrategyConfig, build_parameter_object

    return AgentStrategyConfig(
        parameters=[build_parameter_object(spec) for spec in PARAMETER_SPECS]
    )