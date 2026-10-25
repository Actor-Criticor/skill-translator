# -*- coding: utf-8 -*-
"""dump_built.py —— 检查 build.py 产出的中文 PDF：分页、中文、公式编号、图/表图像。"""
import re
import sys

import fitz

pdf = fitz.open(sys.argv[1])
print("pages =", pdf.page_count)
CJK = re.compile(r"[\u4e00-\u9fff]")
for i in range(pdf.page_count):
    p = pdf[i]
    txt = p.get_text()
    cjk = len(CJK.findall(txt))
    nums = re.findall(r"\((\d+)\)", txt)
    imgs = p.get_images(full=True)
    print("\n===== page %d  CJK字符=%d  图像对象=%d  文本长度=%d" % (i + 1, cjk, len(imgs), len(txt)))
    for im in imgs:
        try:
            rects = p.get_image_rects(im[0])
        except Exception:
            rects = []
        for r in rects:
            print("    image xref=%s %dx%d px  placed=%.0fx%.0fpt (页面 %.0fx%.0f) 占页面积 %.0f%%"
                  % (im[0], im[2], im[3], r.width, r.height, p.rect.width, p.rect.height,
                     100.0 * r.width * r.height / (p.rect.width * p.rect.height)))
    # 段落预览
    for b in p.get_text("blocks")[:200]:
        t = " ".join(b[4].split())
        if t:
            print("    y=%6.1f %r" % (b[1], t[:100]))
    if nums:
        print("    括号数字（含公式编号）:", nums)
