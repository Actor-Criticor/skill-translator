# -*- coding: utf-8 -*-
"""env_check.py —— 环境自检（skill-translator 上手第一步；跨平台）。

检查什么：
  1. Python 版本与 PyMuPDF（PDF 解析的核心依赖，唯一必需的第三方库）；
  2. LaTeX 引擎 xelatex（生成中文 PDF 必需）+ ctex 宏包 + Fandol 中文字体（兜底字体）；
  3. 本机可用的中文字体（决定 build.py 会选哪个字体集）；
  4. 输出目录可写。

用法：
    python env_check.py                 # 自检并给出结论与安装指引
    python env_check.py --install-deps  # 缺 PyMuPDF 时自动 pip 安装
    python env_check.py --json          # 只输出 JSON（供程序调用）
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2zpaths import abs_path  # noqa: E402  绝对路径（勿用 Path.resolve）

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

FONT_DIRS_WIN = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
FONT_DIRS_MAC = [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                 Path.home() / "Library" / "Fonts"]
FONT_DIRS_LINUX = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                   Path.home() / ".fonts", Path.home() / ".local/share/fonts"]

CJK_FONT_FILES = {
    "SimSun（宋体）": ["simsun.ttc", "simsun.ttf"],
    "SimHei（黑体）": ["simhei.ttf"],
    "Microsoft YaHei（微软雅黑）": ["msyh.ttc"],
    "Songti SC（宋体-简）": ["Songti.ttc", "Songti SC.ttf"],
    "Heiti SC（黑体-简）": ["Heiti.ttc"],
    "PingFang SC（苹方）": ["PingFang.ttc"],
    "Noto Serif CJK SC": ["NotoSerifCJK-Regular.ttc", "NotoSerifCJKsc-Regular.otf"],
    "Noto Sans CJK SC": ["NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf"],
    "Source Han Serif SC（思源宋体）": ["SourceHanSerifSC-Regular.otf"],
    "Source Han Sans SC（思源黑体）": ["SourceHanSansSC-Regular.otf"],
    "WenQuanYi（文泉驿）": ["wqy-zenhei.ttc", "wqy-microhei.ttc"],
    "Fandol（TeX Live 自带宋体）": ["FandolSong-Regular.otf"],
}

INSTALL_HINTS = {
    "win32": {
        "latex": "安装 TeX Live：https://tug.org/texlive/ （或 MiKTeX：https://miktex.org/）；"
                 "安装后确认 xelatex 在 PATH 中。中文支持由 ctex + Fandol 提供，通常已随 TeX Live 安装。",
        "pymupdf": "在命令行执行：python -m pip install pymupdf",
        "fonts": "Windows 自带宋体/黑体，一般无需额外安装。",
    },
    "darwin": {
        "latex": "安装 MacTeX：brew install --cask mactex  或  https://tug.org/mactex/",
        "pymupdf": "python3 -m pip install pymupdf",
        "fonts": "系统自带 Songti SC / Heiti SC；若缺失可 brew install --cask font-noto-serif-cjk-sc",
    },
    "linux": {
        "latex": "Debian/Ubuntu：sudo apt install texlive-xetex texlive-lang-chinese texlive-fonts-recommended texlive-latex-extra；"
                 "Fedora：sudo dnf install texlive-xetex texlive-collection-langchinese",
        "pymupdf": "python3 -m pip install pymupdf",
        "fonts": "Debian/Ubuntu：sudo apt install fonts-noto-cjk fonts-noto-cjk-extra",
    },
}


def _hints() -> Dict[str, str]:
    key = "win32" if IS_WIN else ("darwin" if IS_MAC else "linux")
    return INSTALL_HINTS[key]


def check_python() -> Tuple[bool, str]:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 8)
    return ok, f"Python {platform.python_version()}（{'满足' if ok else '需要 3.8+'}）"


def check_pymupdf() -> Tuple[bool, str]:
    try:
        import fitz  # noqa: F401
        ver = getattr(fitz, "__doc__", "")
        # 去掉库自述里重复的 "PyMuPDF" 前缀，避免出现 "PyMuPDF PyMuPDF 1.26.5"
        short = ver.replace("PyMuPDF", "").strip().split(":")[0].strip()
        tables = hasattr(fitz.Page, "find_tables")
        return True, f"PyMuPDF {short}；表格识别 find_tables={'可用' if tables else '不可用（将回退截图）'}"
    except Exception as e:
        return False, f"未安装 PyMuPDF（{e}）"


def try_install_pymupdf() -> bool:
    print("[*] 尝试自动安装 PyMuPDF：python -m pip install pymupdf")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "pymupdf"],
                           capture_output=True, text=True, timeout=900, errors="replace")
        print((r.stdout or "")[-1500:])
        if r.returncode != 0:
            print((r.stderr or "")[-800:])
        return r.returncode == 0
    except Exception as e:
        print(f"[x] 安装失败：{e}")
        return False


def check_latex() -> Tuple[bool, str, Optional[str]]:
    engine = None
    for name in ("xelatex", "latexmk"):
        p = shutil.which(name)
        if p:
            engine = p
            break
    if not engine:
        return False, "未找到 xelatex / latexmk", None
    ver = ""
    try:
        r = subprocess.run([engine, "--version"], capture_output=True, text=True,
                           timeout=30, errors="replace")
        ver = (r.stdout or "").splitlines()[0].strip() if r.stdout else ""
    except Exception:
        pass
    return True, f"{Path(engine).name} -> {engine}；{ver}", engine


def check_ctex() -> Tuple[bool, str]:
    kpse = shutil.which("kpsewhich")
    if not kpse:
        return False, "未找到 kpsewhich（TeX 发行版可能不完整）"
    missing = []
    for f in ("ctexart.cls", "ctex.sty"):
        try:
            r = subprocess.run([kpse, f], capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or not (r.stdout or "").strip():
                missing.append(f)
        except Exception:
            missing.append(f)
    if missing:
        return False, "缺少 ctex 宏包：" + ", ".join(missing) + "（LaTeX 侧中文支持依赖它）"
    return True, "ctex 宏包可用（ctexart.cls / ctex.sty）"


def check_fandol() -> Tuple[bool, str]:
    kpse = shutil.which("kpsewhich")
    if not kpse:
        return False, "无法检查（缺 kpsewhich）"
    found = []
    for f in ("FandolSong-Regular.otf", "FandolHei-Regular.otf", "FandolFang-Regular.otf"):
        try:
            r = subprocess.run([kpse, f], capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and (r.stdout or "").strip():
                found.append(f)
        except Exception:
            pass
    if len(found) >= 2:
        return True, f"Fandol 兜底字体可用（{', '.join(found)}）"
    return False, ("未找到 TeX Live 的 Fandol 字体；若本机也没有系统中文字体，"
                   "中文将无法渲染。装 texlive-fonts-extra / texlive-lang-chinese 可补上。")


def scan_system_fonts() -> Dict[str, Optional[str]]:
    dirs = FONT_DIRS_WIN if IS_WIN else (FONT_DIRS_MAC if IS_MAC else FONT_DIRS_LINUX)
    present: Dict[str, Optional[str]] = {}
    for label, names in CJK_FONT_FILES.items():
        hit = None
        for d in dirs:
            if not d or not d.exists():
                continue
            for n in names:
                p = d / n
                if p.exists():
                    hit = str(p)
                    break
            if hit:
                break
        if not hit:
            # fontconfig 探测（Linux/mac 常见）
            fc = shutil.which("fc-list")
            if fc:
                try:
                    r = subprocess.run([fc, ":", "file", "family"], capture_output=True,
                                       text=True, timeout=30, errors="replace")
                    if names[0].split(".")[0].lower() in (r.stdout or "").lower():
                        hit = "fontconfig"
                except Exception:
                    pass
        present[label] = hit
    return present


def check_writable(path: Path) -> Tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".p2z_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, f"可写：{path}"
    except Exception as e:
        return False, f"不可写：{path}（{e}）"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="skill-translator 环境自检")
    ap.add_argument("--install-deps", action="store_true", help="缺 PyMuPDF 时自动 pip 安装")
    ap.add_argument("--work", default="translator-work", help="工作目录（用于可写性检查）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args(argv)

    rep: Dict[str, Any] = {"platform": platform.platform(), "python": platform.python_version()}
    ok_py, msg_py = check_python()
    ok_pm, msg_pm = check_pymupdf()
    if not ok_pm and args.install_deps:
        if try_install_pymupdf():
            ok_pm, msg_pm = check_pymupdf()
    ok_lx, msg_lx, engine = check_latex()
    ok_ct, msg_ct = check_ctex() if ok_lx else (False, "跳过（无 LaTeX 引擎）")
    ok_fd, msg_fd = check_fandol() if ok_lx else (False, "跳过（无 LaTeX 引擎）")
    fonts = scan_system_fonts()
    ok_wr, msg_wr = check_writable(abs_path(args.work))

    rep.update({
        "python_ok": ok_py, "python_msg": msg_py,
        "pymupdf_ok": ok_pm, "pymupdf_msg": msg_pm,
        "latex_ok": ok_lx, "latex_msg": msg_lx, "engine": engine,
        "ctex_ok": ok_ct, "ctex_msg": msg_ct,
        "fandol_ok": ok_fd, "fandol_msg": msg_fd,
        "system_cjk_fonts": {k: v for k, v in fonts.items() if v},
        "writable_ok": ok_wr, "writable_msg": msg_wr,
    })
    has_cjk = bool(rep["system_cjk_fonts"]) or ok_fd
    required_ok = ok_py and ok_pm and ok_lx and ok_ct and has_cjk
    rep["required_ok"] = required_ok

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if required_ok else 1

    h = _hints()
    mark = lambda b: "[v]" if b else "[x]"  # noqa: E731
    print("=== skill-translator 环境自检 ===")
    print(f"平台        : {rep['platform']}")
    print(f"{mark(ok_py)} Python      : {msg_py}")
    print(f"{mark(ok_pm)} PyMuPDF     : {msg_pm}")
    print(f"{mark(ok_lx)} LaTeX 引擎  : {msg_lx}")
    print(f"{mark(ok_ct)} ctex        : {msg_ct}")
    print(f"{mark(ok_fd)} Fandol 字体 : {msg_fd}")
    print(f"{mark(ok_wr)} 工作目录    : {msg_wr}")
    print("\n系统中文字体：")
    if rep["system_cjk_fonts"]:
        for k, v in rep["system_cjk_fonts"].items():
            print(f"  [v] {k}  -> {v}")
    else:
        print("  [!] 未在标准字体目录发现常见中文字体；将使用 TeX Live 的 Fandol 兜底。")

    print("\n--- 结论 ---")
    if required_ok:
        print("[v] 环境可用，可以开始：python extract.py <英文期刊.pdf> --out translator-work")
        if not ok_fd:
            print("    提示：本机没有 Fandol，换到别的电脑时可能需要装 texlive-lang-chinese。")
    else:
        print("[x] 环境不满足要求，按下面指引补齐：")
        if not ok_py:
            print("  * Python 需要 3.8 或更高版本。")
        if not ok_pm:
            print("  * " + h["pymupdf"] + ("（或重跑本脚本加 --install-deps）" if not args.install_deps else ""))
        if not ok_lx or not ok_ct:
            print("  * " + h["latex"])
        if not has_cjk:
            print("  * " + h["fonts"])
        if not ok_wr:
            print("  * 换一个有写权限的工作目录（--work 指定）。")
    print("\n说明：本工具链只依赖 Python + PyMuPDF（解析）与 xelatex + ctex（排版），"
          "不需要 pandoc、不需要联网、不需要任何商业翻译 API。")
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
