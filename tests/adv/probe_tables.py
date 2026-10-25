# -*- coding: utf-8 -*-
"""probe_tables.py —— 复算表格定位链路，定位截图兜底范围为何异常。"""
import sys
from pathlib import Path

SKILL = Path(r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\scripts")
sys.path.insert(0, str(SKILL))
import fitz
import p2zlib as L
import extract as E

pdf = sys.argv[1]
d = L.load_document(pdf)
doc = fitz.open(pdf)
pages = [doc[i] for i in range(doc.page_count)]

for p in d.pages:
    caps = [(num, cap) for (kind, num, cap) in L.find_captions(p) if kind == "table"]
    if not caps:
        # 顺带看这一页 PyMuPDF 自己找到了什么"表"
        for strat in ("lines", "lines_strict", "text"):
            try:
                f = pages[p.index].find_tables(strategy=strat)
            except Exception as ex:
                print("  page%d %s -> 异常 %s" % (p.index + 1, strat, ex)); continue
            for t in getattr(f, "tables", []) or []:
                rows = t.extract()
                bb = tuple(round(float(v), 1) for v in t.bbox)
                ar = (bb[2] - bb[0]) * (bb[3] - bb[1]) / (p.width * p.height)
                print("  page%d(无表题) %s bbox=%s rows=%d cols=%d area=%.2f"
                      % (p.index + 1, strat, bb, len(rows),
                         max((len(r) for r in rows), default=0), ar))
        continue
    for (num, capb) in caps:
        print("\n=== Table %s (page %d) caption bbox=%s" % (num, p.index + 1,
              tuple(round(v, 1) for v in capb.bbox)))
        region, note = E._table_region(p, capb)
        print("  _table_region -> %s  %s" % (region and tuple(round(v, 1) for v in region), note))
        # 截图兜底的实际页面搜索逻辑
        pd_page = None
        for q in d.pages:
            if capb.bbox and E._bbox_on_page(capb.bbox, q):
                pd_page = q
                break
        print("  pd_page.index = %s   （题注真正所在页 index = %d）"
              % (pd_page.index if pd_page else None, p.index))
        if pd_page is not None:
            rect = E._table_crop_rect(pages[pd_page.index], capb.bbox, pd_page, d)
            print("  _table_crop_rect -> %s  （尺寸 %.1fx%.1f）"
                  % (tuple(round(v, 1) for v in rect), rect[2] - rect[0], rect[3] - rect[1]))
        # PyMuPDF 原生 find_tables 结果
        for strat in ("lines", "lines_strict", "text"):
            try:
                f = pages[p.index].find_tables(strategy=strat)
            except Exception as ex:
                print("  find_tables(%s) -> 异常 %s" % (strat, ex)); continue
            for t in getattr(f, "tables", []) or []:
                rows = t.extract()
                bb = tuple(round(float(v), 1) for v in t.bbox)
                ov = L.rect_inter(bb, capb.bbox)
                dist = None
                if ov is not None:
                    dist = 0.0
                num2, cap2, dist2 = E._nearest_caption(bb, caps)
                print("  find_tables(%s) bbox=%s rows=%d cols=%d quality=%.2f "
                      "caption=%s dist=%.0f"
                      % (strat, bb, len(rows), max((len(r) for r in rows), default=0),
                         E._table_quality(rows), repr(num2), dist2))
                for r in rows:
                    print("       row: %s" % (" | ".join(str(c) for c in r)))
