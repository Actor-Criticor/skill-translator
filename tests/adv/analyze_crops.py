# -*- coding: utf-8 -*-
"""analyze_crops.py —— 用几何量客观评估裁剪质量（测试辅助，不依赖目视）。

对 work/extracted/doc.json 里的每个 FigureInfo / EquationInfo：
  * 报出裁剪 bbox 尺寸、页面占比；
  * 报出裁剪框内落进了哪些“正文/题注/表格”文本块，以及占用的字符数；
  * 对公式：报出裁剪框是否覆盖了该公式在 PDF 中全部字形的外接框。
"""
import json
import sys
from pathlib import Path

import fitz

D = Path(sys.argv[1])                       # work/extracted
doc_json = json.loads((D / "doc.json").read_text(encoding="utf-8"))


def blocks_of(page):
    return [b for b in page.get_text("dict")["blocks"] if b.get("type", 0) == 0]


pdf = fitz.open(sys.argv[2])

print("### 图片裁剪评估")
for f in doc_json.get("figures", []):
    pno = f["page"]
    bb = f["bbox"]
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    page = pdf[pno]
    area_ratio = (w * h) / (page.rect.width * page.rect.height)
    print(f"\nfig{f['index']} page={pno+1} bbox=({bb[0]:.1f},{bb[1]:.1f},{bb[2]:.1f},{bb[3]:.1f}) "
          f"size={w:.1f}x{h:.1f}pt page_area={area_ratio*100:.1f}% notes={f.get('notes')}")
    inside = []
    for b in blocks_of(page):
        x0, y0, x1, y1 = b["bbox"]
        # 交集比例
        ix = max(0.0, min(x1, bb[2]) - max(x0, bb[0]))
        iy = max(0.0, min(y1, bb[3]) - max(y0, bb[1]))
        ia = ix * iy
        ba = max(1e-6, (x1 - x0) * (y1 - y0))
        if ia / ba > 0.5:
            txt = " ".join("".join(s["text"] for l in bl["lines"] for s in l["spans"])
                           for bl in [b])
            txt = " ".join(txt.split())
            inside.append((round(y0, 1), len(txt), txt[:80]))
    inside.sort()
    for (y, n, t) in inside:
        print(f"    内含文本 y={y} chars={n} :: {t!r}")

print("\n\n### 公式裁剪评估（相对原页字形外接框）")
for e in doc_json.get("equations", []):
    pno = e["page"]
    bb = e["bbox"]
    page = pdf[pno]
    # 该公式所在行带 (y0-3, y1+3) 内的全部字形范围
    words = page.get_text("words")
    band = [w for w in words if not (w[3] < bb[1] - 4 or w[1] > bb[3] + 4)]
    if band:
        gx0 = min(w[0] for w in band); gx1 = max(w[2] for w in band)
    else:
        gx0 = gx1 = 0
    print(f"\n{e['id']} page={pno+1} num={e.get('number')!r} bbox="
          f"({bb[0]:.1f},{bb[1]:.1f},{bb[2]:.1f},{bb[3]:.1f}) size={bb[2]-bb[0]:.1f}x{bb[3]-bb[1]:.1f}pt"
          f" conf={e.get('confidence')}")
    print(f"    同行带字形 x 范围 = {gx0:.1f}..{gx1:.1f}  (裁剪右边界 {bb[2]:.1f}) "
          f"-> 未覆盖宽度 {max(0.0, gx1-bb[2]):.1f}pt")
    txt = " ".join(w[4] for w in sorted(band, key=lambda w: (round(w[1]), w[0])))
    print(f"    同行带文本: {txt!r}")
