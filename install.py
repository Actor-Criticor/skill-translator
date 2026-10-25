# -*- coding: utf-8 -*-
"""install.py —— 把 skill-translator 安装到用户级 skills 目录（任何工作目录都能调用）。

DSH 的技能发现顺序（优先级从高到低）：
    <项目>/.dsh/skills  →  <项目>/.agents/skills  →  ~/.dsh/skills  →  ~/.agents/skills

把本目录复制到 ~/.dsh/skills/skill-translator 之后，在任意项目里都能 `/skill-translator` 调用。

用法：
    python install.py                          # 安装到 %USERPROFILE%\\.dsh\\skills（或 $HOME/.dsh/skills）
    python install.py --target D:\\my\\skills   # 装到指定 skills 根目录
    python install.py --force                  # 覆盖已有安装
    python install.py --dry-run                # 只看会做什么
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence

HERE = Path(os.path.abspath(__file__)).parent
NAME = "skill-translator"
# 不必随包复制的内容（测试工作目录、审计证据、编译中间产物、缓存）
SKIP_DIRS = {"adv", "__pycache__", ".pytest_cache", ".git", ".idea", ".vscode"}
SKIP_SUFFIXES = {".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk", ".pyc"}
KEEP_LOGS = {"build_sample.log"}


def default_root() -> Path:
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    return home / "skills"


def ignore(dirpath: str, names):
    """部署时排除：下划线开头的目录（`_work`/`_single`/`_adv_check` 等测试产物）、
    审计证据目录、LaTeX 编译中间文件、字节码缓存。"""
    ignored = []
    for n in names:
        p = Path(dirpath) / n
        # `.git`/`.gitignore` 等点开头的是仓库元数据，不属于技能内容；
        # `_xxx` 是测试遗留的工作目录。两者都不部署。
        if n in SKIP_DIRS or n.startswith("_") or n.startswith("."):
            ignored.append(n)
            continue
        if p.suffix.lower() in SKIP_SUFFIXES and n not in KEEP_LOGS:
            ignored.append(n)
            continue
        if n.endswith(".pyc"):
            ignored.append(n)
    return ignored


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="把 skill-translator 安装到用户级 skills 目录")
    ap.add_argument("--target", default="", help="skills 根目录（默认 ~/.dsh/skills）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的安装")
    ap.add_argument("--dry-run", action="store_true", help="只显示计划，不实际复制")
    args = ap.parse_args(argv)

    root = Path(args.target).expanduser() if args.target else default_root()
    dest = root / NAME
    print(f"[*] 源目录  ：{HERE}")
    print(f"[*] 目标目录：{dest}")

    if not (HERE / "SKILL.md").exists():
        print("[x] 源目录里没有 SKILL.md，安装包不完整。", file=sys.stderr)
        return 2
    if dest.exists() and not args.force:
        print(f"[x] 目标已存在：{dest}\n    用 --force 覆盖，或先手动删除。", file=sys.stderr)
        return 2
    if os.path.abspath(str(dest)) == os.path.abspath(str(HERE)):
        print("[=] 源与目标相同，无需安装（已在用户级目录中）。")
        return 0

    if args.dry_run:
        print("[=] --dry-run：将会复制 scripts/ references/ tests/ SKILL.md README.md install.py")
        return 0

    if dest.exists():
        shutil.rmtree(dest)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERE, dest, ignore=ignore)

    n_files = sum(1 for _ in dest.rglob("*") if _.is_file())
    print(f"[v] 已安装：{dest}（{n_files} 个文件）")
    print("[*] 自检一下：")
    print(f'    python "{dest / "scripts" / "env_check.py"}"')
    print("[*] 之后在任意目录都可以直接对 agent 说「把这篇英文 PDF 转成中文版」，或输入 /skill-translator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
