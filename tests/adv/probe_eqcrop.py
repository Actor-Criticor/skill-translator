# -*- coding: utf-8 -*-
"""probe_eqcrop.py —— 量化对比“eqNN_orig.png 覆盖范围”与“原公式真实占位范围”。"""
import json
import sys
from pathlib import Path

import fitz

pdf = sys.argv[1]
work = Path(sys.argv[2])
man = json.loads((work / "extracted" / "manifest.json").read_text(encoding="utf-8"))
doc = fitz.open(pdf)

for e in man["equations"]:
    pno = e["page"]
    bb = e["bbox"]
    page = doc[pno]
    print("\n%s 原编号=%r 页=%d bbox=(%.1f,%.1f,%.1f,%.1f) 宽=%.1fpt 置信度=%.2f"
          % ("eq%02d" % e["index"], e["number"], pno + 1, bb[0], bb[1], bb[2], bb[3],
             bb[2] - bb[0], e["confidence"]))
    band = [w for w in page.get_text("words")
            if not (w[3] < bb[1] - 5 or w[1] > bb[3] + 5)]
    band.sort(key=lambda w: (round(w[1]), w[0]))
    if band:
        print("   同一水平带内全部词：")
        for w in band:
            print("      x=%7.2f..%7.2f y=%6.1f  %r" % (w[0], w[2], w[1], w[4]))
        gx1 = max(w[2] for w in band)
        print("   带内最右字形 x=%.1f ；裁剪右边界 x=%.1f ；未覆盖 %.1fpt"
              % (gx1, bb[2], max(0.0, gx1 - bb[2])))
    print("   raw_text=%r" % e["raw_text"])
    print("   latex   =%r" % e["latex"])
