"""Dify provider 适配：把策略类暴露给插件运行时。

provider yaml（``provider/cost_aware.yaml``）声明策略与参数；这里的 Python 类负责把
策略类型交给 SDK。SDK 缺失时使用 :mod:`strategies._sdk` 的 stub 基类，保证可导入。
"""

from __future__ import annotations

from typing import Any

from strategies import _sdk
from strategies.cost_aware import CostAwareAgentStrategy

__all__ = ["CostAwareStrategyProvider"]


class CostAwareStrategyProvider(_sdk.ProviderBase):  # type: ignore[misc]
    """Agent Strategy Provider（无凭据校验：策略本身不需要外部凭证）。"""

    def _validate_credentials(self, credentials: dict[str, Any]) -> None:  # pragma: no cover
        return None

    def get_strategies(self) -> list[Any]:  # pragma: no cover - 由宿主调用
        return [CostAwareAgentStrategy]