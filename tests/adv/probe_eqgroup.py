# -*- coding: utf-8 -*-
"""probe_eqgroup.py —— 追查分式分子为何未进入公式分组。"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\scripts")
import fitz
import p2zlib as L
import extract as E

pdf = sys.argv[1]
want = (float(sys.argv[2]), float(sys.argv[3]))
pno = int(sys.argv[4]) - 1

d = L.load_document(pdf)
p = d.pages[pno]
doc = fitz.open(pdf)
raw = doc[pno].get_text("rawdict")
print("### rawdict 行")
for bi, blk in enumerate(raw["blocks"]):
    if blk.get("type", 0) != 0:
        continue
    for ln in blk.get("lines", []):
        x0, y0, x1, y1 = ln["bbox"]
        if y1 < want[0] or y0 > want[1]:
            continue
        txt = "".join(ch["c"] for sp in ln["spans"] for ch in sp["chars"])
        fonts = sorted({sp["font"] for sp in ln["spans"]})
        sizes = sorted({round(sp["size"], 1) for sp in ln["spans"]})
        print("  blk%-2d y=%7.1f..%7.1f x=%7.1f..%7.1f %-28r fonts=%s sizes=%s"
              % (bi, y0, y1, x0, x1, txt, fonts, sizes))
print("\n### p2zlib 块/行")
for b in p.blocks:
    if b.bbox[1] > want[0] - 15 and b.bbox[3] < want[1] + 25:
        print("  Block role=%-7s col=%d bbox=(%.1f,%.1f,%.1f,%.1f) math_ratio=%.2f lines=%d %r"
              % (b.role, b.col, b.bbox[0], b.bbox[1], b.bbox[2], b.bbox[3],
                 L.math_ratio(b), len(b.lines), " ".join(b.text().split())[:70]))
        for ln in b.lines:
            print("      ln bbox=(%.1f,%.1f,%.1f,%.1f) ratio=%.2f attach_only=%s trailing_num=%s %r"
                  % (ln.bbox[0], ln.bbox[1], ln.bbox[2], ln.bbox[3],
                     E._line_math_ratio(ln), E._attach_only(ln), E._trailing_eqnum(ln),
                     ln.text()[:70]))
print("\n### _display_candidates 结果")
for (pg, lines) in E._display_candidates(d):
    if pg.index != pno:
        continue
    bb = (min(c.x0 for ln in lines for c in ln.chars),
          min(c.y0 for ln in lines for c in ln.chars),
          max(c.x1 for ln in lines for c in ln.chars),
          max(c.y1 for ln in lines for c in ln.chars))
    print("  组 bbox=(%.1f,%.1f,%.1f,%.1f) 行数=%d :: %r"
          % (bb[0], bb[1], bb[2], bb[3], len(lines),
             " | ".join(ln.text()[:40] for ln in lines)))
