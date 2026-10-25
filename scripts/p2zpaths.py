# -*- coding: utf-8 -*-
"""p2zpaths —— 路径处理的统一入口（只做一件小事，但很关键）。

为什么需要它：在本项目的目标环境（Windows + Anaconda Python 3.9）上实测发现
`pathlib.Path("mywork").resolve()` **会返回相对路径** "mywork"（对不存在的路径尤甚），
于是 `work / "paper" / "paper.md"` 这类拼接会得到 `mywork\\mywork\\paper\\paper.md`，
表现为"译稿找不到""文件生成到了奇怪的地方"。

因此所有脚本一律用 `abs_path()` 取绝对路径，不直接用 `Path.resolve()`。
"""

from __future__ import annotations

import os
from pathlib import Path


def abs_path(p) -> Path:
    """返回绝对路径（不依赖 Path.resolve 的实现差异）。"""
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = Path(os.path.abspath(str(path)))
    return path
