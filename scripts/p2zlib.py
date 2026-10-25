# -*- coding: utf-8 -*-
"""p2zlib —— 英文期刊 PDF 版面/文本/公式 分析库（skill-translator 共用底层）。

设计目标（按重要性排序）：
1. **只依赖 PyMuPDF**（pip install pymupdf 即可，全平台通用），不依赖 poppler/pandoc。
2. 输出自带**置信度与告警**：机器做不到 100% 的地方必须显式标出来，交给人工复核，
   绝不静默编造。公式尤其如此。
3. 纯函数式：不写文件（除显式 save_* 外），便于在 tests 里单独验证。

主要入口：
    doc = load_document(pdf_path)          -> Document
    Document.pages[i].blocks / .lines / .figures / .tables / .equations
    order_blocks(page)                     -> 阅读顺序
    find_captions(page)                    -> 图/表题注
    run_to_latex(chars)                    -> (latex, confidence, notes)
    layout_to_latex(chars, rules)          -> 多行公式（矩阵/分段/对齐）
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import fitz  # PyMuPDF

from mathmap import (
    BIG_OPS, BODY_FONT_HINTS, CLOSE_DELIM, DELIM_LATEX, FUNCTIONS,
    INLINE_CONTINUE, ITALIC_STOPWORDS, MATH_FONT_HINTS, OPEN_DELIM,
    SUB_MAP, SUP_MAP, SYMBOLS, UPRIGHT_WORDS,
)

# --------------------------------------------------------------------------- #
# 常量与正则
# --------------------------------------------------------------------------- #

MATH_FONT_RE = re.compile("|".join(MATH_FONT_HINTS), re.I)
BODY_FONT_RE = re.compile("|".join(BODY_FONT_HINTS), re.I)

# 图题注：Figure 1. / Fig. 1: / FIGURE 1 | / Extended Data Fig. 3.
# **编号后必须有分隔符**（. : | —），否则会把正文句 "Figure 1 shows ..." 误判为图注。
FIG_CAP_RE = re.compile(
    r"^\s*(?P<prefix>(?:Extended\s+Data\s+|Supplementary\s+|Suppl\.?\s+|Online\s+)?"
    r"(?:Fig(?:ure)?s?\.?|FIGURE|FIG)\s*\.?\s*(?P<num>\d+[a-zA-Z]?))"
    r"\s*(?P<sep>[.:\|\u2014\u2013])\s*",
)
# 表题注：Table 1. / TABLE 1: / Table 1 —
TAB_CAP_RE = re.compile(
    r"^\s*(?P<prefix>(?:Extended\s+Data\s+|Supplementary\s+|Suppl\.?\s+)?"
    r"(?:TABLE|Table|Tab\.)\s*\.?\s*(?P<num>\d+[a-zA-Z]?))"
    r"\s*(?P<sep>[.:\|\u2014\u2013])\s*",
)
# 章节标题关键词（用于无字号信息时的兜底）
SECTION_WORDS = {
    "abstract", "introduction", "background", "related work", "methods",
    "method", "materials and methods", "methodology", "theory", "model",
    "models", "results", "results and discussion", "discussion",
    "conclusion", "conclusions", "concluding remarks", "summary",
    "acknowledgements", "acknowledgments", "references", "bibliography",
    "appendix", "supplementary material", "supplementary information",
    "data availability", "author contributions", "funding", "highlights",
    "keywords", "experimental", "experimental section", "numerical methods",
    "limitations", "future work",
}
# 参考文献区起点
REF_HEAD_RE = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references|bibliography|references and notes|"
    r"literature cited)\s*$", re.I)
# 页脚页码
PAGENUM_RE = re.compile(r"^\s*(?:\|\s*)?\d{1,4}\s*(?:\|\s*)?$|^\s*page\s+\d+\s*(of\s+\d+)?\s*$", re.I)
# 行末连字符
HYPHEN_END_RE = re.compile(r"(\w)[\-\u2010\u00ad]$")

# 西文连字符号（LaTeX 输出常见）：不还原会让译文出现 "e\uFB03cient" 这类乱字符
LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "ft", "\ufb06": "st",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00ad": "",
    "\u00a0": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ",
}


def silence_mupdf_errors() -> None:
    """关闭 MuPDF 的 C 层错误打印（坏图/损坏压缩流会刷屏，但不应打扰用户）。"""
    try:
        fitz.TOOLS.mupdf_display_errors(False)
        fitz.TOOLS.mupdf_display_warnings(False)
    except Exception:                                       # pragma: no cover
        pass


def normalize_text(s: str) -> str:
    """统一连字符、连字、各种空格。"""
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    return s

MIN_MATH_CONFIDENCE = 0.75   # 低于此值必须人工复核


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class Char:
    """一个字形（字符级）。坐标单位为 PDF point，原点在页面左上角。"""
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    bx: float          # baseline 起点 x
    by: float          # baseline y
    font: str
    size: float
    flags: int

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def italic(self) -> bool:
        return bool(self.flags & 2)

    @property
    def bold(self) -> bool:
        return bool(self.flags & 16)

    @property
    def serif(self) -> bool:
        return bool(self.flags & 4)

    @property
    def mono(self) -> bool:
        return bool(self.flags & 8)

    @property
    def is_math_font(self) -> bool:
        return bool(MATH_FONT_RE.search(self.font or ""))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Char({self.c!r}@{self.x0:.1f},{self.by:.1f},{self.size:.1f},{self.font})"


@dataclass
class Line:
    """一行（PyMuPDF 的行分组，通常可信任）。"""
    bbox: Tuple[float, float, float, float]
    chars: List[Char] = field(default_factory=list)
    block_no: int = -1
    line_no: int = -1

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def w(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def h(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def size(self) -> float:
        """行内主导字号（按字符数加权的中位数）。"""
        if not self.chars:
            return 0.0
        return _weighted_median([c.size for c in self.chars])

    @property
    def real_chars(self) -> List[Char]:
        return [c for c in self.chars if c.c.strip()]

    def text(self) -> str:
        """按字距重建文本（自动补空格，处理无空格字形的 PDF）。"""
        return chars_to_text(self.chars)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Line({self.text()[:60]!r})"


@dataclass
class Block:
    """一个块（PyMuPDF 的 block，通常≈一个段落）。"""
    bbox: Tuple[float, float, float, float]
    lines: List[Line] = field(default_factory=list)
    block_no: int = -1
    col: int = 0                 # 0=左栏,1=右栏,-1=通栏
    role: str = "body"           # body|heading|caption|math|ref|header|footer|table|figure|list
    level: int = 0               # 标题层级
    conf: float = 1.0
    notes: List[str] = field(default_factory=list)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def w(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def h(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def text(self) -> str:
        return "\n".join(ln.text() for ln in self.lines)

    def chars(self) -> List[Char]:
        out: List[Char] = []
        for ln in self.lines:
            out.extend(ln.chars)
        return out

    def size(self) -> float:
        if not self.lines:
            return 0.0
        return _weighted_median([c.size for ln in self.lines for c in ln.chars])

    def __repr__(self) -> str:  # pragma: no cover
        return f"Block({self.role},col={self.col},{self.text()[:50]!r})"


@dataclass
class Graphic:
    """矢量图形路径的包围盒。"""
    bbox: Tuple[float, float, float, float]
    kind: str = "path"        # path|rule|image
    items: int = 0


@dataclass
class ImageInfo:
    xref: int
    bbox: Tuple[float, float, float, float]
    width: int
    height: int
    ext: str


@dataclass
class TableInfo:
    bbox: Tuple[float, float, float, float]
    rows: List[List[str]] = field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    caption: str = ""
    caption_bbox: Optional[Tuple[float, float, float, float]] = None
    number: str = ""
    strategy: str = ""
    quality: float = 0.0
    header_rows: int = 1
    page: int = -1
    notes: List[str] = field(default_factory=list)


@dataclass
class EquationInfo:
    index: int                       # 1-based，输出序号（eq01...）
    number: str                      # 原文编号，如 "3"（可能为空）
    bbox: Tuple[float, float, float, float]
    page: int
    raw_text: str = ""
    latex: str = ""
    confidence: float = 0.0
    notes: List[str] = field(default_factory=list)
    unmapped: List[str] = field(default_factory=list)
    multi_row: bool = False


@dataclass
class FigureInfo:
    index: int                       # 1-based -> fig1.png
    number: str                      # 原文编号
    page: int
    bbox: Tuple[float, float, float, float]
    caption: str = ""
    caption_bbox: Optional[Tuple[float, float, float, float]] = None
    kind: str = "vector"             # vector|raster|mixed|fallback-raster|missing
    panels: int = 1
    has_text_inside: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass
class PageData:
    index: int
    rect: Tuple[float, float, float, float]
    rotation: int = 0
    blocks: List[Block] = field(default_factory=list)
    graphics: List[Graphic] = field(default_factory=list)
    rules: List[Tuple[float, float, float, float]] = field(default_factory=list)
    images: List[ImageInfo] = field(default_factory=list)
    split_x: Optional[float] = None  # 两栏时的栏间分割线
    n_cols: int = 1
    is_reference_zone: bool = False

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


@dataclass
class Document:
    path: str
    pages: List[PageData]
    n_cols: int = 1
    body_size: float = 10.0
    meta: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def n_pages(self) -> int:
        return len(self.pages)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _weighted_median(values: Sequence[float]) -> float:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return 0.0
    return vals[len(vals) // 2]


def _most_common(values: Sequence[Any]) -> Any:
    if not values:
        return None
    counts: Dict[Any, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def order_chars(chars: Sequence[Char], tol_ratio: float = 0.5) -> List[Char]:
    """按"阅读顺序"排列字符：先按基线分带，带内按 x 排。

    为什么不能直接按 x 排：矩阵/分式的多行内容会交错；
    为什么不能直接按基线排：上标（如 10^{-3} 的 −3）会被排到基数前面，
    导致 "10^{-3}" 变成 "^{-3}10"。分带容差取 0.5 字号，正好把上下标并入同一带。
    """
    cs = [c for c in chars if c.c.strip()]
    if not cs:
        return []
    dom = _weighted_median([c.size for c in cs]) or 10.0
    tol = tol_ratio * dom
    bands: List[Dict[str, Any]] = []
    for c in sorted(cs, key=lambda c: (c.by, c.x0)):
        if bands and abs(c.by - bands[-1]["y"]) <= tol:
            bands[-1]["items"].append(c)
            bands[-1]["y"] = _weighted_median([x.by for x in bands[-1]["items"]])
        else:
            bands.append({"y": c.by, "items": [c]})
    out: List[Char] = []
    for b in bands:
        out.extend(sorted(b["items"], key=lambda c: (c.x0, c.by)))
    return out


def chars_to_text(chars: Sequence[Char], gap_ratio: float = 0.16,
                  min_gap: float = 1.4) -> str:
    """按字距重建文本，自动补空格。

    阈值取"字号的 16% 且不小于 1.4pt"：西文的正常词间空格在 10pt 字号下约 3.3pt，
    一定超过阈值；而字符紧排/字距调整（kerning）通常小于 1pt，不会误插空格。
    阈值过严会出现 "everyoneofthese" 这类单词粘连，所以刻意放宽。
    """
    if not chars:
        return ""
    cs = order_chars(chars)
    out: List[str] = []
    prev: Optional[Char] = None
    for c in cs:
        if prev is not None:
            gap = c.x0 - prev.x1
            thr = max(min_gap, gap_ratio * max(c.size, prev.size, 1.0))
            if (gap >= thr and prev.c.strip() and c.c.strip()
                    and not prev.c.endswith(" ") and not c.c.startswith(" ")):
                out.append(" ")
        out.append(c.c)
        prev = c
    return normalize_text("".join(out).strip())


def rect_union(rects: Sequence[Sequence[float]]) -> Optional[Tuple[float, float, float, float]]:
    rs = [r for r in rects if r and (r[2] > r[0]) and (r[3] > r[1])]
    if not rs:
        return None
    return (min(r[0] for r in rs), min(r[1] for r in rs),
            max(r[2] for r in rs), max(r[3] for r in rs))


def rect_area(r: Sequence[float]) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def rect_inter(a: Sequence[float], b: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if r[2] <= r[0] or r[3] <= r[1]:
        return None
    return r


def rect_iou(a: Sequence[float], b: Sequence[float]) -> float:
    inter = rect_inter(a, b)
    if not inter:
        return 0.0
    ia = rect_area(inter)
    return ia / (rect_area(a) + rect_area(b) - ia)


def rect_expand(r: Sequence[float], pad: float) -> Tuple[float, float, float, float]:
    return (r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad)


def cluster_rects(rects: Sequence[Sequence[float]], gap: float = 8.0,
                  max_iter: int = 30) -> List[Tuple[float, float, float, float]]:
    """把彼此靠近的矩形并成簇（用于把一幅图的多个子图/路径合并）。"""
    groups: List[Tuple[float, float, float, float]] = [tuple(r) for r in rects]  # type: ignore
    for _ in range(max_iter):
        merged = False
        out: List[Tuple[float, float, float, float]] = []
        for r in groups:
            placed = False
            for i, o in enumerate(out):
                e = rect_expand(o, gap)
                if rect_inter(e, r):
                    out[i] = rect_union([o, r])  # type: ignore
                    placed = True
                    merged = True
                    break
            if not placed:
                out.append(r)
        groups = out
        if not merged:
            break
    return sorted(groups, key=lambda r: (r[1], r[0]))


# --------------------------------------------------------------------------- #
# 页面解析
# --------------------------------------------------------------------------- #

def _chars_from_span(span: Dict[str, Any]) -> List[Char]:
    font = span.get("font", "")
    size = float(span.get("size", 0.0))
    flags = int(span.get("flags", 0))
    out: List[Char] = []
    for ch in span.get("chars", []) or []:
        c = ch.get("c", "")
        bbox = ch.get("bbox") or span.get("bbox") or (0, 0, 0, 0)
        origin = ch.get("origin") or (bbox[0], bbox[3])
        out.append(Char(c=c, x0=float(bbox[0]), y0=float(bbox[1]),
                        x1=float(bbox[2]), y1=float(bbox[3]),
                        bx=float(origin[0]), by=float(origin[1]),
                        font=font, size=size, flags=flags))
    return out


def parse_page(page: "fitz.Page", index: int) -> PageData:
    """解析一页：块/行/字 + 图形 + 图片。"""
    pd = PageData(index=index, rect=tuple(page.rect), rotation=int(getattr(page, "rotation", 0) or 0))
    raw = page.get_text("rawdict")
    for bi, blk in enumerate(raw.get("blocks", [])):
        if blk.get("type", 0) != 0:      # 非文本块（图片）跳过，图片另走 get_images
            continue
        blines: List[Line] = []
        for li, ln in enumerate(blk.get("lines", [])):
            chars: List[Char] = []
            for sp in ln.get("spans", []):
                chars.extend(_chars_from_span(sp))
            if not chars:
                continue
            blines.append(Line(bbox=tuple(ln.get("bbox", (0, 0, 0, 0))),
                               chars=chars, block_no=bi, line_no=li))
        if not blines:
            continue
        bb = rect_union([ln.bbox for ln in blines]) or tuple(blk.get("bbox", (0, 0, 0, 0)))
        pd.blocks.append(Block(bbox=bb, lines=blines, block_no=bi))

    # 矢量路径 / 横线
    try:
        drawings = page.get_drawings()
    except Exception:                                     # pragma: no cover
        drawings = []
    for d in drawings:
        r = d.get("rect")
        if not r:
            continue
        rb = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
        n_items = len(d.get("items", []) or [])
        kind = "path"
        if (rb[3] - rb[1]) <= 1.5 and (rb[2] - rb[0]) >= 6.0:
            kind = "rule"
            pd.rules.append(rb)
        pd.graphics.append(Graphic(bbox=rb, kind=kind, items=n_items))
        # 个别 PDF 把长横线拆成线段/填充矩形 item，补一次扫描
        # （LaTeX 的分式横线、根号上横线就是这种 \rule 填充矩形）
        for it in d.get("items", []) or []:
            if not it:
                continue
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) <= 1.0 and abs(p1.x - p2.x) >= 6.0:
                    x0, x1 = sorted((float(p1.x), float(p2.x)))
                    y = (float(p1.y) + float(p2.y)) / 2.0
                    pd.rules.append((x0, y - 0.4, x1, y + 0.4))
            elif it[0] == "re":
                rr = it[1]
                w = float(rr.x1) - float(rr.x0)
                h = float(rr.y1) - float(rr.y0)
                if h <= 1.6 and w >= 6.0:
                    y = (float(rr.y0) + float(rr.y1)) / 2.0
                    pd.rules.append((float(rr.x0), y - 0.4, float(rr.x1), y + 0.4))

    # 栅格图片
    for img in page.get_images(full=True):
        xref = int(img[0])
        try:
            rects = page.get_image_rects(xref)
        except Exception:                                  # pragma: no cover
            rects = []
        for r in rects:
            pd.images.append(ImageInfo(xref=xref, bbox=(float(r.x0), float(r.y0), float(r.x1), float(r.y1)),
                                       width=int(img[2]), height=int(img[3]), ext=str(img[8] or "")))
    return pd


def load_document(pdf_path: str, page_limit: Optional[int] = None,
                  progress: bool = False) -> Document:
    """打开 PDF 并完成版面分析。"""
    silence_mupdf_errors()
    doc = fitz.open(pdf_path)
    pages: List[PageData] = []
    n = doc.page_count if page_limit is None else min(page_limit, doc.page_count)
    for i in range(n):
        pages.append(parse_page(doc[i], i))
        if progress:
            print(f"  parse page {i + 1}/{n}", flush=True)

    # 全文字号统计：正文 = 字符数最多的字号
    sizes: List[float] = []
    for p in pages:
        for b in p.blocks:
            for ln in b.lines:
                for c in ln.chars:
                    if c.c.strip() and not c.is_math_font:
                        sizes.append(round(c.size, 1))
    body_size = float(_most_common(sizes)) if sizes else 10.0

    d = Document(path=str(pdf_path), pages=pages, body_size=body_size)
    d.meta = dict(doc.metadata or {})
    d.n_cols = detect_document_columns(d)
    for p in pages:
        _detect_page_columns(p, d.n_cols)
        for b in p.blocks:
            b.col = classify_block_column(b, p.split_x, p.rect)
    doc.close()
    return d


# --------------------------------------------------------------------------- #
# 分栏检测
# --------------------------------------------------------------------------- #

def _column_split_score(blocks: Sequence[Block], split: float,
                        full_width_ratio: float = 0.72) -> Tuple[int, int, int]:
    """返回 (跨界块数, 左侧块数, 右侧块数)。通栏块（很宽）不计入跨界。"""
    left = right = cross = 0
    for b in blocks:
        if b.w > 0 and (b.w >= full_width_ratio * max(b.w, 1)) and b.w > 300:
            continue
        if b.x1 <= split + 1:
            left += 1
        elif b.x0 >= split - 1:
            right += 1
        else:
            cross += 1
    return cross, left, right


def detect_page_split_x(page: PageData) -> Optional[float]:
    """返回最佳分割 x（两栏）或 None（单栏）。"""
    w = page.width
    page_area = page.width * page.height
    blocks = [b for b in page.blocks
              if rect_area(b.bbox) < 0.7 * page_area and len(b.text().strip()) > 15]
    if len(blocks) < 6:
        return None
    candidates: List[Tuple[Tuple[int, float], float, int, int]] = []
    lo, hi, steps = 0.32, 0.68, 37
    for i in range(steps):
        s = w * (lo + (hi - lo) * i / (steps - 1))
        cross, left, right = _column_split_score(blocks, s)
        if left < 2 or right < 2:
            continue
        candidates.append(((cross, abs(s - w / 2.0)), s, left, right))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    best = candidates[0]
    cross, s, left, right = best[0][0], best[1], best[2], best[3]
    if cross <= max(1, len(blocks) // 20):
        return s
    return None


def detect_document_columns(d: Document) -> int:
    """判定整篇是否两栏（按页投票）。"""
    votes = 0
    total = 0
    for p in d.pages:
        if len(p.blocks) < 6:
            continue
        total += 1
        if detect_page_split_x(p) is not None:
            votes += 1
    if total == 0:
        return 1
    return 2 if votes >= max(1, int(total * 0.6)) else 1


def _detect_page_columns(page: PageData, doc_cols: int) -> None:
    if doc_cols == 2:
        page.n_cols = 2
        page.split_x = detect_page_split_x(page) or (page.rect[0] + page.width / 2.0)
    else:
        page.n_cols = 1
        page.split_x = None


def classify_block_column(b: Block, split_x: Optional[float],
                          rect: Sequence[float]) -> int:
    """判断块属于左栏(0)/右栏(1)/通栏(-1)。"""
    if split_x is None:
        return 0
    w = rect[2] - rect[0]
    if b.x1 <= split_x + 2:
        return 0
    if b.x0 >= split_x - 2:
        return 1
    if b.w > 0.78 * w:
        return -1
    # 跨栏但不够宽：按重心归属
    return 0 if (b.x0 + b.x1) / 2 < split_x else 1


def order_blocks(page: PageData) -> List[Block]:
    """按阅读顺序返回块：通栏块把页面切成若干"带"，带内先左栏后右栏。"""
    if page.split_x is None:
        return sorted(page.blocks, key=lambda b: (round(b.y0, 1), b.x0))
    full = sorted([b for b in page.blocks if b.col == -1], key=lambda b: b.y0)
    bands: List[Tuple[float, float]] = []
    prev_bottom = page.rect[1]
    for f in full:
        bands.append((prev_bottom, f.y0))
        prev_bottom = f.y1
    bands.append((prev_bottom, page.rect[3]))

    ordered: List[Block] = []
    for (top, bottom) in bands:
        in_band = [b for b in page.blocks
                   if b.col != -1 and b.y0 >= top - 2 and b.y1 <= bottom + 2]
        ordered.extend(sorted([b for b in in_band if b.col == 0], key=lambda b: (round(b.y0, 1), b.x0)))
        ordered.extend(sorted([b for b in in_band if b.col == 1], key=lambda b: (round(b.y0, 1), b.x0)))
        for f in full:
            if abs(f.y0 - top) < 2 or (f.y0 >= top - 2 and f.y0 < bottom + 2):
                if f not in ordered:
                    ordered.append(f)
    # 兜底：漏掉的块按位置追加
    for b in page.blocks:
        if b not in ordered:
            ordered.append(b)
    return ordered


# --------------------------------------------------------------------------- #
# 角色判定（页眉页脚、标题、题注、数学块）
# --------------------------------------------------------------------------- #

def _norm_running_head(t: str) -> str:
    """把文本里的数字替换成 #，用于识别"只差页码/卷号"的跨页重复行（期刊运行页眉）。"""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", t)).strip()


def mark_headers_footers(d: Document) -> None:
    """把重复出现在页顶/页底的行标为 header/footer。

    两类都会命中：
    * **完全相同**的文本（普通页眉页脚）；
    * **只差页码/卷号**的文本（期刊运行页眉，如 "J. Test Phys. 12 (2024) 345–352 1/2/3"）——
      用"数字归一化成 #"后再计数识别，否则每页文本都不同、永远判不出来，会漏进译文源文。
    """
    top_cands: Dict[str, int] = {}
    bot_cands: Dict[str, int] = {}
    top_norm: Dict[str, int] = {}
    bot_norm: Dict[str, int] = {}
    n = max(1, d.n_pages)
    for p in d.pages:
        h = p.height
        for b in p.blocks:
            t = b.text().strip()
            if not t:
                continue
            norm = _norm_running_head(t)
            if b.y1 < p.rect[1] + 0.09 * h:
                top_cands[t] = top_cands.get(t, 0) + 1
                top_norm[norm] = top_norm.get(norm, 0) + 1
            if b.y0 > p.rect[3] - 0.09 * h:
                bot_cands[t] = bot_cands.get(t, 0) + 1
                bot_norm[norm] = bot_norm.get(norm, 0) + 1
    need = max(2, int(n * 0.5))
    for p in d.pages:
        h = p.height
        for b in p.blocks:
            t = b.text().strip()
            if not t:
                continue
            norm = _norm_running_head(t)
            non_digit = sum(1 for c in norm if not c.isdigit())
            if b.y1 < p.rect[1] + 0.09 * h:
                same = top_cands.get(t, 0) >= need
                # 归一化后重复：要求它确实像"页眉文字"（含非数字字符、且不只是一两个字符）
                norm_rep = top_norm.get(norm, 0) >= need and non_digit >= 4
                if same or norm_rep or PAGENUM_RE.match(t):
                    b.role = "header"
                    b.notes.append("running-head/pagenumber")
            if b.y0 > p.rect[3] - 0.09 * h:
                same = bot_cands.get(t, 0) >= need and len(t) < 60
                norm_rep = bot_norm.get(norm, 0) >= need and non_digit >= 4 and len(t) < 60
                if same or norm_rep:
                    b.role = "footer"
                    b.notes.append("footer/page-number")
                elif PAGENUM_RE.match(t):
                    b.role = "footer"
                    b.notes.append("page-number")


def classify_blocks(d: Document) -> None:
    """判定每个块的角色：heading / caption / math / body / ref 等。"""
    body = d.body_size
    for p in d.pages:
        for b in p.blocks:
            if b.role in ("header", "footer"):
                continue
            text = b.text().strip()
            if not text:
                b.role = "empty"
                continue
            fs = b.size()
            first = b.lines[0].text().strip() if b.lines else ""
            n_lines = len([ln for ln in b.lines if ln.text().strip()])
            words = len(text.split())

            if FIG_CAP_RE.match(first):
                b.role, b.level = "caption", 0
                b.notes.append("figure-caption")
                continue
            if TAB_CAP_RE.match(first):
                b.role, b.level = "caption", 0
                b.notes.append("table-caption")
                continue

            # 标题：字号明显大 或 加粗且行数少且短
            is_big = fs >= body * 1.10
            is_bold_short = (b.lines and b.lines[0].chars and
                             all(c.bold or c.size >= body * 1.02 for c in b.lines[0].real_chars)) \
                and n_lines <= 2 and words <= 18
            key = re.sub(r"^[\d\.\s]+", "", first).strip().rstrip(":").lower()
            is_known_section = key in SECTION_WORDS
            if n_lines <= 3 and words <= 22 and (is_big or is_known_section or (is_bold_short and words <= 12)):
                b.role = "heading"
                if fs >= body * 1.45:
                    b.level = 1
                elif fs >= body * 1.22:
                    b.level = 1
                elif fs >= body * 1.10:
                    b.level = 2
                else:
                    b.level = 3
                continue

            # 数学块：绝大多数，是数学字形
            mr = math_ratio(b)
            if mr >= 0.55:
                b.role = "math"
                b.conf = mr
                continue

            b.role = "body"


def mark_reference_zones(d: Document) -> None:
    """从 References/Bibliography 标题起，后续块标为 ref（不翻译）。"""
    ordered_global: List[Tuple[int, Block]] = []
    for p in d.pages:
        for b in order_blocks(p):
            ordered_global.append((p.index, b))
    in_refs = False
    for _, b in ordered_global:
        t = b.text().strip()
        if b.role == "heading" and REF_HEAD_RE.match(t):
            in_refs = True
            b.role = "ref-heading"
            continue
        if in_refs:
            if b.role == "heading" and re.match(r"^\s*(appendix|supplementary|acknowledg)", t, re.I):
                in_refs = False
                continue
            if b.role not in ("header", "footer"):
                b.role = "ref"


# --------------------------------------------------------------------------- #
# 数学片段识别与 LaTeX 还原
# --------------------------------------------------------------------------- #

def _level_of(c: Char, base_size: float, base_y: float) -> str:
    """相对基线/字号判断上标/下标。"""
    if base_size <= 0:
        return ""
    if c.size <= 0.85 * base_size:
        if c.by < base_y - 0.16 * base_size:
            return "sup"
        if c.by > base_y + 0.10 * base_size:
            return "sub"
    return ""


def math_ratio(b: Block) -> float:
    """块内"数学字形"占比（按字符数）。"""
    tot = mathc = 0
    for ln in b.lines:
        for c in ln.chars:
            if not c.c.strip():
                continue
            tot += 1
            if c.is_math_font or c.c in SYMBOLS or c.c in SUP_MAP or c.c in SUB_MAP:
                mathc += 1
    return (mathc / tot) if tot else 0.0


def _is_math_char(c: Char) -> bool:
    return bool(c.is_math_font or c.c in SYMBOLS or c.c in SUP_MAP or c.c in SUB_MAP)


def find_math_runs(line: Line, base_size: Optional[float] = None) -> List[Tuple[int, int]]:
    """在行内找出数学片段（返回 chars 下标区间 [start, end)）。

    规则：
    1) 数学字体字形 / Unicode 数学符号 -> 命中；
    2) 与命中字形相邻、且字距很近的 数字/运算符/括号 一并吞入；
    3) 以"数字为底"的上标下标（如 10^{-3}）算数学；以单词为底的（脚注角标）不算；
    4) 斜体孤立短字母（变量）算数学，但排除 et al. 这类拉丁缩写。
    """
    chars = line.chars
    if not chars:
        return []
    sizes = [c.size for c in chars if c.c.strip()]
    dom = base_size or _weighted_median(sizes)
    bases = [round(c.by, 1) for c in chars if c.c.strip()]
    base_y = _weighted_median(bases)

    mark = [False] * len(chars)
    strong = [False] * len(chars)
    for i, c in enumerate(chars):
        if not c.c.strip():
            continue
        if _is_math_char(c):
            mark[i] = True
            strong[i] = True
    # 3) 数字为底的上标/下标
    for i, c in enumerate(chars):
        if mark[i] or not c.c.strip():
            continue
        lv = _level_of(c, dom, base_y)
        if not lv:
            continue
        # 找最近的左侧基底字符
        j = i - 1
        while j >= 0 and not chars[j].c.strip():
            j -= 1
        if j >= 0:
            base_chars = [chars[j]]
            k = j - 1
            while k >= 0 and chars[k].c.strip() and chars[k].x1 >= chars[j].x0 - 0.6 * chars[j].size:
                base_chars.append(chars[k])
                k -= 1
            base_txt = "".join(ch.c for ch in reversed(base_chars))
            if base_txt and (base_txt[-1].isdigit() or base_txt[-1] in ")]}"):
                mark[i] = True
                strong[i] = True
    # 4) 斜体孤立变量
    tokens = _tokenize(chars)
    for (a, b) in tokens:
        seg = [c for c in chars[a:b] if c.c.strip()]
        if not seg or len(seg) > 3:
            continue
        txt = "".join(c.c for c in seg)
        if not txt.isalpha() or len(txt) > 3:
            continue
        if txt.lower() in ITALIC_STOPWORDS:
            continue
        if all(c.italic for c in seg) and not any(_is_math_char(c) for c in seg):
            # 上下文：两侧必须是空白/标点，避免把单词内部切碎
            left_ok = a == 0 or not chars[a - 1].c.isalpha()
            right_ok = b >= len(chars) or not chars[b].c.isalpha()
            if left_ok and right_ok:
                for i in range(a, b):
                    mark[i] = True
    # 2) 邻近膨胀
    for i, c in enumerate(chars):
        if mark[i] or not c.c.strip() or c.c not in INLINE_CONTINUE:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(chars) and mark[j]:
                gap = min(abs(chars[j].x0 - c.x1), abs(c.x0 - chars[j].x1))
                if gap < 0.6 * max(c.size, chars[j].size):
                    mark[i] = True
                    break
    # 合并为区间（允许空隙 < 1.2 空格宽）
    runs: List[Tuple[int, int]] = []
    i = 0
    while i < len(chars):
        if not mark[i]:
            i += 1
            continue
        j = i
        last = i
        while j < len(chars):
            if mark[j]:
                last = j
                j += 1
                continue
            nxt = j
            while nxt < len(chars) and not chars[nxt].c.strip() and not mark[nxt]:
                nxt += 1
            if nxt < len(chars) and mark[nxt] and (chars[nxt].x0 - chars[last].x1) < 1.2 * max(chars[last].size, 1):
                j = nxt
                continue
            break
        runs.append((i, last + 1))
        i = last + 1
    # 过滤：至少含一个强数学字符（避免把 "12.4" 之类误判）
    runs = [(a, b) for (a, b) in runs if any(strong[a:b])]
    # 边界修正：数字被切成 "4$5.0$" / "29$8.15$" 时把紧邻的数字、小数点吞进来
    # （否则译者需要靠人眼把 45.0 / 298.15 拼回去，极易抄错）
    def _covered(i: int, skip: Tuple[int, int]) -> bool:
        return any(x <= i < y for (x, y) in runs if (x, y) != skip)

    fixed: List[Tuple[int, int]] = []
    for (a, b) in runs:
        size_l = max(chars[a].size, 1.0)
        while (a > 0 and chars[a - 1].c in "0123456789.,%"
               and not _covered(a - 1, (a, b))
               and (chars[a].x0 - chars[a - 1].x1) < 0.45 * size_l):
            a -= 1
            size_l = max(chars[a].size, 1.0)
        size_r = max(chars[b - 1].size, 1.0)
        while (b < len(chars) and chars[b].c in "0123456789.,%"
               and not _covered(b, (a, b))
               and (chars[b].x0 - chars[b - 1].x1) < 0.45 * size_r):
            b += 1
            size_r = max(chars[b - 1].size, 1.0)
        fixed.append((a, b))
    return fixed


def _tokenize(chars: Sequence[Char], gap_ratio: float = 0.30) -> List[Tuple[int, int]]:
    """按字距切词，返回 [start,end) 列表。"""
    if not chars:
        return []
    toks: List[Tuple[int, int]] = []
    start = 0
    for i in range(1, len(chars)):
        prev, cur = chars[i - 1], chars[i]
        gap = cur.x0 - prev.x1
        if gap > gap_ratio * max(cur.size, prev.size, 1.0) or prev.c == " " or cur.c == " ":
            toks.append((start, i))
            start = i
    toks.append((start, len(chars)))
    return [(a, b) for (a, b) in toks if b > a]


def _emit_token(txt: str, italic: bool) -> str:
    """把一个"词"转成 LaTeX：函数名/直立缩写/普通字母数字。"""
    if not txt:
        return ""
    if txt in FUNCTIONS:
        return FUNCTIONS[txt]
    if txt in UPRIGHT_WORDS:
        return r"\mathrm{%s}" % txt
    out: List[str] = []
    for ch in txt:
        # 上一步刚吐出一个以字母结尾的 LaTeX 命令（\partial、\beta ...）而当前又是
        # 字母数字时，必须补空格：否则 "\partialu" 会被 LaTeX 当成未定义命令。
        if (out and ch.isalnum() and out[-1].startswith("\\")
                and out[-1][-1:].isalpha()):
            out.append(" ")
        if ch in SYMBOLS:
            out.append(SYMBOLS[ch])
        elif ch in SUB_MAP:
            out.append("_{%s}" % SUB_MAP[ch])
        elif ch in SUP_MAP:
            out.append("^{%s}" % SUP_MAP[ch])
        elif ch in "%$#&_{}":
            out.append("\\" + ch)
        elif ch in "^~":
            out.append(r"\hat{}" if ch == "^" else r"\sim")
        elif ch == "\u00a0":
            out.append("~")
        else:
            out.append(ch)
    s = "".join(out)
    s = s.replace("--", "-")
    return s


def _group_by_level(chars: Sequence[Char], dom: float, base_y: float) -> List[Tuple[str, List[Char]]]:
    """把字符按 上标/下标/正常 切分成组。"""
    groups: List[Tuple[str, List[Char]]] = []
    cur_lv: Optional[str] = None
    cur: List[Char] = []
    for c in chars:
        lv = _level_of(c, dom, base_y)
        if cur_lv is None or lv == cur_lv:
            cur.append(c)
            cur_lv = lv if cur_lv is None else cur_lv
        else:
            groups.append((cur_lv or "", cur))
            cur = [c]
            cur_lv = lv
    if cur:
        groups.append((cur_lv or "", cur))
    return groups


def _split_by_rules(chars: Sequence[Char],
                    rules: Sequence[Sequence[float]]) -> Optional[Tuple[List[Char], List[Char], Tuple[float, float, float, float]]]:
    """如果存在横线把字符分成上下两半，返回 (分子, 分母, 横线)。"""
    if not chars or not rules:
        return None
    bx0 = min(c.x0 for c in chars)
    bx1 = max(c.x1 for c in chars)
    best: Optional[Tuple[float, List[Char], List[Char], Tuple[float, float, float, float]]] = None
    for r in rules:
        ry = (r[1] + r[3]) / 2.0
        rh = r[3] - r[1]
        if rh > 1.8:
            continue
        overlap = min(bx1, r[2]) - max(bx0, r[0])
        if overlap <= 0.45 * (bx1 - bx0):
            continue
        num = [c for c in chars if c.by <= ry + 0.5 and c.y1 <= ry + 1.0]
        den = [c for c in chars if c.by > ry + 0.5]
        if not num or not den:
            continue
        score = len(num) + len(den) + overlap
        if best is None or score > best[0]:
            best = (score, num, den, (r[0], r[1], r[2], r[3]))
    if best is None:
        return None
    return best[1], best[2], best[3]


def _find_radicals(chars: Sequence[Char], rules: Sequence[Sequence[float]]
                   ) -> List[Tuple[int, int, int]]:
    """返回 (√字符下标, 根号内容起始下标, 根号内容结束下标)。

    依据 √ 后方的上横线判定根号范围；只考虑 √ 右侧的字符，
    否则会把同一行左边的 "r =" 也吞进根号里。
    """
    out: List[Tuple[int, int, int]] = []
    for i, c in enumerate(chars):
        if c.c != "\u221a":
            continue
        right_of_sign = [k for k in range(len(chars))
                         if k != i and chars[k].x0 >= c.x1 - 1.0]
        if not right_of_sign:
            continue
        inner_start = min(right_of_sign)
        best_end: Optional[int] = None
        for r in rules:
            if r[0] >= c.x0 - 1 and r[1] < c.y1 and r[1] > c.y0 - 3:
                cand = [k for k in right_of_sign if chars[k].x0 <= r[2] + 1]
                if cand:
                    e = max(cand) + 1
                    best_end = e if best_end is None else max(best_end, e)
        if best_end is None:
            # 无横线：退化为紧随其后的一小段
            end = inner_start + 1
            while end < len(chars) and (chars[end].x0 - chars[end - 1].x1) < 0.6 * chars[end].size:
                end += 1
            best_end = end
        if best_end > inner_start:
            out.append((i, inner_start, best_end))
    return out


def _fraction_atoms(cs: Sequence[Char], rules: Sequence[Sequence[float]],
                    dom: float) -> List[Tuple[Sequence[float], List[Char], List[Char]]]:
    """找横线对应的"分式区域"，返回 [(横线, 分子字符, 分母字符)]。

    判据：细横线（高度≤2.2pt）；分子=横线之上、x 落在横线范围内的字符；
    分母=横线之下、x 落在范围内的字符。**不要求横线占据整段宽度**，
    因此 "∂u/∂t = ..." 这种"分式只是表达式一部分"的情况也能还原。
    """
    out: List[Tuple[Sequence[float], List[Char], List[Char]]] = []
    for r in rules or ():
        if (r[3] - r[1]) > 2.2 or (r[2] - r[0]) < 4.0:
            continue
        ry = (r[1] + r[3]) / 2.0
        pad = max(1.5, 0.35 * (r[2] - r[0]))
        lo, hi = r[0] - pad, r[2] + pad
        num = [c for c in cs if lo <= (c.x0 + c.x1) / 2.0 <= hi and c.y1 <= ry + 0.6]
        den = [c for c in cs if lo <= (c.x0 + c.x1) / 2.0 <= hi and c.y0 >= ry - 0.6]
        if not num or not den:
            continue
        out.append((r, num, den))
    out.sort(key=lambda t: -(t[0][2] - t[0][0]))
    return out


def _strip_single_level(s: str) -> str:
    """去掉多余的一层 _{...} / ^{...}：_ {_{i=1}} -> i=1（避免下下标变小）。"""
    s = s.strip()
    if len(s) >= 4 and s[0] in "_^" and s[1] == "{" and s.endswith("}"):
        depth = 0
        for i, ch in enumerate(s[1:], start=1):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[2:i] if i == len(s) - 1 else s
    return s


# --------------------------------------------------------------------------- #
# 符号守恒校验（不依赖视觉的公式正确性检查）
# --------------------------------------------------------------------------- #

# LaTeX 命令 -> 原 Unicode 字符（SYMBOLS 的反查）
_CMD_TO_CHAR: Dict[str, str] = {}
for _ch, _cmd in SYMBOLS.items():
    _CMD_TO_CHAR.setdefault(_cmd, _ch)
for _ch, _cmd in BIG_OPS.items():
    _CMD_TO_CHAR.setdefault(_cmd, _ch)

# 结构性命令：本身不产生符号（\left( 里的括号由后面的字符贡献）
_STRUCTURAL_CMDS = {
    r"\left", r"\right", r"\frac", r"\dfrac", r"\tfrac", r"\big", r"\Big",
    r"\bigg", r"\Bigg", r"\bigl", r"\bigr", r"\Bigl", r"\Bigr", r"\displaystyle",
    r"\textstyle", r"\scriptstyle", r"\limits", r"\nolimits", r"\quad",
    r"\qquad", r"\,", r"\;", r"\!", r"\ ", r"\hspace", r"\mathrm", r"\mathbf",
    r"\mathit", r"\mathsf", r"\mathtt", r"\mathcal", r"\mathbb", r"\bm",
    r"\boldsymbol", r"\operatorname", r"\text", r"\textrm", r"\mbox",
    r"\hat", r"\bar", r"\vec", r"\dot", r"\tilde", r"\overline", r"\underline",
    r"\pmod", r"\left.", r"\right.", r"\middle", r"\mathrel", r"\mathbin",
}
_SKIP_CMDS = {r"\\", r"\,", r"\;", r"\!", r"\ ", r"\quad", r"\qquad"}
_SKIP_PUNCT = set("{}^_&$")

_TOKEN_RE = re.compile(r"\\begin\{[^}]*\}|\\end\{[^}]*\}|\\label\{[^}]*\}|"
                       r"\\tag\{([^}]*)\}|\\[A-Za-z]+|\\.|.")
_TOKEN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_TOKEN_OPS = set("=+-*/<>|,;:.!?()[]")

# 符号守恒校验时的等价类：不同形式的减号/空格算同一个符号
_EQUIV = {"\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2010": "-", "\u2011": "-",
          "\u00d7": "\u00d7", "\u00b7": "\u22c5", "\u2217": "*", "\u00b1": "\u00b1"}


def _equ_equiv(d: Dict[str, int]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k, v in d.items():
        k2 = _EQUIV.get(k, k)
        out[k2] = out.get(k2, 0) + v
    return out


def latex_symbol_multiset(latex: str) -> "Dict[str, int]":
    """把 LaTeX 数学片段归一化成"基符号"多重集，便于与原文抽取的字符比对。

    * \\frac{a}{b} -> a, b（分数线不是字符）
    * \\sqrt{x}   -> √, x
    * \\alpha     -> α；\\sum -> ∑；\\times -> ×
    * 结构命令（\\left、\\mathrm 等）不产生符号，其花括号内容照常计入
    * \\tag{3}    -> 3（公式编号的数字要保留）
    """
    counts: Dict[str, int] = {}
    i = 0
    s = latex or ""
    while i < len(s):
        m = _TOKEN_RE.match(s, i)
        if not m:
            i += 1
            continue
        tok = m.group(0)
        i = m.end()
        if tok.startswith("\\begin") or tok.startswith("\\end") or tok.startswith("\\label"):
            continue
        if tok.startswith("\\tag"):
            inner = m.group(1) or ""
            for ch in inner:
                if ch.isalnum():
                    counts[ch] = counts.get(ch, 0) + 1
            continue
        if tok.startswith("\\"):
            if tok in _SKIP_CMDS:
                continue
            if tok == r"\sqrt":
                counts["\u221a"] = counts.get("\u221a", 0) + 1
                continue
            if tok in _STRUCTURAL_CMDS:
                continue
            ch = _CMD_TO_CHAR.get(tok)
            if ch:
                counts[ch] = counts.get(ch, 0) + 1
                continue
            # 未登记的命令：按命令名字母计入（如 \sin -> sin），便于发现未知命令
            name = tok[1:]
            for c2 in name:
                counts[c2] = counts.get(c2, 0) + 1
            continue
        if tok in _SKIP_PUNCT or tok.isspace():
            continue
        if tok in _TOKEN_CHARS or tok in _TOKEN_OPS or tok in SYMBOLS:
            counts[tok] = counts.get(tok, 0) + 1
            continue
        # 其它符号（≤ ≥ ∈ 等 unicode）也计入
        if not tok.isalnum():
            counts[tok] = counts.get(tok, 0) + 1
    return counts


def chars_symbol_multiset(chars: Sequence[Char]) -> "Dict[str, int]":
    """原文抽取字符 -> 基符号多重集（去掉空白、结构标点、私有区碎片）。"""
    counts: Dict[str, int] = {}
    for c in chars:
        ch = normalize_text(c.c)
        if not ch or _is_ignorable(ch):
            continue
        for x in ch:
            if x in _SKIP_PUNCT or x.isspace():
                continue
            if x.isalnum() or x in _TOKEN_OPS or x in SYMBOLS:
                counts[x] = counts.get(x, 0) + 1
            elif not x.isalnum():
                counts[x] = counts.get(x, 0) + 1
    return counts


def symbol_diff(orig_chars: Sequence[Char], latex: str) -> Dict[str, Any]:
    """比对"原文字符"与"还原 LaTeX"的符号多重集。

    返回 {'missing': {...}, 'extra': {...}, 'ok': bool}：
      missing —— 原文有、还原里没有的符号（**危险，必须修**）
      extra   —— 还原里有、原文没有的符号（可能编造，需确认）
    """
    a = _equ_equiv(chars_symbol_multiset(orig_chars))
    b = _equ_equiv(latex_symbol_multiset(latex))
    missing = {k: a[k] - b.get(k, 0) for k in a if a[k] > b.get(k, 0)}
    extra = {k: b[k] - a.get(k, 0) for k in b if b[k] > a.get(k, 0)}
    return {"missing": missing, "extra": extra, "ok": not missing}


def _cluster_by_gap(cs: Sequence[Char], dom: float) -> List[List[Char]]:
    """把（已按 x 排序的）字符按空隙切成若干簇。"""
    out: List[List[Char]] = []
    for c in cs:
        if out and (c.x0 - out[-1][-1].x1) <= 1.1 * dom:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def _assemble_with_atoms(cs: Sequence[Char], used_ids: Set[int],
                         atoms: Sequence[Tuple[float, str]],
                         rules: Sequence[Sequence[float]], dom: float,
                         by: float) -> str:
    """把"原子"（分式/根式等结构化片段）与剩余字符按 x 顺序拼成一条公式。"""
    rest = sorted([c for c in cs if id(c) not in used_ids], key=lambda c: c.x0)
    items: List[Tuple[float, str]] = []
    for cl in _cluster_by_gap(rest, dom):
        center = (min(c.x0 for c in cl) + max(c.x1 for c in cl)) / 2.0
        seg = run_to_latex(cl, rules, dom, by)[0]
        if seg:
            items.append((center, seg))
    items.extend(atoms)
    items.sort(key=lambda t: t[0])
    return _join_math_tokens([t[1] for t in items if t[1]])


def run_to_latex(chars: Sequence[Char], rules: Sequence[Sequence[float]] = (),
                 base_size: Optional[float] = None,
                 base_y: Optional[float] = None) -> Tuple[str, float, List[str], List[str]]:
    """把一段字符还原为 LaTeX 数学片段。

    返回 (latex, confidence, notes, unmapped_chars)。
    confidence 低于 MIN_MATH_CONFIDENCE 时必须人工复核。
    """
    notes: List[str] = []
    unmapped: List[str] = []
    had_pua = any(_is_pua(c.c) for c in chars)
    cs = order_chars([c for c in chars if not _is_ignorable(c.c)])
    if not cs:
        return "", 1.0, notes, unmapped
    if had_pua:
        notes.append("有可伸展大定界符碎片（矩阵/分段的外括号），已按结构处理")
    sizes = [c.size for c in cs]
    dom = base_size or _weighted_median(sizes)
    if base_y is not None:
        by = base_y
    else:
        # 主基线取"全字号字符"的基线中位数：只用中位数会被上下标（小字号、基线偏移）
        # 带偏，导致 x_i 里的 i 判不出是下标
        full = [c.by for c in cs if c.size >= 0.9 * dom] or [c.by for c in cs]
        by = _weighted_median(full)

    penalty = 0.0
    # --- 1) 分式（可出现在表达式的任意位置，也可能嵌套）---
    fracs = _fraction_atoms(cs, rules, dom)
    if fracs:
        r, num, den = fracs[0]
        used = {id(c) for c in num} | {id(c) for c in den}
        nl, ncf, nnotes, num_un = run_to_latex(num, rules, dom, by)
        dl, dcf, dnotes, den_un = run_to_latex(den, rules, dom, by)
        notes.extend(nnotes + dnotes)
        unmapped.extend(num_un + den_un)
        rule_center = (r[0] + r[2]) / 2.0
        latex = _assemble_with_atoms(cs, used, [(rule_center, r"\frac{%s}{%s}" % (nl, dl))],
                                     rules, dom, by)
        conf = min(ncf, dcf) - 0.05
        notes.append("fraction")
        return latex, max(0.2, conf), notes, unmapped

    # --- 2) 根号（同样按"原子"装配，避免把左侧的 "r =" 吞进根号）---
    rads = _find_radicals(cs, rules)
    if rads:
        used: Set[int] = set()
        atoms: List[Tuple[float, str]] = []
        min_conf = 1.0
        for (sig_idx, s0, s1) in rads:
            inner_chars = cs[s0:s1]
            if not inner_chars:
                continue
            inner, ci, nt, um = run_to_latex(inner_chars, rules, dom, by)
            notes.extend(nt)
            unmapped.extend(um)
            min_conf = min(min_conf, ci)
            used.add(id(cs[sig_idx]))
            for c in inner_chars:
                used.add(id(c))
            xs = [cs[sig_idx].x0] + [c.x1 for c in inner_chars]
            atoms.append(((cs[sig_idx].x0 + max(xs)) / 2.0, r"\sqrt{%s}" % inner))
        if atoms:
            latex = _assemble_with_atoms(cs, used, atoms, rules, dom, by)
            notes.append("radical")
            return latex, max(0.3, min_conf - 0.2), notes, unmapped

    # --- 3) 大运算符的上下限 ---
    INTEGRALS = {"\u222b", "\u222c", "\u222d", "\u222e"}
    for idx, c in enumerate(cs):
        if c.c not in BIG_OPS:
            continue
        op = BIG_OPS[c.c]
        ocx = (c.x0 + c.x1) / 2.0
        lim_lo, lim_hi = ocx - 0.8 * c.w - 4.0, ocx + 0.8 * c.w + 4.0

        def _is_limit(x: Char) -> bool:
            return lim_lo <= (x.x0 + x.x1) / 2.0 <= lim_hi and x is not c
        # 以"本段主基线"为参照判断上下限（比用运算符字形包围盒可靠：
        # 大运算符字形的 bbox 往往不贴基线，用它会误吞主行字符）
        above = [x for x in cs if _is_limit(x) and x.by <= by - 0.25 * dom]
        below = [x for x in cs if _is_limit(x) and x.by >= by + 0.25 * dom]
        # 积分号的上下限在**右侧**（LaTeX 的 \int 默认侧置上下限）
        if c.c in INTEGRALS:
            side = [x for x in cs
                    if x is not c and x.size <= 0.9 * dom
                    and c.x1 - 1.0 <= x.x0 <= c.x1 + 2.2 * c.w + 6.0
                    and not _is_limit(x)]
            side.sort(key=lambda x: x.x0)
            if side:
                first_x = side[0].x0
                cluster = [x for x in side if x.x0 - first_x <= 0.6 * dom]
                cluster.sort(key=lambda x: x.by)
                above.extend(cluster[:1])          # 基线更高者为上限
                below.extend(cluster[1:])
        if above or below:
            pre = [x for x in cs if x.x1 <= c.x0 + 0.5 and x not in above and x not in below]
            post = [x for x in cs if x.x0 >= c.x1 - 0.5 and x not in above and x not in below]
            lim = op
            if below:
                bl, _, _, _ = run_to_latex(below, rules, dom, by)
                lim += "_{%s}" % _strip_single_level(bl)
            if above:
                al, _, _, _ = run_to_latex(above, rules, dom, by)
                lim += "^{%s}" % _strip_single_level(al)
            parts: List[str] = []
            if pre:
                pl, _, _, _ = run_to_latex(pre, rules, dom, by)
                parts.append(pl)
            parts.append(lim)
            if post:
                ql, _, _, _ = run_to_latex(post, rules, dom, by)
                parts.append(ql)
            notes.append("big-operator-with-limits")
            return " ".join(p for p in parts if p), 0.8, notes, unmapped

    # --- 4) 普通序列：按层级 + 词 ---
    groups = _group_by_level(cs, dom, by)
    out_parts: List[str] = []
    for lv, gcs in groups:
        txt_tokens: List[str] = []
        for (a, b) in _tokenize(gcs):
            seg = [c for c in gcs[a:b] if c.c.strip()]
            if not seg:
                continue
            raw = "".join(c.c for c in seg)
            italic = all(c.italic for c in seg)
            if raw.strip() == "":
                continue
            latex = _emit_token(raw, italic)
            for ch in raw:
                if _is_ignorable(ch):
                    continue
                if (ch.isalpha() or ch.isdigit() or ch in "+-=<>()[]{}|,.;:!?'*/^_"):
                    continue
                if ch in SYMBOLS or ch in SUP_MAP or ch in SUB_MAP:
                    continue
                unmapped.append(ch)
            txt_tokens.append(latex)
        joined = _join_math_tokens(txt_tokens)
        if not joined:
            continue
        if lv == "sup":
            out_parts.append("^{%s}" % joined)
        elif lv == "sub":
            out_parts.append("_{%s}" % joined)
        else:
            out_parts.append(joined)
    latex = _join_math_tokens(out_parts)
    latex = _fix_delimiters(latex)

    # 置信度
    if unmapped:
        penalty += 0.18 * len(set(unmapped))
        notes.append("unmapped-chars:" + "".join(sorted(set(unmapped))))
    if latex.count("{") != latex.count("}"):
        penalty += 0.3
        notes.append("brace-mismatch")
    conf = max(0.05, 1.0 - penalty)
    return latex, conf, notes, sorted(set(unmapped))


def _join_math_tokens(tokens: Sequence[str]) -> str:
    """在数字/字母之间补空格（LaTeX 中多字母连写会被当成一个变量乘积，需要空格分开）。"""
    out = ""
    for t in tokens:
        if not t:
            continue
        if not out:
            out = t
            continue
        prev = out[-1]
        nxt = t[0]
        need = False
        if prev.isalnum() and nxt.isalnum():
            need = True
        if prev in ")" and nxt.isalnum():
            need = True
        if prev.isdigit() and nxt in r"\^{":
            need = False
        out += (" " if need else "") + t
    return out


def _fix_delimiters(latex: str) -> str:
    """若括号内含 \\frac 或 \\sum 等，补 \\left \\right。"""
    if "\\frac" not in latex and "\\sum" not in latex and "\\int" not in latex:
        return latex
    out = latex
    for op, cl in (("(", ")"), ("[", "]")):
        if op in out and cl in out:
            out = out.replace(op, r"\left" + op).replace(cl, r"\right" + cl)
    out = out.replace(r"\left\left", r"\left").replace(r"\right\right", r"\right")
    if out.count(r"\left") != out.count(r"\right"):
        # 不平衡则回退，避免编译失败
        return latex
    return out


def layout_to_latex(chars: Sequence[Char], rules: Sequence[Sequence[float]] = (),
                    base_size: Optional[float] = None) -> Tuple[str, float, List[str], bool]:
    """多行公式还原。返回 (latex, conf, notes, multi_row)。

    重要原则：**只在有结构证据时**才输出矩阵/对齐/分段环境；否则一律"展平"成单行数学
    交给 run_to_latex（它能处理分式/根式/大运算符上下限）。因为 PDF 里一个公式常被
    拆成多行文本，盲目按行猜环境会产生大量垃圾草稿。
    """
    cs_all = [c for c in chars if c.c.strip()]
    cs = [c for c in cs_all if not _is_ignorable(c.c)]
    if not cs:
        return "", 1.0, [], False
    sizes = [c.size for c in cs]
    dom = base_size or _weighted_median(sizes)
    # 行聚类
    rows: List[List[Char]] = []
    for c in sorted(cs, key=lambda c: c.by):
        if rows and abs(c.by - _weighted_median([x.by for x in rows[-1]])) <= 0.55 * dom:
            rows[-1].append(c)
        else:
            rows.append([c])
    if len(rows) <= 1:
        lx, cf, nt, um = run_to_latex(cs, rules, dom)
        return lx, cf, nt, False

    notes: List[str] = []
    # ---- 结构证据 1：大定界符（矩阵 / 分段函数）----
    # 注意：可伸展定界符碎片（私有区字形）已在 cs 里被过滤，必须用 cs_all 才能识别矩阵
    win = _delimiter_window(cs_all, dom)
    # ---- 结构证据 2：多行在关系符处对齐 ----
    anchors: List[float] = []
    for r in rows:
        real = [c for c in r if not _is_ignorable(c.c)]
        eq = [c for c in real if c.c in "=\u2264\u2265\u2248\u2261\u2260\u2192\u21d2\u221d"]
        # 只有一个关系符、且该行字符数够多（排除 "i=1" 这类上下限碎片）才算对齐行
        if len(eq) >= 1 and len(real) >= 4:
            anchors.append(eq[0].x0)
    aligned = (len(anchors) >= max(2, len(rows) - 1)
               and (max(anchors) - min(anchors)) <= 1.0 * dom)

    if win is not None:
        kind, xl, xr, dchar = win
        # 定界符本身不参与矩阵/分段的单元格内容
        delim_ids = {id(c) for c in cs_all if _is_pua(c.c) or c.c == dchar}
        body_rows: List[List[Char]] = []
        for r in rows:
            inner = [c for c in r if (c.x0 >= xl - 0.5 and (xr is None or c.x1 <= xr + 0.5))
                     and id(c) not in delim_ids]
            if inner:
                body_rows.append(inner)
        if len(body_rows) >= 2:
            lines = []
            for r in body_rows:
                cols = _split_columns(r)
                lines.append(" & ".join(run_to_latex(col, rules, dom)[0] for col in cols))
            body = " \\\\\n".join(lines)
            if kind == "cases":
                env = "cases"
                out = "\\begin{cases}\n%s\n\\end{cases}" % body
            else:
                env = kind
                out = "\\begin{%s}\n%s\n\\end{%s}" % (env, body, env)
            # 定界符左侧的前缀（如 "M ="）与右侧的后缀（如 ", α,β∈R."）
            prefix = [c for c in cs if c.x1 <= xl + 0.5 and id(c) not in delim_ids]
            suffix = [c for c in cs if xr is not None and c.x0 >= xr - 0.5
                      and id(c) not in delim_ids]
            pre = run_to_latex(prefix, rules, dom)[0] if prefix else ""
            suf = run_to_latex(suffix, rules, dom)[0] if suffix else ""
            head = (pre + " ") if pre else ""
            tail = (" " + suf) if suf else ""
            conf = 0.6 if kind == "cases" else 0.65
            return (head + out + tail, conf,
                    notes + [f"delimiter-window={kind} guess:必须人工复核"], True)
    if aligned:
        lines = []
        for r in rows:
            eq = [c for c in r if c.c in "=\u2264\u2265\u2248\u2261\u2260\u2192\u21d2\u221d"]
            if eq:
                anchor = eq[0]
                left = [c for c in r if c.x1 <= anchor.x0 + 1]
                right = [c for c in r if c.x0 >= anchor.x1 - 1]
                ll = run_to_latex(left, rules, dom)[0]
                rl = run_to_latex(right, rules, dom)[0]
                lines.append("%s &%s %s" % (ll, anchor.c, rl))
            else:
                lines.append("& " + run_to_latex(r, rules, dom)[0])
        body = " \\\\\n".join(lines)
        return ("\\begin{aligned}\n%s\n\\end{aligned}" % body, 0.7,
                notes + ["aligned-guess:必须人工复核"], True)

    # ---- 无结构证据：展平（保持 original 顺序 = 按行、行内按 x）----
    lx, cf, nt, um = run_to_latex(cs, rules, dom)
    return lx, cf, nt + ["flattened:未见矩阵/对齐结构，按单行还原"], False


def _is_pua(ch: str) -> bool:
    return bool(ch) and 0xE000 <= ord(ch[0]) <= 0xF8FF


def _is_ignorable(ch: str) -> bool:
    """空白/控制/私有区之外的不可见字符：直接忽略，不算还原失败。"""
    if not ch:
        return True
    o = ord(ch[0])
    if ch.isspace():
        return True
    if o in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD):
        return True
    if 0xE000 <= o <= 0xF8FF:            # CMEX 等可伸展定界符碎片
        return True
    return False


def _tail_after(cs: Sequence[Char], xr: Optional[float]) -> List[Char]:
    if xr is None:
        return []
    return [c for c in cs if c.x0 >= xr - 0.5]


def _delimiter_window(cs: Sequence[Char], dom: float
                      ) -> Optional[Tuple[str, float, Optional[float], str]]:
    """识别"跨行大定界符"，返回 (环境名, 左界, 右界或 None, 定界符字符)。"""
    cands: List[Char] = []
    for c in cs:
        if _is_pua(c.c):
            cands.append(c)
            continue
        if c.c not in OPEN_DELIM and c.c not in CLOSE_DELIM:
            continue
        # CMEX 等"可伸展定界符字体"（括号被拆成上中下几片，单片不高但确实是矩阵/分段的括号）
        if "CMEX" in (c.font or "").upper() or c.h >= 1.45 * dom:
            cands.append(c)
    if len(cands) < 1:
        return None
    xs = [c.x0 for c in cands]
    x_left, x_right = min(xs), max(c.x1 for c in cands)
    span = max(1.0, x_right - x_left)
    left = [c for c in cands if c.x0 <= x_left + 0.25 * span]
    right = [c for c in cands if c.x1 >= x_right - 0.25 * span]
    real = [c.c for c in cands if not _is_pua(c.c)]
    dchar = _most_common(real) or ""
    if len(cands) >= 2 and right and right is not left:
        kind = {"(": "pmatrix", "[": "bmatrix", "|": "vmatrix",
                "{": "Bmatrix"}.get(dchar, "pmatrix")
        return kind, x_left, x_right, dchar
    # 只有左侧大定界符 -> 分段函数（cases）
    if left and (dchar in ("", "{", "(")):
        return "cases", x_left, None, dchar or "{"
    return None


def _is_mathy(s: str) -> bool:
    return bool(re.search(r"[\\^_{}]", s))


def _looks_like_cases(rows: Sequence[Sequence[Char]]) -> bool:
    """右侧为文字条件、左侧为表达式，且行数>=2 -> 分段函数。"""
    if len(rows) < 2:
        return False
    with_gap = 0
    for r in rows:
        if _split_two_columns(r):
            with_gap += 1
    return with_gap >= max(1, len(rows) - 1)


def _split_two_columns(row: Sequence[Char], min_gap_ratio: float = 1.6) -> Optional[Tuple[List[Char], List[Char]]]:
    """把一行按大的水平间隙切成左右两段（用于 cases / 双列表）。"""
    cs = sorted(row, key=lambda c: c.x0)
    if len(cs) < 3:
        return None
    med = _weighted_median([c.size for c in cs]) or 10.0
    gaps: List[Tuple[float, int]] = []
    for i in range(1, len(cs)):
        g = cs[i].x0 - cs[i - 1].x1
        gaps.append((g, i))
    if not gaps:
        return None
    g, idx = max(gaps)
    if g < min_gap_ratio * 0.35 * med or idx < 1 or idx > len(cs) - 1:
        return None
    return cs[:idx], cs[idx:]


def _split_columns(row: Sequence[Char], min_gap: Optional[float] = None) -> List[List[Char]]:
    """按水平间隙把一行切成多列（矩阵用）。"""
    cs = sorted(row, key=lambda c: c.x0)
    if not cs:
        return []
    med = _weighted_median([c.size for c in cs]) or 10.0
    thr = min_gap if min_gap is not None else 0.55 * med
    cols: List[List[Char]] = [[cs[0]]]
    for c in cs[1:]:
        if c.x0 - cols[-1][-1].x1 > thr:
            cols.append([c])
        else:
            cols[-1].append(c)
    return cols


# --------------------------------------------------------------------------- #
# 题注与图表定位
# --------------------------------------------------------------------------- #

def find_captions(page: PageData) -> List[Tuple[str, str, Block]]:
    """返回 [(kind, number, block)]，kind ∈ {figure, table}。"""
    out: List[Tuple[str, str, Block]] = []
    for b in page.blocks:
        if b.role in ("header", "footer", "empty"):
            continue
        t = b.lines[0].text().strip() if b.lines else ""
        if not t:
            continue
        words = len(b.text().split())
        if words > 90:                      # 题注不会这么长，判为正文
            continue
        m = FIG_CAP_RE.match(t)
        if m:
            out.append(("figure", m.group("num"), b))
            continue
        m = TAB_CAP_RE.match(t)
        if m:
            out.append(("table", m.group("num"), b))
    return out


def caption_text(b: Block, drop_number: bool = False) -> str:
    t = " ".join(ln.text().strip() for ln in b.lines if ln.text().strip())
    if drop_number:
        t = FIG_CAP_RE.sub("", t, count=1)
        t = TAB_CAP_RE.sub("", t, count=1)
    return re.sub(r"\s+", " ", t).strip()


def column_bounds(page: PageData, col: int) -> Tuple[float, float]:
    """返回某一栏的水平范围。"""
    if page.split_x is None or col == -1:
        return (page.rect[0], page.rect[2])
    if col == 0:
        return (page.rect[0], page.split_x)
    return (page.split_x, page.rect[2])


def body_text_blocks(page: PageData) -> List[Block]:
    return [b for b in page.blocks if b.role in ("body", "list")]


# --------------------------------------------------------------------------- #
# 表格重建（针对只有横线的三线表：PyMuPDF 的 find_tables 会失败，这里用几何法）
# --------------------------------------------------------------------------- #

def _gap_threshold(gaps: Sequence[float]) -> Optional[float]:
    """在"词间空格"与"列间间隙"两种分布之间自动找分界值。"""
    gs = sorted(g for g in gaps if g > 0.8)
    if len(gs) < 6:
        return None
    best: Optional[Tuple[float, int]] = None
    for i in range(1, len(gs)):
        jump = gs[i] - gs[i - 1]
        if best is None or jump > best[0]:
            best = (jump, i)
    if best is None:
        return None
    jump, i = best
    if jump < 1.2 or gs[i] < 2.4:
        return None
    return (gs[i] + gs[i - 1]) / 2.0


def _split_cells(ln: Line, thr: float) -> List[Tuple[Tuple[float, float, float, float], str]]:
    cs = sorted([c for c in ln.chars if c.c.strip()], key=lambda c: c.x0)
    if not cs:
        return []
    groups: List[List[Char]] = [[cs[0]]]
    for c in cs[1:]:
        if c.x0 - groups[-1][-1].x1 > thr:
            groups.append([c])
        else:
            groups[-1].append(c)
    out = []
    for g in groups:
        bb = (min(c.x0 for c in g), min(c.y0 for c in g),
              max(c.x1 for c in g), max(c.y1 for c in g))
        out.append((bb, chars_to_text(g)))
    return [o for o in out if o[1]]


def table_from_text(page: PageData, region: Sequence[float],
                    min_rows: int = 2, min_cols: int = 2
                    ) -> Optional[Tuple[List[List[str]], List[str], int,
                                        Tuple[float, float, float, float]]]:
    """从文本几何重建表格。

    返回 (rows, notes, header_rows, bbox)；无法可靠重建时返回 None。
    bbox 是"实际用到的那些行"的并集——即使重建质量不够，也可以拿它作为截图范围，
    避免截图兜底退化成"整页"（那是实测出过的严重错误）。
    适用于只有 \\toprule/\\midrule/\\bottomrule 的三线表（期刊最常见）。
    """
    notes: List[str] = []
    lines: List[Line] = []
    for b in page.blocks:
        if b.role in ("header", "footer", "empty"):
            continue
        for ln in b.lines:
            if not ln.text().strip():
                continue
            inter = rect_inter(ln.bbox, region)
            if inter and rect_area(inter) > 0.75 * rect_area(ln.bbox):
                lines.append(ln)
    if len(lines) < min_rows:
        return None
    # 行聚类（按基线）
    lines.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    row_groups: List[List[Line]] = []
    for ln in lines:
        base = _weighted_median([c.by for c in ln.chars] or [ln.bbox[3]])
        placed = False
        if row_groups:
            prev = row_groups[-1]
            prev_base = _weighted_median([c.by for l2 in prev for c in l2.chars] or [0])
            if abs(base - prev_base) <= 0.6 * max(ln.size, 1.0):
                prev.append(ln)
                placed = True
        if not placed:
            row_groups.append([ln])
    if len(row_groups) < min_rows:
        return None
    # 自动定列间隔阈值：先收集所有"较大间隙"
    gaps: List[float] = []
    for rg in row_groups:
        for ln in rg:
            cs = sorted([c for c in ln.chars if c.c.strip()], key=lambda c: c.x0)
            for i in range(1, len(cs)):
                gaps.append(cs[i].x0 - cs[i - 1].x1)
    med_size = _weighted_median([ln.size for ln in lines]) or 10.0
    thr = _gap_threshold(gaps) or (0.55 * med_size)
    notes.append(f"cell-gap-threshold={thr:.1f}pt")

    rows_cells: List[List[Tuple[Tuple[float, float, float, float], str]]] = []
    for rg in row_groups:
        rg = sorted(rg, key=lambda ln: ln.bbox[0])
        cells: List[Tuple[Tuple[float, float, float, float], str]] = []
        for ln in rg:
            parts = _split_cells(ln, thr)
            if cells and parts:
                # 同一行内相邻文本块（少见）合并到最后一格
                if parts[0][0][0] - cells[-1][0][2] <= thr:
                    bb0, t0 = cells[-1]
                    bb1, t1 = parts[0]
                    cells[-1] = (rect_union([bb0, bb1]), (t0 + " " + t1).strip())
                    parts = parts[1:]
            cells.extend(parts)
        if cells:
            rows_cells.append(cells)
    if len(rows_cells) < min_rows:
        return None
    n_max = max(len(r) for r in rows_cells)
    if n_max < min_cols:
        return None

    # 列边界：
    #   首选"表头行"的单元格位置作为列定义（表格的表头通常就定义了列），
    #   否则用"没有任何单元格覆盖的竖直自由带"切列。
    bounds: Optional[List[float]] = None
    header_first = sorted(rows_cells[0], key=lambda c: c[0][0])
    if len(header_first) >= min_cols:
        counts = [len(r) for r in rows_cells]
        agree = sum(1 for n in counts[1:] if abs(n - len(header_first)) <= 1)
        if agree >= 0.6 * max(1, len(counts) - 1):
            mids = []
            for (bb, _txt) in header_first:
                mids.append((bb[0] + bb[2]) / 2.0)
            cuts = [(mids[i] + mids[i + 1]) / 2.0 for i in range(len(mids) - 1)]
            x_lo = min(c[0][0] for r in rows_cells for c in r)
            x_hi = max(c[0][2] for r in rows_cells for c in r)
            bounds = [x_lo - 1.0] + cuts + [x_hi + 1.0]
            notes.append(f"columns=from-header({len(mids)})")
    if bounds is None:
        tol = 0.15 * med_size
        forbidden = [(c[0][0] - tol, c[0][2] + tol) for r in rows_cells for c in r]
        x0 = min(c[0][0] for r in rows_cells for c in r)
        x1 = max(c[0][2] for r in rows_cells for c in r)
        steps = 600
        free_runs: List[Tuple[float, float]] = []
        run_start = None
        for k in range(steps + 1):
            x = x0 + (x1 - x0) * k / steps
            covered = any(f0 <= x <= f1 for (f0, f1) in forbidden)
            if not covered and run_start is None:
                run_start = x
            elif covered and run_start is not None:
                free_runs.append((run_start, x))
                run_start = None
        if run_start is not None:
            free_runs.append((run_start, x1))
        seps = [((a + b) / 2.0) for (a, b) in free_runs
                if a > x0 + 1e-6 and b < x1 - 1e-6 and (b - a) >= 0.55 * med_size]
        bounds = [x0 - 0.1] + sorted(seps) + [x1 + 0.1]
        notes.append(f"columns=free-band({len(bounds) - 1})")
    n_cols = len(bounds) - 1
    if n_cols < min_cols:
        return None
    rows: List[List[str]] = []
    for r in rows_cells:
        cells = [""] * n_cols
        for (bb, txt) in r:
            center = (bb[0] + bb[2]) / 2.0
            idx = 0
            for j in range(n_cols):
                if bounds[j] <= center < bounds[j + 1]:
                    idx = j
                    break
            else:
                idx = n_cols - 1
            while cells[idx]:                      # 同列多段则退到下一列，避免丢内容
                idx += 1
                if idx >= n_cols:
                    idx = n_cols - 1
                    cells[idx] = (cells[idx] + " " + txt).strip()
                    break
            if not cells[idx]:
                cells[idx] = txt
        rows.append(cells)
    # 表头行数：用区域内的横线判断（\\midrule 之后是数据）
    rules = sorted([r for r in page.rules if rect_inter(r, rect_expand(region, 4))],
                   key=lambda r: r[1])
    header_rows = 1
    if len(rules) >= 3:
        mid_y = (rules[1][1] + rules[1][3]) / 2.0
        header_rows = max(1, sum(1 for rg in row_groups[:len(rows)]
                                 if _weighted_median([c.by for ln in rg for c in ln.chars] or [0]) < mid_y))
        notes.append(f"rules={len(rules)} -> 表头 {header_rows} 行")
    if n_cols >= 2 and not any(all(not c for c in r) for r in rows):
        used_bbox = rect_union([ln.bbox for rg in row_groups for ln in rg]) or tuple(region)
        return rows, notes, min(header_rows, max(1, len(rows) - 1)), used_bbox  # type: ignore
    return None

