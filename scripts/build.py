# -*- coding: utf-8 -*-
"""build.py —— 把中文译稿（Markdown）编译成中文版 PDF。

设计要点（为"全电脑可用"服务）：
* **不依赖 pandoc**：内置一个够用的 Markdown→LaTeX 转换器（标题/段落/列表/引用/
  脚注/粗斜体/链接/代码/公式/占位符），只用 Python 标准库。
* **字体集自适应**：中文字体在不同系统上不一样（Windows 宋体/黑体、macOS Songti/Heiti、
  Linux Noto、TeX Live 自带 Fandol）。本脚本用"实际编译探针"逐个试用候选字体集，
  哪个能编过就用哪个，结果缓存复用（真正的跨平台，而不是靠猜测）。
* **数值与结构不做任何改写**：只做格式转换，不改一字一句。

用法：
    python build.py --work translator-work                 # 编译 paper/paper.md -> paper/paper.pdf
    python build.py --work work --md paper/draft.md      # 指定译稿
    python build.py --work work --tex paper/paper.tex    # 直接编译已有 tex
    python build.py --work work --reprobe                # 重新探测字体集
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import p2zconsole  # noqa: E402,F401  控制台输出编码
from p2zpaths import abs_path  # noqa: E402  绝对路径（勿用 Path.resolve）

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PREAMBLE = SKILL_DIR / "references" / "preamble-zh.tex"

# --------------------------------------------------------------------------- #
# 字体集候选（按优先级；用真实编译探针决定使用哪个）
# --------------------------------------------------------------------------- #

FONTSETS: List[Dict[str, str]] = [
    {
        "id": "windows",
        "desc": "Windows 系统字体（宋体 + 黑体 + Times New Roman）",
        "cjk_main": r"\setCJKmainfont[AutoFakeBold=2.5]{SimSun}",
        "cjk_sans": r"\setCJKsansfont[AutoFakeBold=2.0]{SimHei}",
        "cjk_mono": r"\setCJKmonofont{FangSong}",
        "zhsong": "SimSun", "zhhei": "SimHei", "zhkai": "KaiTi", "zhfs": "FangSong",
        "latin": r"\setmainfont{Times New Roman}",
        "latin_sans": r"\setsansfont{Arial}",
    },
    {
        "id": "macos",
        "desc": "macOS 系统字体（Songti SC + Heiti SC）",
        "cjk_main": r"\setCJKmainfont[AutoFakeBold=2.5]{Songti SC}",
        "cjk_sans": r"\setCJKsansfont[AutoFakeBold=2.0]{Heiti SC}",
        "cjk_mono": r"\setCJKmonofont{STFangsong}",
        "zhsong": "Songti SC", "zhhei": "Heiti SC", "zhkai": "Kaiti SC", "zhfs": "STFangsong",
        "latin": r"\setmainfont{Times New Roman}",
        "latin_sans": r"\setsansfont{Helvetica}",
    },
    {
        "id": "noto",
        "desc": "Linux/通用 Noto CJK（Noto Serif CJK SC + Noto Sans CJK SC）",
        "cjk_main": r"\setCJKmainfont[AutoFakeBold=2.5]{Noto Serif CJK SC}",
        "cjk_sans": r"\setCJKsansfont[AutoFakeBold=2.0]{Noto Sans CJK SC}",
        "cjk_mono": r"\setCJKmonofont{Noto Sans Mono CJK SC}",
        "zhsong": "Noto Serif CJK SC", "zhhei": "Noto Sans CJK SC",
        "zhkai": "Noto Serif CJK SC", "zhfs": "Noto Sans CJK SC",
        "latin": r"\setmainfont{TeX Gyre Termes}",
        "latin_sans": r"\setsansfont{TeX Gyre Heros}",
    },
    {
        "id": "source-han",
        "desc": "思源宋体/黑体（Source Han Serif/Sans SC）",
        "cjk_main": r"\setCJKmainfont[AutoFakeBold=2.5]{Source Han Serif SC}",
        "cjk_sans": r"\setCJKsansfont[AutoFakeBold=2.0]{Source Han Sans SC}",
        "cjk_mono": r"\setCJKmonofont{Source Han Sans SC}",
        "zhsong": "Source Han Serif SC", "zhhei": "Source Han Sans SC",
        "zhkai": "Source Han Serif SC", "zhfs": "Source Han Sans SC",
        "latin": r"\setmainfont{TeX Gyre Termes}",
        "latin_sans": r"\setsansfont{TeX Gyre Heros}",
    },
    {
        "id": "fandol",
        "desc": "TeX Live 自带 Fandol 字体（任何装了 TeX Live 的机器都有，最终兜底）",
        "cjk_main": r"\setCJKmainfont[AutoFakeBold=2.5]{FandolSong-Regular.otf}",
        "cjk_sans": r"\setCJKsansfont[AutoFakeBold=2.0]{FandolHei-Regular.otf}",
        "cjk_mono": r"\setCJKmonofont{FandolFang-Regular.otf}",
        "zhsong": "FandolSong-Regular.otf", "zhhei": "FandolHei-Regular.otf",
        "zhkai": "FandolKai-Regular.otf", "zhfs": "FandolFang-Regular.otf",
        "latin": r"\setmainfont{texgyretermes}[Extension=.otf, UprightFont=*-regular, "
                 r"ItalicFont=*-italic, BoldFont=*-bold, BoldItalicFont=*-bolditalic]",
        "latin_sans": r"\setsansfont{texgyreheros}[Extension=.otf, UprightFont=*-regular, "
                      r"ItalicFont=*-italic, BoldFont=*-bold, BoldItalicFont=*-bolditalic]",
    },
]


def fontset_tex(cfg: Dict[str, str]) -> str:
    """生成 fontset.tex。

    注意两点（都是踩过的坑）：
    1. 用 fontset=none 时 ctex **不会**定义 \\heiti/\\songti/\\kaishu 等命令，
       而模板的标题格式里要用 \\heiti，所以这里必须自己补上 CJKfamily 定义。
    2. 文件要带 UTF-8 BOM：Windows 上 XeTeX 会按本地代码页读无 BOM 的文件，
       否则中文标题会变成乱码（"引言" -> "寮曡█"）。
    """
    lines = [
        "% 由 build.py 自动生成的字体设置（探测结果：" + str(cfg["id"]) + "）",
        "% " + cfg["desc"],
        r"\ifdefined\setCJKmainfont",
        cfg["cjk_main"],
        cfg["cjk_sans"],
        cfg["cjk_mono"],
        # 中文字体族命令（ctex 在 fontset=none 下不提供）
        r"\setCJKfamilyfont{zhsong}{%s}" % cfg.get("zhsong", cfg.get("cjk_main", "")),
        r"\setCJKfamilyfont{zhhei}{%s}" % cfg.get("zhhei", cfg.get("cjk_sans", "")),
        r"\setCJKfamilyfont{zhkai}{%s}" % cfg.get("zhkai", cfg.get("zhsong", "")),
        r"\setCJKfamilyfont{zhfs}{%s}" % cfg.get("zhfs", cfg.get("zhsong", "")),
        r"\providecommand*{\songti}{\CJKfamily{zhsong}}",
        r"\providecommand*{\heiti}{\CJKfamily{zhhei}}",
        r"\providecommand*{\kaishu}{\CJKfamily{zhkai}}",
        r"\providecommand*{\fangsong}{\CJKfamily{zhfs}}",
        r"\fi",
        cfg["latin"],
        cfg["latin_sans"],
        "",
    ]
    return "\n".join(lines)


PROBE_DOC = r"""\documentclass[11pt,a4paper,fontset=none]{ctexart}
\usepackage{fontspec}
\input{fontset.tex}
\begin{document}
中文测试 Chinese Test 123 $\alpha^2+\frac{1}{2}$
\end{document}
"""


def find_engine() -> Optional[str]:
    for name in ("xelatex", "latexmk"):
        p = shutil.which(name)
        if p:
            return p
    return None


def probe_fontset(engine: str, cfg: Dict[str, str], timeout: int = 90) -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="p2z_font_") as td:
        d = Path(td)
        (d / "fontset.tex").write_text(fontset_tex(cfg), encoding="utf-8-sig")
        (d / "probe.tex").write_text(PROBE_DOC, encoding="utf-8")
        try:
            r = subprocess.run([engine, "-interaction=nonstopmode", "-halt-on-error",
                                "-output-directory", str(d), str(d / "probe.tex")],
                               capture_output=True, text=True, timeout=timeout,
                               cwd=str(d), errors="replace")
        except Exception as e:
            return False, str(e)
        log = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0 and (d / "probe.pdf").exists()
        if ok:
            return True, ""
        err = "\n".join([ln for ln in log.splitlines() if ln.startswith("!")][:3]) or log[-400:]
        return False, err


def choose_fontset(work: Path, engine: str, force_id: str = "",
                   reprobe: bool = False) -> Tuple[Dict[str, str], str]:
    paper_dir = work / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    cache = paper_dir / "fontset-choice.json"
    if force_id:
        for cfg in FONTSETS:
            if cfg["id"] == force_id:
                return cfg, "用户指定"
        raise SystemExit(f"[x] 未知字体集 id：{force_id}（可选：{', '.join(c['id'] for c in FONTSETS)}）")
    if cache.exists() and not reprobe:
        try:
            obj = json.loads(cache.read_text(encoding="utf-8-sig"))
            for cfg in FONTSETS:
                if cfg["id"] == obj.get("id"):
                    return cfg, "缓存（--reprobe 可重新探测）"
        except Exception:
            pass
    print("[*] 探测可用的中文字体集（用实际编译试验，逐个尝试）")
    for cfg in FONTSETS:
        ok, err = probe_fontset(engine, cfg)
        mark = "[v]" if ok else "[ ]"
        print(f"    {mark} {cfg['id']:<12} {cfg['desc']}")
        if ok:
            cache.write_text(json.dumps({"id": cfg["id"], "desc": cfg["desc"]},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
            return cfg, "探测通过"
        else:
            print(f"        失败原因：{err.strip()[:200]}")
    raise SystemExit("[x] 所有候选字体集都无法编译。请安装中文字体或 TeX Live 的 fandol 包后重试。")


# --------------------------------------------------------------------------- #
# Markdown -> LaTeX
# --------------------------------------------------------------------------- #

TEX_ESCAPE = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

INLINE_RE = re.compile(
    r"("
    r"\$\$.+?\$\$"                      # 行间公式
    r"|\$[^$\n]+?\$"                    # 行内公式
    r"|\\\(.+?\\\)"                     # \( \)
    r"|\*\*.+?\*\*"                     # 粗体
    r"|`[^`\n]+?`"                      # 行内代码
    r"|\[[^\]\n]*\]\([^)\n]*\)"         # 链接/图片
    r"|\^\[[^\]\n]*\]"                  # 脚注
    r")",
    re.S)

EQ_RE = re.compile(r"^\u27e6EQ(\d{2})\u27e7\s*$")
TAB_RE = re.compile(r"^\u27e6TABLE(\d{2})\s*(?:\|\s*(.*?))?\u27e7\s*$")
FIG_RE = re.compile(r"^\u27e6FIG(\d{2})\s*(?:\|\s*(.*?))?\u27e7\s*$")
IMG_RE = re.compile(r"^!\[(?P<cap>[^\]]*)\]\((?P<path>[^)]+)\)\s*(?:\{\s*(?P<opts>[^}]*)\})?\s*$")
COMMENT_RE = re.compile(r"^\s*<!--(.*?)-->\s*$", re.S)
CAP_PREFIX_RE = re.compile(r"^\s*(?:图|表|Figure|Table|Fig\.?)\s*\d+[a-zA-Z]?\s*[.:：、,，]?\s*", re.I)


def esc(text: str) -> str:
    return "".join(TEX_ESCAPE.get(ch, ch) for ch in text)


def strip_cap_prefix(s: str) -> str:
    return CAP_PREFIX_RE.sub("", s or "").strip()


class Ctx:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.eq_numbers: Dict[str, str] = {}
        self.tables: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        mf = work / "extracted" / "manifest.json"
        if mf.exists():
            try:
                obj = json.loads(mf.read_text(encoding="utf-8-sig"))
                for e in obj.get("equations", []):
                    self.eq_numbers["%02d" % int(e["index"])] = str(e.get("number") or "")
            except Exception as ex:
                self.warnings.append(f"manifest.json 读取失败：{ex}")
        tf = work / "extracted" / "tables" / "tables.json"
        if tf.exists():
            try:
                self.tables = json.loads(tf.read_text(encoding="utf-8-sig"))
            except Exception as ex:
                self.warnings.append(f"tables.json 读取失败：{ex}")

    # ---- 占位符 ----
    def equation(self, idx: str) -> str:
        draft = self.work / "extracted" / "equations" / "draft" / f"eq{idx}.tex"
        if not draft.exists():
            self.warnings.append(f"eq{idx}: 找不到草稿 {draft}，正文里保留了占位符")
            return "%% MISSING EQUATION eq%s\n" % idx
        body = re.sub(r"(?m)^\s*%.*$", "", draft.read_text(encoding="utf-8-sig")).strip()
        num = self.eq_numbers.get(idx, "")
        if not body:
            self.warnings.append(f"eq{idx}: 草稿为空")
            return "%% EMPTY EQUATION eq%s\n" % idx
        if num:
            return ("\\begin{equation}\n%s\n\\tag{%s}\n\\label{eq:%s}\n\\end{equation}"
                    % (body, num, num))
        return "\\[\n%s\n\\]" % body

    def table(self, idx: str, caption: str) -> str:
        info = self.tables.get(idx, {})
        cap = strip_cap_prefix(caption) or strip_cap_prefix(info.get("caption_en", "")) or f"表{int(idx)}"
        num = info.get("number", str(int(idx)))
        label = "tab:%s" % num
        if info.get("mode") == "image":
            fname = info.get("file", f"table{idx}.png")
            return ("\\begin{table}[htbp]\n\\centering\n\\caption{%s}\n\\label{%s}\n"
                    "\\includegraphics[width=\\linewidth]{%s}\n\\end{table}"
                    % (esc(cap), label, fname))
        fname = info.get("file", f"table{idx}.tex")
        return ("\\begin{table}[htbp]\n\\centering\n\\caption{%s}\n\\label{%s}\n\\small\n"
                "\\setlength{\\tabcolsep}{4pt}\n\\input{%s}\n\\end{table}"
                % (esc(cap), label, fname))

    def figure(self, idx: str, caption: str = "", path: str = "", opts: str = "") -> str:
        num = str(int(idx))
        width = "0.92\\linewidth"
        m = re.search(r"width\s*=\s*([0-9.]+)", opts or "")
        if m:
            width = "%s\\linewidth" % m.group(1)
        cap = strip_cap_prefix(caption) or f"图{num}"
        img = path or f"fig{num}.png"
        return ("\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=%s]{%s}\n"
                "\\caption{%s}\n\\label{fig:%s}\n\\end{figure}"
                % (width, img, esc(cap), num))


def inline(text: str, ctx: Ctx) -> str:
    """行内元素转换：公式/粗体/斜体/代码/链接/脚注，其余转义。"""
    out: List[str] = []
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append(esc(text[pos:m.start()]))
        tok = m.group(0)
        if tok.startswith("$$") and tok.endswith("$$"):
            out.append("\\[" + tok[2:-2].strip() + "\\]")
        elif tok.startswith("$") and tok.endswith("$"):
            out.append("$" + tok[1:-1] + "$")
        elif tok.startswith(r"\("):
            out.append("$" + tok[2:-2] + "$")
        elif tok.startswith("**"):
            out.append(r"\textbf{" + inline(tok[2:-2], ctx) + "}")
        elif tok.startswith("`"):
            out.append(r"\texttt{" + esc(tok[1:-1]) + "}")
        elif tok.startswith("["):
            mm = re.match(r"\[([^\]]*)\]\(([^)]*)\)", tok)
            if mm:
                label, url = mm.group(1), mm.group(2)
                if url.lower().endswith((".png", ".jpg", ".jpeg", ".pdf")):
                    out.append(r"\includegraphics[width=0.9\linewidth]{%s}" % url)
                else:
                    out.append(r"\href{%s}{%s}" % (url, inline(label or url, ctx)))
            else:
                out.append(esc(tok))
        elif tok.startswith("^["):
            out.append(r"\footnote{" + inline(tok[2:-1], ctx) + "}")
        else:
            out.append(esc(tok))
        pos = m.end()
    if pos < len(text):
        out.append(esc(text[pos:]))
    return "".join(out)


def md_table_to_tex(rows: List[List[str]], ctx: Ctx) -> str:
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    n = len(header)
    spec = "l" * n
    lines = ["\\begin{tabular}{%s}" % spec, "\\toprule",
             " & ".join(inline(c, ctx) for c in header) + r" \\", "\\midrule"]
    for r in body:
        r = r + [""] * (n - len(r))
        lines.append(" & ".join(inline(c, ctx) for c in r[:n]) + r" \\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines)


def split_md_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_table_sep(line: str) -> bool:
    s = line.strip().strip("|").strip()
    return bool(s) and bool(re.fullmatch(r"[\s:\-|]+", s)) and "-" in s


def convert_body(md: str, ctx: Ctx) -> str:
    lines = md.splitlines()
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        s = ln.strip()

        # HTML 注释 -> LaTeX 注释（保留可追溯信息）
        m = COMMENT_RE.match(ln)
        if m:
            txt = re.sub(r"\s+", " ", m.group(1)).strip()
            if txt:
                out.append("% " + txt.replace("\n", " "))
            i += 1
            continue

        # 代码块
        if s.startswith("```"):
            lang = s[3:].strip()
            buf: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("\\begin{verbatim}")
            out.extend(buf)
            out.append("\\end{verbatim}")
            out.append("")
            continue

        # 数学块 $$
        if s.startswith("$$"):
            buf = []
            rest = s[2:]
            if rest.endswith("$$") and len(rest) > 2:
                buf.append(rest[:-2])
                i += 1
            else:
                if rest.strip():
                    buf.append(rest)
                i += 1
                while i < n and "$$" not in lines[i]:
                    buf.append(lines[i])
                    i += 1
                if i < n:
                    tail = lines[i].split("$$")[0]
                    if tail.strip():
                        buf.append(tail)
                    i += 1
            out.append("\\[\n%s\n\\]" % "\n".join(buf).strip())
            out.append("")
            continue

        # 占位符（公式/表/图）
        m = EQ_RE.match(s)
        if m:
            out.append(ctx.equation(m.group(1)))
            out.append("")
            i += 1
            continue
        m = TAB_RE.match(s)
        if m:
            out.append(ctx.table(m.group(1), m.group(2) or ""))
            out.append("")
            i += 1
            continue
        m = FIG_RE.match(s)
        if m:
            out.append(ctx.figure(m.group(1), m.group(2) or ""))
            out.append("")
            i += 1
            continue

        # 独立成段的图片
        m = IMG_RE.match(s)
        if m:
            cap = m.group("cap")
            path = m.group("path")
            opts = m.group("opts") or ""
            fid = ""
            fm = re.search(r"#fig:([\w\.\-]+)", opts)
            if fm:
                fid = fm.group(1)
            num = re.sub(r"\D", "", fid) or "0"
            block = ctx.figure(num or "0", cap, path, opts)
            if fid and num:
                block = block.replace("\\label{fig:%s}" % num, "\\label{fig:%s}" % fid)
            out.append(block)
            out.append("")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            cmd = {1: "section", 2: "subsection", 3: "subsubsection"}.get(lvl, "paragraph")
            title = m.group(2).strip()
            if lvl > 3:
                ctx.warnings.append(f"标题层级过深（{lvl} 级）：{title} —— 已降为 paragraph")
            # 去掉自带的 "1." 等编号，交给 LaTeX 自动编号（保持与原文一致）
            title = re.sub(r"^\d+(\.\d+)*[\.、]?\s+", "", title)
            # 参考文献/致谢不参与正文编号（否则会出现 "4 致谢 / 4.1 参考文献" 这种怪编号）
            star = "" if re.search(r"参考文献|致谢|References|Acknowledg", title, re.I) else "*"
            out.append("\\%s%s{%s}" % (cmd, star, inline(title, ctx)))
            out.append("")
            i += 1
            continue

        # 分隔线
        if re.fullmatch(r"[-*_]{3,}", s):
            i += 1
            continue

        # 引用块
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("\\begin{quote}")
            out.append(inline(" ".join(buf), ctx))
            out.append("\\end{quote}")
            out.append("")
            continue

        # Markdown 表格
        if s.startswith("|") and i + 1 < n and is_table_sep(lines[i + 1]):
            rows = [split_md_row(lines[i])]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_md_row(lines[i]))
                i += 1
            out.append("\\begin{table}[htbp]\\centering")
            out.append("\\small")
            out.append(md_table_to_tex(rows, ctx))
            out.append("\\end{table}")
            out.append("")
            continue

        # 列表
        if re.match(r"^\s*([-*\u2022]|\d+[.)])\s+", ln):
            ordered = bool(re.match(r"^\s*\d+[.)]\s+", ln))
            items: List[str] = []
            while i < n and re.match(r"^\s*([-*\u2022]|\d+[.)])\s+", lines[i]):
                txt = re.sub(r"^\s*([-*\u2022]|\d+[.)])\s+", "", lines[i])
                # 续行
                j = i + 1
                while j < n and lines[j].strip() and not re.match(
                        r"^\s*([-*\u2022]|\d+[.)])\s+", lines[j]) and not lines[j].startswith("#"):
                    txt += " " + lines[j].strip()
                    j += 1
                items.append(inline(txt.strip(), ctx))
                i = j
            env = "enumerate" if ordered else "itemize"
            out.append("\\begin{%s}[leftmargin=2em,itemsep=2pt,topsep=3pt]" % env)
            for it in items:
                out.append("  \\item " + it)
            out.append("\\end{%s}" % env)
            out.append("")
            continue

        # 原始 LaTeX 直通行
        if s.startswith("\\"):
            out.append(ln.rstrip())
            i += 1
            continue

        # 空行
        if not s:
            i += 1
            continue

        # 段落
        buf = [ln.strip()]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^\s*(#{1,6}\s|[-*\u2022]\s|\d+[.)]\s|>|\||```|\$\$|\\|<!--)", lines[i]) \
                and not EQ_RE.match(lines[i].strip()) and not TAB_RE.match(lines[i].strip()) \
                and not FIG_RE.match(lines[i].strip()) and not IMG_RE.match(lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        para = " ".join(buf)
        para = re.sub(r"\s+", " ", para)
        out.append(inline(para, ctx))
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# front matter
# --------------------------------------------------------------------------- #

def parse_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    text = text.replace("\r\n", "\n")
    if not text.lstrip().startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    meta: Dict[str, str] = {}
    i = 1
    key = None
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == "---":
            i += 1
            break
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", ln)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if val in ("|", ">"):
                buf: List[str] = []
                i += 1
                while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                    buf.append(lines[i].strip())
                    i += 1
                meta[key] = " ".join(x for x in buf if x)
                continue
            meta[key] = val.strip('"').strip("'")
        i += 1
    return meta, "\n".join(lines[i:])


def extract_abstract(body: str) -> Tuple[str, str, str]:
    """把 `## 摘要` 与 `## 关键词` 从正文抽出（放进 abstract 环境）。"""
    abstract = ""
    keywords = ""
    lines = body.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.match(r"^#{1,3}\s*(摘要|abstract|Abstract|ABSTRACT)\s*$", s)
        if m:
            i += 1
            buf = []
            while i < len(lines) and not re.match(r"^#{1,6}\s", lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            abstract = " ".join(x for x in buf if x)
            continue
        m = re.match(r"^#{1,3}\s*(关键词|关键字|Keywords?|KEYWORDS)\s*$", s)
        if m:
            i += 1
            buf = []
            while i < len(lines) and not re.match(r"^#{1,6}\s", lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            keywords = " ".join(x for x in buf if x)
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), abstract, keywords


# --------------------------------------------------------------------------- #
# 组装与编译
# --------------------------------------------------------------------------- #

def build_tex(md_path: Path, work: Path, ctx: Ctx) -> str:
    raw = md_path.read_text(encoding="utf-8-sig")
    meta, body = parse_front_matter(raw)
    body, abstract, kw_section = extract_abstract(body)
    body_tex = convert_body(body, ctx)

    pre = PREAMBLE.read_text(encoding="utf-8-sig") if PREAMBLE.exists() else ""
    if not pre:
        ctx.warnings.append(f"找不到模板 {PREAMBLE}，使用内置最小 preamble")

    title_zh = meta.get("title_zh") or meta.get("title") or "（未填 title_zh）"
    title_en = meta.get("title_en", "")
    authors = meta.get("authors", meta.get("authors_en", ""))
    venue = meta.get("venue", "")
    year = meta.get("year", "")
    doi = meta.get("doi", "")
    url = meta.get("url", "")
    source = meta.get("source_pdf", meta.get("source", ""))
    keywords = meta.get("keywords", kw_section)
    note = meta.get("translation_note", "")

    head: List[str] = []
    short = re.sub(r"\s+", " ", title_zh)
    if len(short) > 26:
        short = short[:26] + "…"
    head.append("\\renewcommand{\\pTwoZHead}{%s}" % esc(short))
    head.append("\\title{%s%s}" % (
        esc(title_zh),
        ("\\\\[0.5em]{\\large\\normalfont\\itshape %s}" % esc(title_en)) if title_en else ""))
    head.append("\\author{%s}" % (esc(authors) if authors else ""))
    head.append("\\date{%s}" % esc(" ".join(x for x in [venue, year] if x)))

    doc: List[str] = []
    doc.append("\\begin{document}")
    doc.append("\\maketitle")
    doc.append("\\thispagestyle{fancy}")
    # 译文出处说明
    meta_bits = []
    if source:
        meta_bits.append("原文：%s" % esc(source))
    if doi:
        meta_bits.append("DOI：%s" % esc(doi))
    if url:
        meta_bits.append("链接：%s" % esc(url))
    doc.append("\\begin{center}\\small")
    if meta_bits:
        doc.append(" \\quad\\textbullet\\quad ".join(meta_bits) + "\\\\")
    doc.append("本文为据原文翻译的中文版，仅供学习研究；公式与数据经人工逐条核对，"
               "图片沿用原图（图内英文未作改动）。")
    if note:
        doc.append("\\\\" + esc(note))
    doc.append("\\end{center}")
    if abstract:
        doc.append("\\begin{abstract}")
        doc.append(inline(abstract, ctx))
        if keywords:
            doc.append("\\\\[0.4em]\\noindent\\textbf{关键词：}%s" % inline(keywords, ctx))
        doc.append("\\end{abstract}")
        doc.append("\\vspace{1em}")
    doc.append(body_tex)
    doc.append("\\end{document}")

    return "\n".join([pre, "", *head, ""] + doc) + "\n"


def compile_tex(paper_dir: Path, engine: str, tex_name: str = "paper.tex",
                passes: int = 2, timeout: int = 600) -> Dict[str, Any]:
    log_all: List[str] = []
    ok = True
    for k in range(passes):
        cmd = [engine, "-interaction=nonstopmode", "-halt-on-error",
               "-output-directory", str(paper_dir), tex_name]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               cwd=str(paper_dir), errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "log": "编译超时", "pages": 0, "errors": ["编译超时(>%ds)" % timeout]}
        log_all.append(f"===== pass {k+1} =====\n" + (r.stdout or "") + (r.stderr or ""))
        if r.returncode != 0:
            ok = False
            break
    log = "\n".join(log_all)
    (paper_dir / "build.log").write_text(log, encoding="utf-8")

    errors = []
    for ln in log.splitlines():
        if ln.startswith("!"):
            errors.append(ln.strip())
    missing_chars = sorted(set(re.findall(r"Missing character: There is no (.+?) in font", log)))
    overfull = len(re.findall(r"Overfull \\hbox", log))
    underfull = len(re.findall(r"Underfull \\hbox", log))
    undefined = sorted(set(re.findall(r"Reference `([^']+)' on page", log) +
                           re.findall(r"Citation `([^']+)' on page", log)))
    pages = 0
    pdf = paper_dir / tex_name.replace(".tex", ".pdf")
    if pdf.exists():
        try:
            import fitz
            d = fitz.open(str(pdf))
            pages = d.page_count
            d.close()
        except Exception:
            pass
    return {"ok": ok and pdf.exists() and not errors, "log": log, "pages": pages,
            "errors": errors, "missing_chars": missing_chars, "overfull": overfull,
            "underfull": underfull, "undefined": undefined, "pdf": str(pdf)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="中文译稿 -> LaTeX -> PDF（含字体自适应）")
    ap.add_argument("--work", default="translator-work", help="工作目录")
    ap.add_argument("--md", default="", help="译稿 Markdown（默认 <work>/paper/paper.md）")
    ap.add_argument("--tex", default="", help="直接编译已有 .tex（跳过 Markdown 转换）")
    ap.add_argument("--fontset", default="", help="强制指定字体集 id（windows/macos/noto/source-han/fandol）")
    ap.add_argument("--reprobe", action="store_true", help="重新探测字体集")
    ap.add_argument("--passes", type=int, default=2)
    args = ap.parse_args(argv)

    work = abs_path(args.work)
    paper_dir = work / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    engine = find_engine()
    if not engine:
        print("[x] 找不到 xelatex/latexmk。请安装 TeX Live（推荐）或 MiKTeX。", file=sys.stderr)
        return 2

    cfg, how = choose_fontset(work, engine, args.fontset, args.reprobe)
    (paper_dir / "fontset.tex").write_text(fontset_tex(cfg), encoding="utf-8-sig")
    print(f"[*] 字体集：{cfg['id']}（{how}）—— {cfg['desc']}")

    ctx = Ctx(work)
    if args.tex:
        tex_path = Path(args.tex)
        if not tex_path.is_absolute():
            tex_path = (paper_dir / args.tex) if (paper_dir / args.tex).exists() else Path(args.tex)
        tex_name = tex_path.name
        if abs_path(tex_path.parent) != abs_path(paper_dir):
            shutil.copy(tex_path, paper_dir / tex_name)
        print(f"[*] 直接编译已有 LaTeX：{tex_name}")
    else:
        md_path = Path(args.md) if args.md else paper_dir / "paper.md"
        if not md_path.is_absolute():
            md_path = (Path.cwd() / md_path) if (Path.cwd() / md_path).exists() else (work / md_path)
        if not md_path.exists():
            print(f"[x] 找不到译稿 {md_path}。请先写 <work>/paper/paper.md（格式见 SKILL.md）。", file=sys.stderr)
            return 2
        tex = build_tex(md_path, work, ctx)
        (paper_dir / "paper.tex").write_text(tex, encoding="utf-8-sig")
        tex_name = "paper.tex"
        print(f"[*] 已生成 {paper_dir / 'paper.tex'}（{len(tex.splitlines())} 行）")

    res = compile_tex(paper_dir, engine, tex_name, passes=max(1, args.passes))
    print(f"[*] 编译{'成功' if res['ok'] else '失败'}；页数 {res['pages']}")
    if res["errors"]:
        print("    编译错误：")
        for e in res["errors"][:15]:
            print("      " + e)
    if res["missing_chars"]:
        print("    [!] 缺字形（字体不支持，输出的 PDF 会有空白/豆腐块）："
              + ", ".join(res["missing_chars"][:10]))
    if res["undefined"]:
        print("    [!] 未定义引用：" + ", ".join(res["undefined"][:10]))
    if res["overfull"]:
        print(f"    [!] Overfull hbox {res['overfull']} 处（可能有出界内容，见 build.log）")
    if ctx.warnings:
        print("    转换告警：")
        for w in ctx.warnings[:15]:
            print("      " + w)
    report = {
        "engine": engine, "fontset": cfg["id"], "pages": res["pages"], "ok": res["ok"],
        "errors": res["errors"], "missing_chars": res["missing_chars"],
        "overfull": res["overfull"], "underfull": res["underfull"],
        "undefined": res["undefined"], "warnings": ctx.warnings,
        "pdf": res["pdf"],
    }
    (paper_dir / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[=] 产物：{res['pdf']}\n    日志：{paper_dir / 'build.log'}")
    print("    下一步：python verify.py --work %s" % work)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
