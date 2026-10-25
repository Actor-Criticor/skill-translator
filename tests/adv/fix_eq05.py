# -*- coding: utf-8 -*-
"""fix_eq05.py —— 以无 BOM 的 UTF-8 重写 eq05 草稿（测试辅助）。"""
from pathlib import Path

p = Path(__file__).resolve().parent / "work" / "extracted" / "equations" / "draft" / "eq05.tex"
body = "f(x)=\\begin{cases} x^{2}, & x\\ge 0,\\\\ -x, & x<0. \\end{cases}\n"
p.write_text(body, encoding="utf-8")
print(p.read_bytes()[:40])
