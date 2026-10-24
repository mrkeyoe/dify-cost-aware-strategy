"""Provider 声明的入口文件（由 ``provider/cost_aware_strategy.yaml`` 的
``extra.python.source`` 指向）。

这里只做一次转导出，真正的实现留在 :mod:`strategies.provider`，
以便单测在没有 ``dify_plugin`` 的环境里直接导入实现本身。
"""

from __future__ import annotations

from strategies.provider import CostAwareStrategyProvider

# Dify 运行时按约定类名查找 Provider 实现，两种常见命名都提供。
Provider = CostAwareStrategyProvider

__all__ = ["CostAwareStrategyProvider", "Provider"]