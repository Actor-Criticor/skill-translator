# -*- coding: utf-8 -*-
"""probe_fig_new.py —— 在最新活代码上量测每张图的裁剪框 vs 图形真实范围。"""
import sys
from pathlib import Path

LIVE = Path(r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\scripts")
sys.path.insert(0, str(LIVE))
import p2zlib as L
import extract as E
import fitz

pdf = sys.argv[1]
d = L.load_document(pdf)
L.mark_headers_footers(d)
L.classify_blocks(d)
L.mark_reference_zones(d)
doc = fitz.open(pdf)

for p in d.pages:
    for (kind, num, cap) in L.find_captions(p):
        if kind != "figure":
            continue
        fi = E.locate_figure(p, cap, num)
        # 图形真实范围 = 与裁剪框相交的图形簇并集
        shapes = [g.bbox for g in p.graphics if L.rect_area(g.bbox) >= 9]
        shapes += [im.bbox for im in p.images if L.rect_area(im.bbox) >= 100]
        inside = [s for s in shapes if L.rect_inter(s, fi.bbox)]
        u = L.rect_union(inside) if inside else None
        print("\nFigure %s p%d notes=%s" % (num, p.index + 1, fi.notes))
        print("  裁剪框  = (%.1f,%.1f,%.1f,%.1f)  %.0fx%.0f"
              % (fi.bbox[0], fi.bbox[1], fi.bbox[2], fi.bbox[3],
                 fi.bbox[2] - fi.bbox[0], fi.bbox[3] - fi.bbox[1]))
        if u:
            print("  框内图形并集 = (%.1f,%.1f,%.1f,%.1f)  %.0fx%.0f"
                  % (u[0], u[1], u[2], u[3], u[2] - u[0], u[3] - u[1]))
            print("  图形被切掉: 左%.1f 右%.1f 上%.1f 下%.1f pt"
                  % (max(0, u[0] - fi.bbox[0]), max(0, u[2] - fi.bbox[2]),
                     max(0, u[1] - fi.bbox[1]), max(0, u[3] - fi.bbox[3])))
        # 框外/框内的短文本（图内标签）
        print("  区域内文本块：")
        for b in sorted(p.blocks, key=lambda b: b.bbox[1]):
            if b.role in ("header", "footer", "empty") or b is cap:
                continue
            it = L.rect_inter(b.bbox, fi.bbox)
            if it and L.rect_area(it) > 0.5 * L.rect_area(b.bbox):
                print("     IN   y=%7.1f %r" % (b.bbox[1], " ".join(b.text().split())[:56]))
        for b in sorted(p.blocks, key=lambda b: b.bbox[1]):
            if b.role in ("header", "footer", "empty", "caption") or b is cap:
                continue
            t = b.text().strip()
            if len(t.split()) > 8 or not t:
                continue
            # 紧邻裁剪框（±25pt）但不在框内 —— 疑似被切掉的图内标签
            near = (fi.bbox[0] - 25 <= b.bbox[2] and b.bbox[0] <= fi.bbox[2] + 25 and
                    fi.bbox[1] - 25 <= b.bbox[3] and b.bbox[1] <= fi.bbox[3] + 25)
            it = L.rect_inter(b.bbox, fi.bbox)
            inside_ratio = (L.rect_area(it) / max(1e-6, L.rect_area(b.bbox))) if it else 0.0
            if near and inside_ratio <= 0.5:
                print("     OUT  y=%7.1f x=%7.1f %r" % (b.bbox[1], b.bbox[0], t[:56]))
