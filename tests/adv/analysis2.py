# -*- coding: utf-8 -*-
"""analysis2.py —— 直接调用技能包模块复算 figure/equation 定位，客观评估裁剪。"""
import sys
from pathlib import Path

SKILL = Path(r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\scripts")
sys.path.insert(0, str(SKILL))
import fitz
import p2zlib as L
import extract as E

pdf = sys.argv[1]
d = L.load_document(pdf)
pdfdoc = fitz.open(pdf)

print("### 文档级")
print("n_pages=%d n_cols=%d body_size=%.2f  warnings=%s" % (d.n_pages, d.n_cols, d.body_size, d.warnings))
for p in d.pages:
    print("  page %d: blocks=%d split_x=%s  rules=%d graphics=%d images=%d"
          % (p.index + 1, len(p.blocks), p.split_x, len(p.rules), len(p.graphics), len(p.images)))
    for b in p.blocks:
        if b.role in ("header", "footer"):
            continue
print()
print("### 题注识别")
for p in d.pages:
    for (kind, num, cap) in L.find_captions(p):
        print("  p%d %s %s col=%d bbox=(%.1f,%.1f,%.1f,%.1f) :: %s"
              % (p.index + 1, kind, num, cap.col, cap.bbox[0], cap.bbox[1], cap.bbox[2], cap.bbox[3],
                 L.caption_text(cap)[:70]))
print()
print("### 图片定位结果 + 裁剪框内混入的文本")
for p in d.pages:
    for (kind, num, cap) in L.find_captions(p):
        if kind != "figure":
            continue
        fi = E.locate_figure(p, cap, num)
        w, h = fi.bbox[2] - fi.bbox[0], fi.bbox[3] - fi.bbox[1]
        ratio = (w * h) / (p.width * p.height)
        print("\n  Figure %s p%d kind=%s notes=%s" % (num, p.index + 1, fi.kind, fi.notes))
        print("     bbox=(%.1f,%.1f,%.1f,%.1f) size=%.1fx%.1fpt 占页面 %.1f%%"
              % (fi.bbox[0], fi.bbox[1], fi.bbox[2], fi.bbox[3], w, h, ratio * 100))
        mixed = []
        for b in p.blocks:
            if b.role in ("header", "footer", "empty") or b is cap:
                continue
            inter = L.rect_inter(b.bbox, fi.bbox)
            if inter and L.rect_area(inter) > 0.5 * L.rect_area(b.bbox):
                txt = " ".join(b.text().split())
                mixed.append((round(b.y0, 1), b.role, len(txt), txt[:70]))
        for m in sorted(mixed):
            print("      混入 y=%7.1f role=%-8s chars=%3d :: %r" % m)
print()
print("### 行间公式定位结果")
eqs = E.extract_equations(d, Path(sys.argv[2]), 100.0)
for e in eqs:
    print("  %s num=%r p%d bbox=(%.1f,%.1f,%.1f,%.1f) size=%.1fx%.1f conf=%.2f notes=%s"
          % (e.id, e.number, e.page + 1, e.bbox[0], e.bbox[1], e.bbox[2], e.bbox[3],
             e.bbox[2] - e.bbox[0], e.bbox[3] - e.bbox[1], e.confidence, e.notes))
