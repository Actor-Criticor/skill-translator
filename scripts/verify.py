# -*- coding: utf-8 -*-
"""verify.py —— 翻译成品的一致性/完整性/正确性质检（skill-translator 的交付门禁）。

翻译最容易出的三类事故，这里逐项设卡：
  1. **数字被改**（漏译、四舍五入、编造数据）—— 逐数字多重集比对；
  2. **公式没复核**（还原错了却没人看）—— 强制要求每条公式的"原图↔渲染"对照已确认；
  3. **有段落没翻译**（夹带英文残留、图表题注漏译）—— 英文残段扫描 + 结构比对。

用法：
    python verify.py --work translator-work
返回码：0 = 全部硬性检查通过；1 = 存在 FAIL；2 = 用法/文件错误。
报告：<work>/paper/质检报告.md 与 verify-report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import p2zconsole  # noqa: E402,F401  控制台输出编码
from p2zpaths import abs_path  # noqa: E402  绝对路径（勿用 Path.resolve）
import fitz  # noqa: E402

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PLACEHOLDER_RE = re.compile(r"\u27e6(?:EQ|FIG|TABLE)[^\u27e7]*\u27e7")
NUM_RE = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?")
CITE_RE = re.compile(r"\[(\d+(?:\s*[,\u2013\-]\s*\d+)*)\]")
ENGLISH_RUN_RE = re.compile(r"\b[A-Za-z][A-Za-z'\u2019\-]*(?:\s+[A-Za-z][A-Za-z'\u2019\-]*){3,}\b")

ALLOWED_EN = [
    # 只放"确定不该算漏译"的具体片段。
    # **不要放 The/a/of/of the 这类通用词**：否则一整句未翻译的英文只要含 "The"
    # 就会被放过，F9 形同失效（这是实测踩过的坑）。
    "et al", "in vitro", "in vivo", "vs.", "e.g.", "i.e.", "cf.",
    "IEEE", "Springer", "Elsevier", "Wiley", "arXiv", "doi:", "https://",
    "http://", "GitHub", "Python", "LaTeX", "MATLAB",
]

FONT_CJK_HINT = re.compile(
    r"Fandol|SimSun|SimHei|Songti|Heiti|Noto\s*(Serif|Sans)\s*CJK|Source\s*Han|"
    r"FangSong|KaiTi|STSong|STHeiti|MS\s*Song|MingLiU|WenQuanYi|Droid|AR\s*PL",
    re.I)


# --------------------------------------------------------------------------- #
# 文本抽取
# --------------------------------------------------------------------------- #

def strip_tex_for_prose(tex: str, keep_inline_math: bool = False) -> Tuple[str, str]:
    """返回 (正文散文字本, 参考文献区文本)。去掉注释/命令/图表环境。

    keep_inline_math=True 保留行内公式内容（数字比对需要，见 strip_source_for_numbers）。
    """
    tex = tex.replace("\r\n", "\n")
    # 去注释（保留转义的 \%）
    tex = re.sub(r"(?<!\\)%.*$", "", tex, flags=re.M)
    body = tex
    m = re.search(r"\\begin\{thebibliography\}|"
                  r"\\(?:section|subsection)\*?\{\s*(?:参考文献|References)\s*\}",
                  body)
    refs = ""
    if m:
        refs = body[m.start():]
        body = body[:m.start()]
    m = re.search(r"\\begin\{document\}", body)
    if m:
        body = body[m.end():]
    else:
        # 无 \begin{document}（可能是片段）时，裁掉 preamble 的宏包区
        body = re.sub(r"(?s)\\documentclass.*?\\begin\{document\}", "", body)
    # 去数学环境与 display math
    body = re.sub(r"(?s)\\begin\{(equation\*?|align\*?|aligned|gather\*?|multline\*?|"
                  r"eqnarray\*?|split|cases|matrix|pmatrix|bmatrix|vmatrix|array)\}.*?"
                  r"\\end\{\1\}", " ", body)
    body = re.sub(r"(?s)\\\[.*?\\\]", " ", body)
    body = re.sub(r"(?s)\$\$.*?\$\$", " ", body)
    # 排版参数与图片（width=0.92\linewidth、\\[0.4em] 等）不是论文数据，
    # **必须在通用命令剥离之前处理**，否则会把参数里的数字留给数字比对造成误报
    body = re.sub(r"\\includegraphics\s*(\[[^\]]*\])?\s*\{[^}]*\}", " ", body)
    body = re.sub(r"\\(?:vspace|hspace|rule|setlength|addtolength)\*?\s*(\[[^\]]*\])?\s*"
                  r"(\{[^}]*\})?", " ", body)
    body = re.sub(r"\\\[[^\]]*\]", " ", body)
    if keep_inline_math:
        # 保留行内公式内容（只去掉 $ 与 LaTeX 命令）：否则 "9$7.5$%" 里的 7.5 会丢
        body = body.replace("$", " ")
        body = re.sub(r"\\[A-Za-z]+", " ", body)
    else:
        body = re.sub(r"(?s)\$[^$]*\$", " ", body)
    body = re.sub(r"(?s)\\begin\{(verbatim|lstlisting)\}.*?\\end\{\1\}", " ", body)
    return body, refs


RUNNING_HEAD_RE = re.compile(
    r"^\s*[A-Z][A-Za-z\.\s&]{0,40}\d{1,4}\s*\(\d{4}\)\s*[\d\u2013\-\u2014]*\s*\d*\s*$")


def drop_math_dollars(s: str) -> str:
    """去掉源文里的 `$`（行内公式标记），同时保住数字完整性。

    两种情形必须区分，否则数字比对会误报：
    * `22$4.4$` —— `$` 夹在两个数字之间：直接删掉，还原成 `224.4`；
    * `to$5.6`  —— `$` 左边是字母：换成空格，否则粘成 `to5.6` 会被数字正则的
      左边界规则否掉（表现为"译文多出 5.6"的假警报）。
    """
    out: List[str] = []
    for i, ch in enumerate(s):
        if ch != "$":
            out.append(ch)
            continue
        prev = s[i - 1] if i > 0 else " "
        nxt = s[i + 1] if i + 1 < len(s) else " "
        if prev.isdigit() and (nxt.isdigit() or nxt == "."):
            continue
        out.append(" ")
    return "".join(out)


def strip_source_for_numbers(md: str) -> Tuple[str, str]:
    """源文 source.md -> (正文文本, 参考文献区文本)。

    * 保留行内公式内容（`$...$` 只去美元号、保留数字），与译文侧口径一致；
    * 丢掉：注释、章节标题行（标题里的 "2.1" 是 LaTeX 自动编号，译文文本里没有）、
      运行页眉与单独成行的页码（期刊卷号/页码属排版噪声，不是数据）。
    """
    md = re.sub(r"(?s)<!--.*?-->", " ", md)
    m = re.search(r"<!--\s*REFERENCES.*?-->", md)
    refs = ""
    if m:
        refs = md[m.end():]
        md = md[:m.start()]
    md = PLACEHOLDER_RE.sub(" ", md)
    keep: List[str] = []
    for line in md.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(">"):
            s = s.lstrip(">").strip()      # 题注行：内容照常参与数字比对
        if re.fullmatch(r"\d{1,4}", s):
            continue
        if RUNNING_HEAD_RE.match(s):
            continue
        keep.append(drop_math_dollars(s))
    return "\n".join(keep), refs


def extract_numbers(text: str, significant_only: bool = True) -> List[str]:
    """抽取数字（规范化），默认只保留"显著数字"（小数/百分比/≥3位/科学计数）。"""
    out: List[str] = []
    for m in NUM_RE.finditer(text):
        tok = m.group(0)
        after = text[m.end():m.end() + 3]
        norm = tok.replace(",", "")
        try:
            val = float(norm)
        except ValueError:
            continue
        digits = norm.replace(".", "").replace("-", "").replace("+", "")
        significant = (("." in norm) or len(re.sub(r"[^0-9]", "", digits)) >= 3
                       or re.match(r"\\?%", after.strip()) is not None
                       or "e" in norm.lower())
        if significant_only and not significant:
            continue
        # 归一化：去尾零，统一科学计数字母
        if "." in norm:
            norm = norm.rstrip("0").rstrip(".")
        if "e" in norm.lower():
            norm = re.sub(r"[eE]\+?", "e", norm)
        out.append(norm)
    return out


def multiset_diff(a: Sequence[str], b: Sequence[str]) -> Tuple[List[str], List[str]]:
    """返回 (只在 a 中出现的, 只在 b 中出现的)。"""
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    only_a = sorted((ca - cb).elements(), key=_num_key)
    only_b = sorted((cb - ca).elements(), key=_num_key)
    return only_a, only_b


def _num_key(s: str) -> Tuple[int, float]:
    try:
        return (0, float(s))
    except ValueError:
        return (1, 0.0)


def load_allowlist(paper_dir: Path) -> Tuple[Set[str], Set[str], List[str]]:
    """paper/verify-allowlist.txt：`-123.4` 表示允许"源文有而译文无"，`+5` 表示允许"译文多出"。"""
    only_a: Set[str] = set()
    only_b: Set[str] = set()
    snippets: List[str] = []
    f = paper_dir / "verify-allowlist.txt"
    if not f.exists():
        return only_a, only_b, snippets
    for ln in f.read_text(encoding="utf-8-sig").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("-"):
            only_a.add(s[1:].strip())
        elif s.startswith("+"):
            only_b.add(s[1:].strip())
        else:
            snippets.append(s)
    return only_a, only_b, snippets


# --------------------------------------------------------------------------- #
# 检查项
# --------------------------------------------------------------------------- #

class Checker:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.paper_dir = work / "paper"
        self.ext = work / "extracted"
        self.results: List[Dict[str, Any]] = []
        self.manifest: Dict[str, Any] = {}
        mf = self.ext / "manifest.json"
        if mf.exists():
            try:
                self.manifest = json.loads(mf.read_text(encoding="utf-8-sig"))
            except Exception:
                self.manifest = {}
        self.tables_json: Dict[str, Any] = {}
        tf = self.ext / "tables" / "tables.json"
        if tf.exists():
            try:
                self.tables_json = json.loads(tf.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
        self.tex = ""
        self.prose = ""
        self.refs_tex = ""
        self.pdf_path = self.paper_dir / "paper.pdf"
        fp = self.paper_dir / "paper.tex"
        if fp.exists():
            self.tex = fp.read_text(encoding="utf-8-sig")
            self.prose, self.refs_tex = strip_tex_for_prose(self.tex)
        self.source_md_path = self.ext / "source" / "source.md"
        self.source_md = self.source_md_path.read_text(encoding="utf-8-sig") if self.source_md_path.exists() else ""

    def add(self, cid: str, name: str, status: str, detail: str = "",
            items: Optional[List[str]] = None) -> None:
        self.results.append({"id": cid, "name": name, "status": status,
                             "detail": detail, "items": items or []})

    # ---------------- 1 图片 ----------------
    def check_figures(self) -> None:
        figs = self.manifest.get("figures", [])
        fdir = self.ext / "figures"
        missing: List[str] = []
        tiny: List[str] = []
        for f in figs:
            p = fdir / f"fig{int(f['index'])}.png"
            if not p.exists():
                missing.append(p.name)
                continue
            if p.stat().st_size < 2000:
                tiny.append(f"{p.name}（{p.stat().st_size} 字节，疑似空白/失败）")
            try:
                with fitz.open(str(p)) as d:
                    r = d[0].rect
                    if r.width < 200 or r.height < 80:
                        tiny.append(f"{p.name}（{int(r.width)}x{int(r.height)} 像素，过小）")
            except Exception as e:
                tiny.append(f"{p.name}（无法读取：{e}）")
        if not figs:
            self.add("F1", "图片提取", "FAIL", "manifest 里没有任何图片：可能是题注识别失败，需人工确认 PDF 是否有图")
            return
        if missing or tiny:
            self.add("F1", "图片提取", "FAIL",
                     f"共 {len(figs)} 张，缺失 {len(missing)}，异常 {len(tiny)}",
                     [f"缺失：{m}" for m in missing] + tiny)
        else:
            # 内容级检查：多张图的文件字节不得相同（相同 = 定位重复，交给用户两张一样的图）
            import hashlib
            seen: Dict[str, int] = {}
            dup: List[str] = []
            for f in figs:
                p = fdir / f"fig{int(f['index'])}.png"
                if not p.exists():
                    continue
                h = hashlib.sha1(p.read_bytes()).hexdigest()
                if h in seen:
                    dup.append(f"fig{f['index']} 与 fig{seen[h]} 内容完全相同")
                else:
                    seen[h] = int(f["index"])
            noted = [f"fig{f['index']}：{'；'.join(f['notes'])}" for f in figs
                     if any(k in "；".join(f["notes"]) for k in ("定位重复", "疑似", "missing", "失败"))]
            if dup:
                self.add("F1", "图片提取", "FAIL",
                         f"fig1~fig{len(figs)} 共 {len(figs)} 张，但存在内容重复",
                         dup + ["请按 references/extraction-rules.md 手工重截后覆盖同名文件"])
            elif noted:
                self.add("F1", "图片提取", "WARN",
                         f"{len(figs)} 张图文件齐全，但 {len(noted)} 张有定位疑点", noted)
            else:
                self.add("F1", "图片提取", "PASS",
                         f"fig1~fig{len(figs)} 共 {len(figs)} 张，文件齐全、尺寸正常且互不重复")

    def check_figure_usage(self) -> None:
        figs = self.manifest.get("figures", [])
        if not self.tex:
            self.add("F2", "图片在译文中被使用", "SKIP", "尚未生成 paper.tex")
            return
        used = [int(m) for m in re.findall(r"fig(\d+)\.(?:png|jpg|jpeg|pdf)", self.tex)]
        labels = [int(m) for m in re.findall(r"\\label\{fig:(\d+)", self.tex)]
        expected = {int(f["index"]) for f in figs}
        unused = sorted(expected - set(used) - set(labels))
        if unused:
            self.add("F2", "图片在译文中被使用", "FAIL",
                     f"有 {len(unused)} 张图没被译文使用（漏插图）", [f"fig{n}" for n in unused])
        else:
            self.add("F2", "图片在译文中被使用", "PASS", f"{len(expected)} 张图全部出现在译文中")

    # ---------------- 2 公式 ----------------
    def check_equations(self) -> None:
        drafts = sorted((self.ext / "equations" / "draft").glob("eq*.tex")) \
            if (self.ext / "equations" / "draft").exists() else []
        eqs = self.manifest.get("equations", [])
        if not drafts:
            self.add("F3", "公式复核", "FAIL", "没有公式草稿：extract.py 未提取到公式，或论文本身无公式（需人工确认）")
            return
        status = {}
        sf = self.ext / "equations" / "status.json"
        if sf.exists():
            try:
                status = json.loads(sf.read_text(encoding="utf-8-sig"))
            except Exception:
                status = {}
        import hashlib

        def sha1(t: str) -> str:
            return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]

        skip: Dict[str, str] = {}
        skf = self.ext / "equations" / "skip-list.txt"
        if skf.exists():
            for ln in skf.read_text(encoding="utf-8-sig").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    parts = ln.split(None, 1)
                    skip[parts[0]] = parts[1] if len(parts) > 1 else "（未写理由）"
        unconfirmed: List[str] = []
        render_fail: List[str] = []
        for d in drafts:
            tag = d.stem
            body = re.sub(r"(?m)^\s*%.*$", "", d.read_text(encoding="utf-8-sig")).strip()
            rec = status.get(tag, {})
            if tag in skip:
                continue
            if not rec.get("render_ok"):
                render_fail.append(f"{tag}（未渲染成功：{str(rec.get('error',''))[:60]}）")
                continue
            if not rec.get("confirmed") or rec.get("draft_sha1") != sha1(body):
                unconfirmed.append(tag)
        detail = (f"公式 {len(eqs) or len(drafts)} 条；草稿 {len(drafts)} 条；"
                  f"已确认 {len(drafts) - len(unconfirmed) - len(render_fail) - len(skip)}；"
                  f"待确认 {len(unconfirmed)}；渲染失败 {len(render_fail)}；"
                  f"登记跳过 {len(skip)}")
        if unconfirmed or render_fail:
            status_str = "FAIL"
            items = [f"待确认：{t}" for t in unconfirmed] + render_fail
            if skip:
                items.append("已登记跳过：" + ", ".join(f"{k}（{v}）" for k, v in skip.items()))
            self.add("F3", "公式复核", status_str, detail, items)
        else:
            self.add("F3", "公式复核", "PASS", detail,
                     [f"登记跳过：{k}（{v}）" for k, v in skip.items()])

    def check_placeholders(self) -> None:
        if not self.tex:
            self.add("F4", "占位符全部解决", "SKIP", "尚未生成 paper.tex")
            return
        left = PLACEHOLDER_RE.findall(self.tex)
        if left:
            self.add("F4", "占位符全部解决", "FAIL",
                     f"译文中残留 {len(left)} 个未处理占位符", sorted(set(left))[:20])
        else:
            self.add("F4", "占位符全部解决", "PASS", "⟦EQnn⟧/⟦FIGnn⟧/⟦TABLEnn⟧ 均已转换为 LaTeX")

    # ---------------- 3 表格 ----------------
    def check_tables(self) -> None:
        tabs = self.manifest.get("tables", [])
        if not tabs:
            self.add("F5", "表格提取", "WARN", "manifest 里没有表格；若原文确有表格请人工检查题注识别")
            return
        bad: List[str] = []
        for i, t in enumerate(tabs, 1):
            idx = "%02d" % i
            info = self.tables_json.get(idx, {})
            fn = info.get("file") or (f"table{idx}.png" if t.get("strategy") == "image" else f"table{idx}.tex")
            p = self.ext / "tables" / fn
            if not p.exists():
                bad.append(f"table{idx}: 缺文件 {fn}")
                continue
            if t.get("strategy") != "image" and float(t.get("quality") or 0) < 0.6:
                bad.append(f"table{idx}: 结构解析质量低（{t.get('quality')}），建议人工核对或改截图")
            if info.get("mode") == "latex":
                tex = p.read_text(encoding="utf-8-sig")
                if tex.count("{") != tex.count("}"):
                    bad.append(f"table{idx}: 花括号不配对，可能编译失败")
        if bad:
            self.add("F5", "表格提取", "WARN", f"{len(bad)} 张表需要人工确认", bad)
        else:
            self.add("F5", "表格提取", "PASS", f"{len(tabs)} 张表文件齐全")

    # ---------------- 4 数字 ----------------
    def check_numbers(self) -> None:
        if not self.tex:
            self.add("F6", "数字一致性", "SKIP", "尚未生成 paper.tex")
            return
        src_body, src_refs = strip_source_for_numbers(self.source_md)
        if not self.source_md:
            self.add("F6", "数字一致性", "FAIL", "找不到 extracted/source/source.md，无法比对")
            return
        s_nums = extract_numbers(src_body) + extract_numbers(src_refs)
        prose_nums, refs_nums = strip_tex_for_prose(self.tex, keep_inline_math=True)
        # 首页由模板自动打印的"原文/DOI/链接"元信息不属于译文正文，
        # 否则 DOI 里的数字永远会被报成"译文多出"（系统性误报）
        prose_nums = re.sub(r"(?s)\\begin\{center\}.*?\\end\{center\}", " ", prose_nums, count=1)
        t_nums = extract_numbers(prose_nums) + extract_numbers(refs_nums)
        only_src, only_tex = multiset_diff(s_nums, t_nums)
        allow_missing, allow_extra, snippets = load_allowlist(self.paper_dir)
        only_src = [x for x in only_src if x not in allow_missing]
        only_tex = [x for x in only_tex if x not in allow_extra]
        detail = (f"源文显著数字 {len(s_nums)} 个 / 译文 {len(t_nums)} 个；"
                  f"源文有而译文无 {len(only_src)} 个；译文多出 {len(only_tex)} 个")
        if only_src or only_tex:
            items = []
            if only_src:
                items.append("【源文有、译文没有】可能漏译或改了数字：" + ", ".join(only_src[:40]))
            if only_tex:
                items.append("【译文多出】可能编造或换算过：" + ", ".join(only_tex[:40]))
            items.append("确认无误的数字请登记到 paper/verify-allowlist.txt（每行一个，-前缀=源文独有，+前缀=译文独有）")
            self.add("F6", "数字一致性", "FAIL", detail, items)
        else:
            self.add("F6", "数字一致性", "PASS", detail)

    def check_citations(self) -> None:
        if not self.tex:
            self.add("F7", "引用标注一致性", "SKIP", "尚未生成 paper.tex")
            return
        src_body, src_refs = strip_source_for_numbers(self.source_md)

        def cites(t: str) -> Set[str]:
            out: Set[str] = set()
            for m in CITE_RE.finditer(t):
                for part in re.split(r"[,\u2013\-]", m.group(1)):
                    part = part.strip()
                    if part.isdigit():
                        out.add(part)
            return out
        sc = cites(src_body) | cites(src_refs)
        tc = cites(self.prose) | cites(self.refs_tex)
        missing = sorted(sc - tc, key=lambda x: int(x))
        extra = sorted(tc - sc, key=lambda x: int(x))
        detail = f"源文引用编号 {len(sc)} 个 / 译文 {len(tc)} 个"
        if missing or extra:
            items = []
            if missing:
                items.append("译文中缺少的引用编号：" + ", ".join("[%s]" % m for m in missing))
            if extra:
                items.append("译文中多出的引用编号：" + ", ".join("[%s]" % m for m in extra))
            self.add("F7", "引用标注一致性", "WARN", detail, items)
        else:
            self.add("F7", "引用标注一致性", "PASS", detail)

    # ---------------- 5 结构 ----------------
    def check_sections(self) -> None:
        sf = self.ext / "source" / "sections.json"
        if not sf.exists():
            self.add("F8", "章节结构一致性", "WARN", "找不到 sections.json")
            return
        try:
            secs = json.loads(sf.read_text(encoding="utf-8-sig"))
        except Exception as e:
            self.add("F8", "章节结构一致性", "WARN", f"sections.json 读取失败：{e}")
            return
        src_n = len([s for s in secs if not s.get("no_translate") and not s.get("front_matter")])
        if not self.tex:
            self.add("F8", "章节结构一致性", "SKIP", "尚未生成 paper.tex")
            return
        tex_sec = len(re.findall(r"\\section\*?\{", self.tex))
        tex_sub = len(re.findall(r"\\subsection\*?\{", self.tex))
        tex_subsub = len(re.findall(r"\\subsubsection\*?\{", self.tex))
        tex_n = tex_sec + tex_sub + tex_subsub
        detail = f"源文可译标题 {src_n} 个；译文 \\section {tex_sec} + \\subsection {tex_sub} + \\subsubsection {tex_subsub} = {tex_n}"
        if tex_n < src_n - 1:
            self.add("F8", "章节结构一致性", "FAIL", detail,
                     ["译文标题数明显少于源文：可能有整节漏译（或被合并）",
                      "源文标题：" + "; ".join(s["title_en"][:40] for s in secs[:20])])
        elif tex_n > src_n + 3:
            self.add("F8", "章节结构一致性", "WARN", detail, ["译文标题数多于源文：确认没有自行添加小标题"])
        else:
            self.add("F8", "章节结构一致性", "PASS", detail)

    def check_untranslated(self) -> None:
        if not self.tex:
            self.add("F9", "漏译扫描（英文残留）", "SKIP", "尚未生成 paper.tex")
            return
        _, _, snippets = load_allowlist(self.paper_dir)
        prose = re.sub(r"\\(?:label|ref|eqref|cite|input|includegraphics|bibliography|"
                       r"bibliographystyle|hspace|vspace|rule|href|url)\s*(?:\[[^\]]*\])?\{[^}]*\}", " ", self.prose)
        prose = re.sub(r"\\[A-Za-z@]+\s*", " ", prose)
        prose = re.sub(r"[{}]", " ", prose)
        hits: List[str] = []
        for m in ENGLISH_RUN_RE.finditer(prose):
            frag = m.group(0)
            if any(a.lower() in frag.lower() for a in ALLOWED_EN):
                continue
            if any(s and s in frag for s in snippets):
                continue
            hits.append(frag[:120])
        if hits:
            self.add("F9", "漏译扫描（英文残留）", "WARN",
                     f"发现 {len(hits)} 处连续英文（可能是漏译，也可能是专有名词/缩写）",
                     hits[:25] + ["确认为专有名词的请写进 paper/verify-allowlist.txt"])
        else:
            self.add("F9", "漏译扫描（英文残留）", "PASS", "正文散文中没有成段的英文残留")

    def check_refs_untouched(self) -> None:
        if not self.tex:
            self.add("F10", "参考文献未翻译", "SKIP", "尚未生成 paper.tex")
            return
        if not self.refs_tex.strip():
            self.add("F10", "参考文献未翻译", "WARN", "译文里没有识别到参考文献区（原文有参考文献吗？）")
            return
        cjk = CJK_RE.findall(re.sub(r"\\[A-Za-z@]+\s*", " ", self.refs_tex))
        # 标题行允许中文（参考文献 / References）
        head_len = 200
        cjk_tail = CJK_RE.findall(re.sub(r"\\[A-Za-z@]+\s*", " ", self.refs_tex[head_len:]))
        if len(cjk_tail) > 60:
            self.add("F10", "参考文献未翻译", "WARN",
                     f"参考文献区出现 {len(cjk_tail)} 个中文字符：按规范参考文献应保留英文原貌",
                     ["如果是有意翻译（例如中文期刊），请在 SKILL 说明中注明并忽略此项"])
        else:
            self.add("F10", "参考文献未翻译", "PASS", "参考文献区保持英文原貌")

    # ---------------- 6 编译与字体 ----------------
    def check_pdf(self) -> None:
        br = {}
        bf = self.paper_dir / "build-report.json"
        if bf.exists():
            try:
                br = json.loads(bf.read_text(encoding="utf-8-sig"))
            except Exception:
                br = {}
        if not self.pdf_path.exists():
            self.add("F11", "PDF 编译", "FAIL", "找不到 paper/paper.pdf，请先运行 build.py")
            return
        items: List[str] = []
        status = "PASS"
        detail = f"页数 {br.get('pages', '?')}；字体集 {br.get('fontset', '?')}"
        if br.get("errors"):
            status = "FAIL"
            items.append("编译错误：" + "; ".join(br["errors"][:8]))
        if br.get("missing_chars"):
            status = "FAIL"
            items.append("缺字形（PDF 会有空白/方块）：" + ", ".join(br["missing_chars"][:10])
                         + " —— 换字体集或修字符（如把生僻符号改写成 LaTeX 命令）")
        if br.get("undefined"):
            status = "WARN" if status == "PASS" else status
            items.append("未定义引用：" + ", ".join(br["undefined"][:10]))
        if br.get("overfull", 0) > 0:
            if status == "PASS":
                status = "WARN"
            items.append(f"Overfull hbox {br['overfull']} 处（内容可能出界，见 paper/build.log）")
        # PDF 是否真的含中文
        try:
            with fitz.open(str(self.pdf_path)) as d:
                txt = "".join(d[i].get_text() for i in range(min(3, d.page_count)))
                n_cjk = len(CJK_RE.findall(txt))
                if n_cjk < 50:
                    status = "FAIL"
                    items.append(f"PDF 前 3 页中文字符仅 {n_cjk} 个：可能没有中文正文字形（字体集问题）")
                else:
                    items.append(f"PDF 中文字符（前3页）{n_cjk} 个，中文渲染正常")
        except Exception as e:
            status = "FAIL"
            items.append(f"PDF 无法打开：{e}")
        self.add("F11", "PDF 编译", status, detail, items)

    def check_fonts_embedded(self) -> None:
        if not self.pdf_path.exists():
            self.add("F12", "字体嵌入", "SKIP", "尚无 PDF")
            return
        not_embedded: List[str] = []
        cjk_fonts: List[str] = []
        total = 0
        try:
            with fitz.open(str(self.pdf_path)) as d:
                seen = set()
                for pno in range(d.page_count):
                    for f in d.get_page_fonts(pno, full=True):
                        xref, ext, ftype, basefont = int(f[0]), f[1], f[2], f[3]
                        if xref in seen:
                            continue
                        seen.add(xref)
                        total += 1
                        embedded = True
                        try:
                            base, e, sub, buf = d.extract_font(xref)
                            embedded = bool(buf)
                        except Exception:
                            embedded = False
                        name = str(basefont) + " " + str(f[4] if len(f) > 4 else "")
                        if FONT_CJK_HINT.search(name):
                            cjk_fonts.append(str(basefont))
                        if not embedded:
                            not_embedded.append(f"{basefont} (type={ftype}, ext={ext})")
        except Exception as e:
            self.add("F12", "字体嵌入", "WARN", f"字体检查失败：{e}")
            return
        items = []
        status = "PASS"
        if not_embedded:
            status = "WARN"
            items.append("未嵌入字体（换机器显示可能不一致）：" + ", ".join(not_embedded[:10]))
        if not cjk_fonts:
            status = "WARN" if status == "PASS" else status
            items.append("未识别到中文字体名（可能被子集化改名）；PDF 中文字符检查见 F11")
        else:
            items.append("中文字体：" + ", ".join(sorted(set(cjk_fonts))[:5]))
        self.add("F12", "字体嵌入", status, f"共 {total} 个字体", items)

    def check_front_matter(self) -> None:
        if not self.tex:
            self.add("F13", "标题/摘要/关键词/说明", "SKIP", "尚未生成 paper.tex")
            return
        items: List[str] = []
        status = "PASS"
        if "\\maketitle" not in self.tex:
            status = "WARN"
            items.append("缺少 \\maketitle")
        if "\\begin{abstract}" not in self.tex:
            status = "WARN"
            items.append("缺少摘要（abstract 环境）：请把 `## 摘要` 写进 paper.md")
        if "关键词" not in self.tex:
            status = "WARN"
            items.append("缺少关键词")
        if "本文为据原文翻译的中文版" not in self.tex:
            status = "WARN"
            items.append("缺少译文出处说明段")
        self.add("F13", "标题/摘要/关键词/说明", status, "元数据完整性", items)

    # ---------------- 汇总 ----------------
    def run(self) -> int:
        self.check_figures()
        self.check_figure_usage()
        self.check_equations()
        self.check_placeholders()
        self.check_tables()
        self.check_numbers()
        self.check_citations()
        self.check_sections()
        self.check_untranslated()
        self.check_refs_untouched()
        self.check_pdf()
        self.check_fonts_embedded()
        self.check_front_matter()
        n_fail = sum(1 for r in self.results if r["status"] == "FAIL")
        n_warn = sum(1 for r in self.results if r["status"] == "WARN")
        n_pass = sum(1 for r in self.results if r["status"] == "PASS")
        return 1 if n_fail else 0


def write_report(work: Path, results: List[Dict[str, Any]], pdf_pages: Any = None) -> None:
    icon = {"PASS": "[通过]", "WARN": "[注意]", "FAIL": "[不通过]", "SKIP": "[跳过]"}
    lines = ["# 中文版译文质检报告（skill-translator）", ""]
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    lines.append(f"**结论：{'不通过，必须先修复下列 FAIL 项' if n_fail else ('通过（有 %d 项需注意）' % n_warn if n_warn else '全部通过')}**")
    lines.append("")
    lines.append("| 编号 | 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|---|")
    for r in results:
        lines.append(f"| {r['id']} | {r['name']} | {icon.get(r['status'], r['status'])} | {r['detail']} |")
    lines.append("")
    for r in results:
        if r["items"]:
            lines.append(f"### {r['id']} {r['name']}（{icon.get(r['status'], r['status'])}）")
            lines.append("")
            for it in r["items"]:
                lines.append(f"- {it}")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**处理指引**")
    lines.append("")
    lines.append("- F1/F2 图片问题：回到 extract.py（或人工截图后放进 `extracted/figures/` 并更新 manifest）。")
    lines.append("- F3 公式未复核：`python equations.py render --work <work>` 生成对照图，"
                 "逐张 `read_image` 比对后在 `equations/draft/eqNN.tex` 修正，再 `confirm`。")
    lines.append("- F6 数字不一致：逐个回原文核对；确认属实的差异写进 `paper/verify-allowlist.txt`。")
    lines.append("- F9 英文残留：把漏译段落补译；专有名词写进 allowlist。")
    lines.append("- F11 缺字形/Overfull：换字体集（`--fontset fandol`）或修公式与表格宽度。")
    (work / "paper").mkdir(parents=True, exist_ok=True)
    (work / "paper" / "质检报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (work / "paper" / "verify-report.json").write_text(
        json.dumps({"results": results, "fail": n_fail, "warn": n_warn}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="译文质检（数字/公式/结构/字体）")
    ap.add_argument("--work", default="translator-work")
    args = ap.parse_args(argv)
    work = abs_path(args.work)
    if not work.exists():
        print(f"[x] 找不到工作目录 {work}", file=sys.stderr)
        return 2
    c = Checker(work)
    if not c.tex and not c.pdf_path.exists():
        print("[x] 既没有 paper.tex 也没有 paper.pdf：请先运行 build.py", file=sys.stderr)
    rc = c.run()
    write_report(work, c.results)
    icon = {"PASS": "v", "WARN": "!", "FAIL": "x", "SKIP": "-"}
    print("[=] 质检结果")
    for r in c.results:
        print(f"  [{icon.get(r['status'],'?')}] {r['id']} {r['name']}: {r['detail']}")
        for it in r["items"][:6]:
            print("        - " + it[:160])
    n_fail = sum(1 for r in c.results if r["status"] == "FAIL")
    n_warn = sum(1 for r in c.results if r["status"] == "WARN")
    print(f"\n[=] FAIL {n_fail}；WARN {n_warn}；报告：{work / 'paper' / '质检报告.md'}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
