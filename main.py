"""Dify 插件进程入口（仅在 Dify 插件运行时内执行）。

本机没有安装 ``dify_plugin``，因此这里用守卫式导入：被误导入时不抛异常，
真正运行插件时按官方模板启动 ``Plugin``。
"""

from __future__ import annotations

import os


def main() -> None:  # pragma: no cover - 需要 Dify 运行时
    from dify_plugin import DifyPluginEnv, Plugin

    class CostAwareStrategyPlugin(Plugin):
        """插件进程：由 Dify runner 注入环境变量与配置。"""

        def __init__(self, env: "DifyPluginEnv") -> None:  # noqa: F821
            super().__init__(env)

    CostAwareStrategyPlugin(
        DifyPluginEnv(MAX_REQUEST_TIMEOUT=int(os.environ.get("MAX_REQUEST_TIMEOUT", "120")))
    ).run()


if __name__ == "__main__":  # pragma: no cover
    main()