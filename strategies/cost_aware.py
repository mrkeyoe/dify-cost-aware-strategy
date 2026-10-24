"""薄适配层：``AgentStrategy`` 子类，把 Dify 运行时接到 :mod:`core`。

这一层**只做翻译**：

* Dify 策略参数  ->  :class:`core.config.StrategyConfig`
* 规划循环        ->  :class:`core.loop.CostAwareAgentLoop`（成本感知在 core 里）
* core 事件       ->  Dify ``AgentInvokeMessage``

真实的 LLM 调用与工具执行分别通过 :meth:`_build_llm` / :meth:`_build_executor`
注入。本机没有 SDK，也没有 Dify 运行时，因此这两个方法的默认实现会抛出
:class:`IntegrationNotWired`，并在 README §已知限制 中如实说明"运行时集成未验证"。
单测通过 ``for_testing()`` 注入 fake 对象，验证接线本身（拦截、注入提示、记账、报告）。
"""

from __future__ import annotations

import time
from typing import Any, Iterator, Mapping, Optional

from core.config import StrategyConfig
from core.estimator import CostEstimator
from core.loop import (
    BudgetBlockedEvent,
    CostAwareAgentLoop,
    HaltEvent,
    LLMProtocol,
    ReportEvent,
    TextEvent,
    ToolExecutedEvent,
    ToolExecutorProtocol,
)
from core.metadata import MetadataRegistry
from core.policy import CostAwarePolicy
from strategies import _sdk
from strategies.schema import build_agent_strategy_config

__all__ = ["CostAwareAgentStrategy", "IntegrationNotWired", "AgentStrategy"]


class IntegrationNotWired(RuntimeError):
    """宿主桥接未接好（本机无 SDK/无 Dify 运行时）时的明确错误。"""


class CostAwareAgentStrategy(_sdk.AgentStrategyBase):  # type: ignore[misc]
    """成本感知 Agent 策略。

    在每次工具调用**前**用相对成本分做预算检查：
    超预算则拦下这次调用并把结构化降级提示注入下一轮规划；
    连续降级超过 ``max_degrade_attempts`` 则终止运行，避免死循环。
    """

    # ------------------------------------------------------------------ 构造
    def __init__(
        self,
        *args: Any,
        llm: Optional[LLMProtocol] = None,
        executor: Optional[ToolExecutorProtocol] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(*args, **kwargs)
        except TypeError:
            # 不同 SDK 版本的基类构造签名不一致：不因基类构造失败而放弃实例
            try:
                super().__init__()
            except Exception:  # noqa: BLE001
                pass
        self._llm = llm
        self._executor = executor

    @classmethod
    def for_testing(
        cls,
        llm: LLMProtocol,
        executor: ToolExecutorProtocol,
    ) -> "CostAwareAgentStrategy":
        """无 SDK 环境下构造可运行实例（单测/本地演练用）。"""
        return cls(llm=llm, executor=executor)

    # ------------------------------------------------------------------ schema
    def _get_provider_schema(self):  # pragma: no cover - 由宿主调用
        return build_agent_strategy_config()

    @classmethod
    def get_provider_schema(cls):  # pragma: no cover - 由宿主调用
        return build_agent_strategy_config()

    # ------------------------------------------------------------------ 桥接
    def _build_llm(self, parameters: Mapping[str, Any]) -> LLMProtocol:
        if self._llm is not None:
            return self._llm
        raise IntegrationNotWired(
            "LLM 桥接尚未接线：Dify 运行时需要把 session.model.llm 适配成 "
            "core.loop.LLMProtocol（plan(query, transcript, cost_hint, remaining_budget) -> Plan）。"
            "本机未安装 dify_plugin，无法验证该桥接，详见 README §已知限制。"
        )

    def _build_executor(self, parameters: Mapping[str, Any]) -> ToolExecutorProtocol:
        if self._executor is not None:
            return self._executor
        raise IntegrationNotWired(
            "工具执行桥接尚未接线：Dify 运行时需要把工具调用适配成 "
            "core.loop.ToolExecutorProtocol（execute(tool_name, tool_input) -> ToolResult）。"
            "本机未安装 dify_plugin，无法验证该桥接，详见 README §已知限制。"
        )

    # ------------------------------------------------------------------ 入口
    def _invoke(self, parameters: Mapping[str, Any]) -> Iterator[Any]:
        started = time.monotonic()
        params = dict(parameters or {})

        config = StrategyConfig.from_parameters(params)
        raw_metadata = params.get("table_metadata", params.get("metadata"))
        registry = (
            MetadataRegistry.from_config(raw_metadata) if raw_metadata else config.metadata
        )
        policy = CostAwarePolicy(config, CostEstimator(config, registry))
        loop = CostAwareAgentLoop(
            policy=policy,
            llm=self._build_llm(params),
            executor=self._build_executor(params),
            max_iterations=config.max_iterations,
        )

        query = str(params.get("query") or "")
        try:
            for event in loop.run(query):
                if isinstance(event, BudgetBlockedEvent):
                    # 结构化提示：既是给 LLM 的下一轮系统约束，也是宿主可见的日志
                    yield _sdk.make_json_message(event.decision.to_dict())
                    yield _sdk.make_text_message(event.prompt)
                elif isinstance(event, ToolExecutedEvent):
                    status = "ok" if event.result.ok else f"error: {event.result.error}"
                    yield _sdk.make_text_message(
                        f"[cost-aware] 放行 {event.call.tool_name} "
                        f"(cost={event.decision.estimated_cost:.2f}, "
                        f"consumed={event.decision.consumed_after:.2f}/"
                        f"{event.decision.total_budget:.2f}, {status})"
                    )
                elif isinstance(event, HaltEvent):
                    yield _sdk.make_text_message(
                        f"[cost-aware] 运行终止（{event.reason_code}）：{event.reason_text}"
                    )
                elif isinstance(event, TextEvent):
                    yield _sdk.make_text_message(event.text)
                elif isinstance(event, ReportEvent):
                    report = event.report
                    if report.elapsed_seconds is None:
                        report.elapsed_seconds = time.monotonic() - started
                    yield _sdk.make_text_message(report.to_text())
                    yield _sdk.make_json_message(report.to_dict())
        except IntegrationNotWired:
            raise
        except Exception as exc:  # noqa: BLE001 - 保证任何异常都带上成本报告
            report = policy.report(elapsed_seconds=time.monotonic() - started)
            yield _sdk.make_text_message(
                f"[cost-aware] 运行异常：{type(exc).__name__}: {exc}"
            )
            yield _sdk.make_json_message(report.to_dict())
            raise

# Dify 运行时按类名查找策略实现时使用的别名（两种常见约定都覆盖）。
AgentStrategy = CostAwareAgentStrategy
