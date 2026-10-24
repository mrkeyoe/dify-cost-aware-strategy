"""测试包：确保仓库根目录在 ``sys.path`` 上，``import core`` 与 ``import strategies`` 可用。

这样无论用 ``python -m unittest discover -s tests`` 还是 ``python -m unittest tests.test_x``，
都不需要安装本包、也不需要设置 ``PYTHONPATH``。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)