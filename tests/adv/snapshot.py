# -*- coding: utf-8 -*-
"""snapshot.py —— 把当前技能包脚本+模板冻结到 tests/adv/snapshot_v1/ 并记录哈希（测试辅助）。"""
import hashlib
import shutil
from pathlib import Path

SKILL = Path(r"D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator")
DST = Path(__file__).resolve().parent / "snapshot_v1"
if DST.exists():
    shutil.rmtree(DST)
(DST / "scripts").mkdir(parents=True)
(DST / "references").mkdir(parents=True)

rows = []
for sub in ("scripts", "references"):
    for src in sorted((SKILL / sub).iterdir()):
        if src.is_dir() or src.suffix == ".pyc":
            continue
        dst = DST / sub / src.name
        shutil.copy2(src, dst)
        h = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
        rows.append((str(src.relative_to(SKILL)), src.stat().st_size, h))

for name in ("SKILL.md", "README.md"):
    p = SKILL / name
    if p.exists():
        shutil.copy2(p, DST / name)
        rows.append((name, p.stat().st_size,
                     hashlib.sha1(p.read_bytes()).hexdigest()[:16]))

out = ["# 冻结快照（snapshot_v1）",
       "",
       "测试期间技能包被并发修改，本快照为本轮报告引用的版本。",
       ""]
out.append("| 文件 | 字节 | sha1(前16) |")
out.append("|---|---|---|")
for (n, s, h) in rows:
    out.append("| %s | %d | %s |" % (n, s, h))
out.append("")
out.append("冻结时间：见文件系统时间；快照目录：tests/adv/snapshot_v1/")
(DST / "SNAPSHOT.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
