# -*- coding: utf-8 -*-
"""thumb.py —— 把大图缩成便于查看的缩略图（测试辅助）。"""
import sys
from pathlib import Path
import fitz

src = Path(sys.argv[1])
out = Path(sys.argv[2])
scale = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
pix = fitz.Pixmap(str(src))
if pix.n > 3:
    pix = fitz.Pixmap(fitz.csRGB, pix)
pix = fitz.Pixmap(pix, int(pix.width * scale), int(pix.height * scale), None)
pix.save(str(out))
print("src=%dx%d -> %s %dx%d" % (0, 0, out, pix.width, pix.height))
