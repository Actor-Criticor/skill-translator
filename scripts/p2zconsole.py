# -*- coding: utf-8 -*-
"""p2zconsole —— 统一控制台输出编码。

被管道/重定向捕获时（GUI、CI、`| more`），Windows 上 Python 默认按本地代码页写字节，
而消费方通常按 UTF-8 解码，于是中文变成乱码。这里在"非终端"情形下显式改写成 UTF-8；
真正的交互终端（cmd/PowerShell 窗口）保持默认，中文照常正常显示。

用法：其它脚本 `import p2zconsole  # noqa: F401` 即可（导入即生效）。
"""

from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None or stream.isatty():
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


setup()
