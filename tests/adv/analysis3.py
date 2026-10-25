# -*- coding: utf-8 -*-
"""analysis3.py —— 冻结快照版：量化 figure/equation 裁剪范围与混入内容。"""
import sys
from pathlib import Path

SN = Path(r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\tests\adv\snapshot_v1\scripts")
sys.path.insert(0, str(SN))
import fitz
import p2zlib as L
import extract as E

pdf = sys.argv[1]
d = L.load_document(pdf)
# extract.py main 里的三步角色判定（漏掉会让裁剪范围与真实产物不一致）
L.mark_headers_footers(d)
L.classify_blocks(d)
L.mark_reference_zones(d)
doc = fitz.open(pdf)

print("### 版面判定")
print("n_pages=%d n_cols=%d body_size=%.1f" % (d.n_pages, d.n_cols, d.body_size))
for p in d.pages:
    print("  page %d split_x=%s blocks=%d rules=%d" % (p.index + 1, p.split_x, len(p.blocks), len(p.rules)))

print("\n### 图片裁剪范围与混入文本（>50% 面积落在裁剪框内的块）")
for p in d.pages:
    for (kind, num, cap) in L.find_captions(p):
        if kind != "figure":
            continue
        fi = E.locate_figure(p, cap, num)
        w, h = fi.bbox[2] - fi.bbox[0], fi.bbox[3] - fi.bbox[1]
        print("\nFigure %s (page %d) notes=%s" % (num, p.index + 1, fi.notes))
        print("  bbox=(%.1f,%.1f,%.1f,%.1f) %.0fx%.0fpt 占页面 %.0f%%"
              % (fi.bbox[0], fi.bbox[1], fi.bbox[2], fi.bbox[3], w, h,
                 100.0 * w * h / (p.width * p.height)))
        mixed = []
        for b in p.blocks:
            if b.role in ("header", "footer", "empty") or b is cap:
                continue
            it = L.rect_inter(b.bbox, fi.bbox)
            if it and L.rect_area(it) > 0.5 * L.rect_area(b.bbox):
                mixed.append((round(b.y0, 1), len(" ".join(b.text().split())), " ".join(b.text().split())[:60]))
        tot = sum(m[1] for m in mixed)
        print("  混入 %d 个非图块，共 %d 个字符，例如：" % (len(mixed), tot))
        for m in sorted(mixed)[:8]:
            print("     y=%6.1f chars=%3d %r" % m)
        if len(mixed) > 8:
            print("     ...（共 %d 个）" % len(mixed))

print("\n### 公式裁剪 vs 原公式真实范围")
man_bbox = {}
for p in d.pages:
    pass
# 直接从 manifest 读
import json
man = json.loads((Path(sys.argv[2]) / "extracted" / "manifest.json").read_text(encoding="utf-8"))
for e in man["equations"]:
    bb = e["bbox"]
    page = doc[e["page"]]
    band = [w for w in page.get_text("words") if not (w[3] < bb[1] - 5 or w[1] > bb[3] + 5)]
    body = [w for w in band if not (w[0] > 505 and w[4].startswith("("))]   # 排除行末公式编号
    gx0 = min([w[0] for w in body], default=0)
    gx1 = max([w[2] for w in body], default=0)
    gy0 = min([w[1] for w in body], default=0)
    gy1 = max([w[3] for w in body], default=0)
    miss_x = max(0.0, gx1 - bb[2])
    miss_y = max(0.0, bb[1] - gy0)
    print("eq%02d num=%-2r conf=%.2f 裁剪=(%.0f,%.0f,%.0f,%.0f) 原公式字形范围=(%.0f,%.0f,%.0f,%.0f) "
          "右缺%.0fpt 上缺%.0fpt raw_text=%r"
          % (e["index"], e["number"], e["confidence"], bb[0], bb[1], bb[2], bb[3],
             gx0, gy0, gx1, gy1, miss_x, miss_y, e["raw_text"]))
    print("     latex = %r" % e["latex"])
