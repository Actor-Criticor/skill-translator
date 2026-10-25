# -*- coding: utf-8 -*-
"""dump_blocks.py —— 打印 PDF 每页的文本块（测试辅助，非技能包组成部分）。"""
import sys
import fitz

pdf = sys.argv[1] if len(sys.argv) > 1 else "adv_sample.pdf"
d = fitz.open(pdf)
print("pages:", d.page_count)
for i in range(d.page_count):
    print("=" * 18, "PAGE", i + 1)
    for b in d[i].get_text("blocks"):
        t = " ".join(b[4].split())
        print("  y=%6.1f-%6.1f x=%6.1f-%6.1f" % (b[1], b[3], b[0], b[2]),
              repr(t[:110]))
    rules = [g["rect"] for g in d[i].get_drawings()
             if g["rect"].height <= 1.5 and g["rect"].width >= 6.0]
    print("  -- rules(h/v-like, h<=1.5pt,w>=6pt):", len(rules))
    for r in rules[:14]:
        print("     rule y=%.1f x=%.1f..%.1f w=%.1f h=%.2f"
              % (r.y0, r.x0, r.x1, r.width, r.height))
