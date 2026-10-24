"""Dify 适配层（薄）。

``core`` 不依赖本包；本包只做 SDK 翻译。导入本包**不需要**安装 ``dify_plugin``。
"""

__all__ = ["CostAwareAgentStrategy", "CostAwareStrategyProvider"]


def __getattr__(name: str):  # 延迟导入，避免包初始化阶段就拉起 SDK 探测
    if name == "CostAwareAgentStrategy":
        from strategies.cost_aware import CostAwareAgentStrategy

        return CostAwareAgentStrategy
    if name == "CostAwareStrategyProvider":
        from strategies.provider import CostAwareStrategyProvider

        return CostAwareStrategyProvider
    raise AttributeError(name)