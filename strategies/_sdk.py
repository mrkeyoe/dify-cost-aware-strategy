"""Dify SDK 绑定层（lazy import + 最小 stub）。

本机**没有安装** ``dify_plugin`` SDK，也不把联网装包当作可交付前提。因此这里：

1. 尽力导入真实 SDK 类型；
2. 导入失败时退回到语义等价的最小 stub，使 ``core`` 与适配层都能在纯标准库下被导入、
   被单测；
3. 通过 :data:`SDK_AVAILABLE` / :data:`SDK_IMPORT_ERROR` 把"当前是否在真实运行时"
   如实暴露给调用方与报告。

已知不确定点（见 README §已知限制）：``AgentStrategy`` 的构造签名、
``create_text_message`` 等消息工厂的确切位置随 SDK 版本变化，故这里全部用
``try/except + 鸭子类型`` 兜住，而不是硬绑定。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = [
    "SDK_AVAILABLE",
    "SDK_IMPORT_ERROR",
    "AgentStrategyBase",
    "AgentStrategyConfig",
    "ProviderBase",
    "make_text_message",
    "make_json_message",
    "build_parameter_object",
]

SDK_AVAILABLE = False
SDK_IMPORT_ERROR: str | None = None

try:  # pragma: no cover - 取决于运行环境是否装了 SDK
    from dify_plugin.interfaces.agent import AgentStrategy as _SdkAgentStrategy
    from dify_plugin.entities.agent import AgentStrategyConfig as _SdkAgentStrategyConfig

    SDK_AVAILABLE = True
    AgentStrategyBase = _SdkAgentStrategy
    AgentStrategyConfig = _SdkAgentStrategyConfig
except Exception as exc:  # noqa: BLE001 - 任何导入失败都必须降级到 stub
    SDK_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

    class _StubParameter:
        """与 SDK ``AgentStrategyConfig.Parameter`` 形状对齐的最小实现。"""

        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

        def model_dump(self) -> dict[str, Any]:
            return dict(self.__dict__)

    class AgentStrategyConfig:  # type: ignore[no-redef]
        Parameter = _StubParameter

        def __init__(self, parameters: list[Any] | None = None) -> None:
            self.parameters = list(parameters or [])

        def model_dump(self) -> dict[str, Any]:
            return {
                "parameters": [
                    p.model_dump() if hasattr(p, "model_dump") else p
                    for p in self.parameters
                ]
            }

    class AgentStrategyBase:  # type: ignore[no-redef]
        """SDK 缺失时的占位基类：只保证可导入、可实例化、可被单测注入依赖。"""

        def _invoke(self, parameters: Mapping[str, Any]):  # pragma: no cover - 抽象
            raise NotImplementedError("子类必须实现 _invoke")

        def create_text_message(self, text: str) -> Any:
            return make_text_message(text)

        def create_json_message(self, payload: Any) -> Any:
            return make_json_message(payload)

    class ProviderBase:  # type: ignore[no-redef]
        def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
            return None

        def get_strategies(self) -> list[Any]:  # pragma: no cover - 抽象
            raise NotImplementedError
else:  # pragma: no cover - 需要真实 SDK
    try:
        from dify_plugin.interfaces.agent import AgentProvider as ProviderBase
    except Exception:  # noqa: BLE001

        class ProviderBase:  # type: ignore[no-redef]
            def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
                return None


# --------------------------------------------------------------- 参数对象
def build_parameter_object(spec: Mapping[str, Any]) -> Any:
    """把 schema 里的一个参数描述转成 SDK 的 ``Parameter`` 对象。

    只传最保守的字段集合；SDK 版本差异导致的 ``TypeError`` 会被兜住，
    退化为 stub ``Parameter``（保证 provider schema 仍然可构造）。
    """
    safe_keys = ("name", "type", "label", "help", "required", "default", "options")
    payload = {key: spec[key] for key in safe_keys if key in spec}
    parameter_cls = getattr(AgentStrategyConfig, "Parameter", None)
    if parameter_cls is None:  # pragma: no cover - 防御
        raise RuntimeError("当前 SDK 的 AgentStrategyConfig 缺少 Parameter")
    try:
        return parameter_cls(**payload)
    except TypeError:
        from types import SimpleNamespace

        return SimpleNamespace(**payload)


# --------------------------------------------------------------- 消息工厂
def make_text_message(text: str) -> Any:
    """文本消息。优先用 SDK 工厂，缺失时返回 ``{"type": "text", ...}`` 字典。"""
    if SDK_AVAILABLE:  # pragma: no cover - 需要真实 SDK
        try:
            from dify_plugin.entities.message import create_text_message

            return create_text_message(text)
        except Exception:  # noqa: BLE001
            pass
    return {"type": "text", "content": text}


def make_json_message(payload: Any) -> Any:
    """JSON 消息。用于把降级决策/成本报告结构化地交给宿主。"""
    if SDK_AVAILABLE:  # pragma: no cover - 需要真实 SDK
        try:
            from dify_plugin.entities.message import create_json_message

            return create_json_message(payload)
        except Exception:  # noqa: BLE001
            pass
    if not isinstance(payload, (dict, list)):
        payload = json.loads(json.dumps(payload, default=str))
    return {"type": "json", "content": payload}