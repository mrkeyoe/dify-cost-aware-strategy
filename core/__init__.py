"""成本感知 Agent 策略的核心逻辑（纯标准库、零第三方依赖）。

分层原因：本机没有 ``dify_plugin`` SDK，也不应该把"能否联网装包"当成能否验证
逻辑的前提。因此：

* ``core/``  —— 成本估算、预算账本、降级提示、策略状态机、运行报告，全部可单测；
* ``strategies/`` —— 薄适配层，只有把 SDK 消息/参数翻译成 core 协议这点代码。
"""

from core.budget import BudgetExhaustedError, BudgetLedger
from core.config import ConfigurationError, StrategyConfig
from core.degrade import (
    DEFAULT_ALTERNATIVES,
    DegradeAlternative,
    build_degrade_prompt,
    parse_degrade_prompt,
)
from core.estimator import CostEstimate, CostEstimator
from core.loop import (
    BudgetBlockedEvent,
    CostAwareAgentLoop,
    HaltEvent,
    LLMProtocol,
    LoopEvent,
    Plan,
    PlannedToolCall,
    ReportEvent,
    TextEvent,
    ToolExecutedEvent,
    ToolExecutorProtocol,
    ToolResult,
)
from core.metadata import MetadataRegistry, TableMetadata
from core.policy import (
    REASON_BUDGET_EXCEEDED,
    REASON_DEGRADE_DISABLED,
    REASON_DEGRADE_LIMIT,
    REASON_MAX_ITERATIONS,
    CostAwarePolicy,
    Decision,
    DecisionAction,
)
from core.report import CostReport, DegradeEvent

__all__ = [
    "BudgetExhaustedError",
    "BudgetLedger",
    "BudgetBlockedEvent",
    "ConfigurationError",
    "CostAwareAgentLoop",
    "CostAwarePolicy",
    "CostEstimate",
    "CostEstimator",
    "CostReport",
    "DEFAULT_ALTERNATIVES",
    "Decision",
    "DecisionAction",
    "DegradeAlternative",
    "DegradeEvent",
    "HaltEvent",
    "LLMProtocol",
    "LoopEvent",
    "MetadataRegistry",
    "Plan",
    "PlannedToolCall",
    "REASON_BUDGET_EXCEEDED",
    "REASON_DEGRADE_DISABLED",
    "REASON_DEGRADE_LIMIT",
    "REASON_MAX_ITERATIONS",
    "ReportEvent",
    "StrategyConfig",
    "TableMetadata",
    "TextEvent",
    "ToolExecutedEvent",
    "ToolExecutorProtocol",
    "ToolResult",
    "build_degrade_prompt",
    "parse_degrade_prompt",
]