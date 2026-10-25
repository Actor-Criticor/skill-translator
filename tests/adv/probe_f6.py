# -*- coding: utf-8 -*-
"""probe_f6.py —— 用 verify.py 自己的函数复算 F6 数字集合，定位差异来源。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\scripts")
import verify as V

work = Path(sys.argv[1])
src_md = (work / "extracted" / "source" / "source.md").read_text(encoding="utf-8")
tex = (work / "paper" / "paper.tex").read_text(encoding="utf-8")

body, refs = V.strip_source_for_numbers(src_md)
s = V.extract_numbers(body) + V.extract_numbers(refs)
prose, r2 = V.strip_tex_for_prose(tex, keep_inline_math=True)
t = V.extract_numbers(prose) + V.extract_numbers(r2)
only_s, only_t = V.multiset_diff(s, t)
print("源文显著数字 %d 个 -> %s" % (len(s), sorted(set(s))))
print()
print("译文显著数字 %d 个 -> %s" % (len(t), sorted(set(t))))
print()
print("源文有而译文无:", only_s)
print("译文多出      :", only_t)
print()
print("--- 译文侧 text 里出现 '12.4' 的片段检查（样张用） ---")
for tok in only_t:
    for m in V.NUM_RE.finditer(prose):
        if m.group(0).replace(",", "") == tok:
            print("  %r 上下文: %r" % (tok, prose[max(0, m.start() - 60):m.end() + 20].replace("\n", " ")))
            break
