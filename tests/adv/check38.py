# -*- coding: utf-8 -*-
"""check38.py —— 用 ast 以 3.8 语法解析全部脚本；并静态找 3.9+/3.10+ 运行时 API。"""
import ast
import re
import sys
from pathlib import Path

sn = Path(r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\tests\adv\snapshot_v1\scripts")
BAD_API = [
    (r"\bremoveprefix\b", "str.removeprefix (3.9+)"),
    (r"\bremovesuffix\b", "str.removesuffix (3.9+)"),
    (r"\bfunctools\.cache\b", "functools.cache (3.9+)"),
    (r"\bis_relative_to\b", "PurePath.is_relative_to (3.9+)"),
    (r"\bzoneinfo\b", "zoneinfo (3.9+)"),
    (r"\bgraphlib\b", "graphlib (3.9+)"),
    (r"\bmath\.lcm\b", "math.lcm (3.9+)"),
    (r"\bmath\.nextafter\b", "math.nextafter (3.9+)"),
    (r"BooleanOptionalAction", "argparse.BooleanOptionalAction (3.9+)"),
    (r"zip\([^)]*strict\s*=", "zip(strict=) (3.10+)"),
    (r"\bpairwise\b", "itertools.pairwise (3.10+)"),
    (r"\bExceptionGroup\b", "ExceptionGroup (3.11+)"),
    (r"\bSelf\b", "typing.Self (3.11+)"),
]

for p in sorted(sn.glob("*.py")):
    src = p.read_text(encoding="utf-8")
    try:
        ast.parse(src, filename=str(p), feature_version=(3, 8))
        syn = "OK(3.8)"
    except SyntaxError as e:
        syn = "3.8 语法失败: %s" % e
    hits = []
    for (pat, desc) in BAD_API:
        if re.search(pat, src):
            hits.append(desc)
    # 运行时（非注解）位置的 PEP585 泛型下标，如 list[int]()
    runtime_generic = re.findall(r"(?<![\w.])(?:list|dict|tuple|set|frozenset)\[[^\]]*\]\s*\(", src)
    print("%-16s %-16s 高危API=%s 运行时泛型下标=%s" % (p.name, syn, hits or "无", runtime_generic or "无"))
