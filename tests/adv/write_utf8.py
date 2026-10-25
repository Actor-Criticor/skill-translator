# -*- coding: utf-8 -*-
"""write_utf8.py —— 按需写出无 BOM / 带 BOM 的 UTF-8 文本（测试辅助）。

用法：python write_utf8.py <path> bom|nobom <text>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
mode = sys.argv[2]
text = sys.argv[3]
enc = "utf-8-sig" if mode == "bom" else "utf-8"
p.write_text(text + "\n", encoding=enc)
print(p, mode, p.read_bytes()[:8])
