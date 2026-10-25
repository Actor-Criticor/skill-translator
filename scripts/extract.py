# -*- coding: utf-8 -*-
"""extract.py —— 把英文期刊 PDF 拆成"可翻译的文本 + 可复用的图/表/公式素材"。

产物（默认输出到 <work>/extracted/）：
    doc.json                 结构化全文（章节/块/角色/置信度/告警）
    manifest.json            统计与告警汇总
    source/source.md         供翻译的源文（含 ⟦EQ01⟧/⟦TABLE01⟧ 占位符与行内 $公式$）
    source/sections.json     章节树（供逐节翻译与漏译核对）
    figures/fig1.png ...     按原文顺序命名的图片（fig1~n）
    figures/figures-manifest.md
    tables/table01.tex|png   表格（优先 LaTeX 三线表，失败则截图）
    tables/tables.json
    equations/eq01_orig.png  每条行间公式的原图（用于人工比对）
    equations/draft/eq01.tex 自动还原的 LaTeX 草稿（人工修订对象）
    equations/equations.md   公式清单：原编号/原文本/草稿/置信度/疑点

用法：
    python extract.py source.pdf --out translator-work
    python extract.py source.pdf --out work --pages 1-3 --dpi 400
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import p2zconsole  # noqa: E402,F401  控制台输出编码
from p2zpaths import abs_path  # noqa: E402  绝对路径（勿用 Path.resolve）
import fitz  # noqa: E402

import p2zlib as L  # noqa: E402
from p2zlib import (  # noqa: E402
    Block, Char, Document, EquationInfo, FigureInfo, PageData, TableInfo,
    caption_text, order_blocks, rect_area, rect_inter, rect_iou, rect_union,
)

EQ_TOKEN = "\u27e6EQ{}\u27e7"          # ⟦EQ01⟧
TAB_TOKEN = "\u27e6TABLE{}\u27e7"      # ⟦TABLE01⟧
FIG_TOKEN = "\u27e6FIG{}\u27e7"        # ⟦FIG01⟧

LATEX_SPECIALS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def crop_png(page: "fitz.Page", rect: Sequence[float], dpi: float, out_path: Path,
             min_px: int = 40) -> Tuple[int, int]:
    """按给定矩形（PDF 点，未旋转坐标）裁剪渲染为 PNG，返回像素尺寸。

    实现要点（都是踩过的坑）：
    * 用 `matrix=` 缩放，**不要**把 dpi 传给 `get_pixmap`：PyMuPDF 1.26 在带 clip 时
      会把 float dpi 传到底层 int setter，直接抛 TypeError；而 argparse 传进来的 dpi 是 float。
    * 任何一路失败都要保留错误信息并最终**抛出**，绝不"静默降级"到另一个分辨率
      （曾出现"无论传多少 dpi 都输出 600dpi"以及 dpi=600 时整次提取崩溃）。
    * 旋转页面按长宽比自动选择是否用 derotation 矩阵。
    """
    r = fitz.Rect(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    r = r & page.rect
    if r.is_empty:
        raise ValueError(f"裁剪矩形无效：{rect}（页面 {page.rect}）")
    scale = max(1.0, float(dpi)) / 72.0
    variants = [r]
    if page.rotation:
        try:
            variants.append(r * page.derotation_matrix)
        except Exception:                                   # pragma: no cover
            pass
    want = max(1e-6, r.width / max(1e-6, r.height))
    best: Optional[Tuple[float, Any]] = None
    errors: List[str] = []
    for cand in variants:
        for use_matrix in (True, False):
            try:
                if use_matrix:
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=cand, alpha=False)
                else:
                    pix = page.get_pixmap(clip=cand, dpi=int(round(float(dpi))), alpha=False)
            except Exception as e:                          # pragma: no cover
                errors.append(f"{type(e).__name__}: {e}")
                continue
            if pix.width < min_px or pix.height < min_px:
                errors.append(f"渲染结果过小：{pix.width}x{pix.height}")
                continue
            score = abs(pix.width / max(1, pix.height) - want)
            if best is None or score < best[0]:
                best = (score, pix)
        if best is not None:
            break
    if best is None:
        raise RuntimeError("裁剪渲染失败：" + "；".join(dict.fromkeys(errors))[:300])
    pix = best[1]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    return pix.width, pix.height


def escape_latex_text(s: str) -> str:
    out = []
    for ch in s:
        out.append(LATEX_SPECIALS.get(ch, ch))
    return "".join(out)


def cell_to_latex(s: str) -> str:
    """表格单元格：转义特殊字符，并把 Unicode 上下标转成数学模式。"""
    if s is None:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    s = s.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    # 10⁻³ 之类 -> $10^{-3}$
    def repl(m: "re.Match[str]") -> str:
        base, sup = m.group(1), m.group(2)
        conv = "".join(L.SUP_MAP.get(c, c) for c in sup)
        return "$%s^{%s}$" % (base, conv)
    s = re.sub(r"([0-9A-Za-z\)\]])([\u2070\u00b9\u00b2\u00b3\u2074-\u2079\u207a\u207b\u207f]+)", repl, s)
    def replsub(m: "re.Match[str]") -> str:
        base, sub = m.group(1), m.group(2)
        conv = "".join(L.SUB_MAP.get(c, c) for c in sub)
        return "$%s_{%s}$" % (base, conv)
    s = re.sub(r"([0-9A-Za-z\)\]])([\u2080-\u208e\u2090-\u209c]+)", replsub, s)
    return escape_latex_text(s)


def join_block_lines(b: Block, dehyph_log: List[Dict[str, Any]]) -> str:
    """把块内的多行合并成一段：处理行末连字符与中英混排空格。"""
    lines = [ln.text().strip() for ln in b.lines if ln.text().strip()]
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        if not nxt:
            continue
        m = L.HYPHEN_END_RE.search(out)
        if m:
            if nxt[:1].islower() and len(nxt) > 1:
                # 可能是真连字符复合词（state-of-the-art）也可能是断词
                word = re.search(r"([A-Za-z]+)-$", out)
                head = word.group(1) if word else ""
                dehyph_log.append({"head": head, "tail": nxt[:12], "action": "joined"})
                out = out[:m.start() + 1] + nxt
                continue
        # 行末无标点的普通折行
        if out.endswith("-"):
            out = out[:-1] + nxt
        else:
            out = out + " " + nxt
    return re.sub(r"\s+", " ", out).strip()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1) 图片提取（fig1~n）
# --------------------------------------------------------------------------- #

def _inner_short_text_blocks(page: PageData, region: Sequence[float],
                             exclude: Sequence[Block]) -> List[Block]:
    """区域内的短文本块（坐标轴标签、图例、面板编号），需并入图片。"""
    out: List[Block] = []
    for b in page.blocks:
        if b in exclude or b.role in ("header", "footer", "empty"):
            continue
        if not rect_inter(b.bbox, region):
            continue
        txt = b.text().strip()
        if len(txt.split()) > 14:
            continue
        if b.w > 0.65 * (region[2] - region[0]) and len(txt.split()) > 8:
            continue
        out.append(b)
    return out


def _figure_internal_ids(page: PageData, clusters: Sequence[Sequence[float]]) -> set:
    """找出"属于图片内部的文字块"（坐标轴标签、图例、面板编号）。

    为什么必须专门处理：这些短文本块会被版面分析当成 `body`，
    如果拿它们当"图上方最近的正文块"，就会把候选带压到只有几 pt 高，
    进而导致图定位失败或抓到隔壁那张图（实测踩过的坑）。
    """
    ids: set = set()
    for b in page.blocks:
        if b.role not in ("body", "list"):
            continue
        txt = b.text().strip()
        n_words = len(txt.split())
        if not txt or n_words > 12:
            continue
        for c in clusters:
            # 与图形簇相交，或紧邻图形簇（横向 22pt / 纵向 25pt 内）且是短文本
            if rect_inter(b.bbox, c):
                ids.add(id(b))
                break
            near = (max(c[0] - b.bbox[2], b.bbox[0] - c[2]) <= 22 and
                    max(c[1] - b.bbox[3], b.bbox[1] - c[3]) <= 25)
            if near:
                ids.add(id(b))
                break
    return ids


def locate_figure(page: PageData, cap: Block, number: str,
                  used: Sequence[Sequence[float]] = ()) -> FigureInfo:
    """以题注为锚点定位图片，返回 FigureInfo（bbox 已收紧）。

    used：同页前面几张图已经占用的图形簇，避免两张图抓到同一片图形。
    """
    fi = FigureInfo(index=0, number=number, page=page.index, bbox=(0, 0, 0, 0),
                    caption=caption_text(cap), caption_bbox=cap.bbox)
    col = cap.col
    cx0, cx1 = L.column_bounds(page, col)

    # 图形候选：矢量路径（含横线）+ 栅格图片
    shapes: List[Tuple[float, float, float, float]] = []
    for g in page.graphics:
        if rect_area(g.bbox) < 9:               # 忽略极小装饰（项目符号、下划线）
            continue
        shapes.append(g.bbox)
    for im in page.images:
        if rect_area(im.bbox) < 100:
            continue
        shapes.append(im.bbox)
    all_clusters = L.cluster_rects(shapes, gap=10.0)
    # 同页其它图已占用 / 与其它题注同属一片的簇，不再重复使用
    clusters = [c for c in all_clusters
                if not any(rect_iou(c, u) > 0.5 for u in used)]

    inner_ids = _figure_internal_ids(page, all_clusters)

    # 其它题注（含表题）形成的"边界"：越过它就不是本图的范围
    other_caps = [cb.bbox for (kind, num, cb) in L.find_captions(page)
                  if cb is not cap and num != number]
    body = [b for b in page.blocks if b.role in ("body", "list", "heading", "ref")
            and not rect_inter(b.bbox, cap.bbox) and id(b) not in inner_ids]

    def band_above() -> Tuple[float, float]:
        top = page.rect[1]
        for b in body:
            if b.y1 <= cap.y0 + 1 and (b.col == col or b.col == -1):
                top = max(top, b.y1)
        for cb in other_caps:                   # 上方若有别的题注，范围从其下沿起算
            if cb[1] < cap.y0 and (cb[0] < cx1 and cb[2] > cx0):
                top = max(top, cb[3])
        return top, cap.y0

    def band_below() -> Tuple[float, float]:
        bot = page.rect[3]
        for b in body:
            if b.y0 >= cap.y1 - 1 and (b.col == col or b.col == -1):
                bot = min(bot, b.y0)
        for cb in other_caps:                   # 下方若有别的题注，范围到其顶沿为止
            if cb[3] > cap.y1 and (cb[0] < cx1 and cb[2] > cx0):
                bot = min(bot, cb[1])
        return cap.y1, bot

    chosen: List[Tuple[float, float, float, float]] = []
    region: Optional[Tuple[float, float, float, float]] = None
    for label, (t, bm) in (("above", band_above()), ("below", band_below())):
        if bm - t < 10:
            continue
        probe = (cx0 - 6, t, cx1 + 6, bm)
        hit = [c for c in clusters if rect_inter(c, probe) and
               rect_area(rect_inter(c, probe)) > 0.25 * rect_area(c)]
        if hit:
            # 取离题注最近的簇（避免把同带内的多幅图一网打尽）
            hit.sort(key=lambda c: min(abs(c[3] - cap.y0), abs(bm - c[3])))
            chosen, region = hit[:1], probe
            fi.notes.append(f"region={label}")
            break
    if region is None:
        # 兜底 1：整页找离题注最近的大图形簇
        cands = [c for c in clusters if rect_area(c) > 0.02 * (page.width * page.height)]
        if cands:
            cands.sort(key=lambda c: min(abs(c[1] - cap.y0), abs(c[3] - cap.y1)))
            chosen = [cands[0]]
            region = (cx0 - 6, page.rect[1], cx1 + 6,
                      cap.y0 if cands[0][1] < cap.y0 else page.rect[3])
            fi.notes.append("fallback=nearest-cluster")
    if region is None:
        imgs = [im for im in page.images if rect_area(im.bbox) > 400
                and not any(rect_iou(im.bbox, u) > 0.5 for u in used)]
        if imgs:
            imgs.sort(key=lambda im: abs(im.bbox[3] - cap.y0))
            chosen = [imgs[0].bbox]
            region = imgs[0].bbox
            fi.kind = "fallback-raster"
            fi.notes.append("fallback=nearest-image")
    if region is None:
        fi.kind = "missing"
        fi.notes.append("未找到图形元素：需人工截图")
        fi.bbox = cap.bbox
        return fi

    # 跨栏判定
    full_width = any(c[0] < page.split_x - 8 and c[2] > page.split_x + 8
                     for c in chosen) if page.split_x else False
    if full_width:
        region = (page.rect[0], region[1], page.rect[2], region[3])
        fi.notes.append("full-width")

    # 并入区域内的短文本（轴标签/图例）
    texts = _inner_short_text_blocks(page, region, exclude=[cap])
    fi.has_text_inside = bool(texts)
    boxes = list(chosen) + [b.bbox for b in texts]
    tight = rect_union(boxes)
    if tight is None:
        tight = region
    pad = 5.0
    tight = (max(page.rect[0], tight[0] - pad), max(page.rect[1], tight[1] - pad),
             min(page.rect[2], tight[2] + pad), min(page.rect[3], tight[3] + pad))
    fi.bbox = tuple(tight)                                   # type: ignore
    fi.panels = max(1, len([c for c in chosen if rect_area(c) > 0.02 * page.width * page.height]))

    n_img = sum(1 for im in page.images if rect_inter(im.bbox, fi.bbox))
    n_path = sum(1 for g in page.graphics if rect_area(g.bbox) >= 9 and rect_inter(g.bbox, fi.bbox))
    if n_img and n_path:
        fi.kind = "mixed"
    elif n_img:
        fi.kind = "raster"
    else:
        fi.kind = "vector"
    return fi


def extract_figures(d: Document, out_dir: Path, dpi: float) -> List[FigureInfo]:
    figs: List[FigureInfo] = []
    doc = fitz.open(d.path)
    used_by_page: Dict[int, List[Tuple[float, float, float, float]]] = {}
    for p in d.pages:
        for (kind, num, cap) in L.find_captions(p):
            if kind != "figure":
                continue
            used = used_by_page.setdefault(p.index, [])
            fi = locate_figure(p, cap, num, used=used)
            # 安全网：同页若与本页已定位的图几乎重合，标记出来（宁可让人看一眼）
            for prev in figs:
                if prev.page == fi.page and rect_iou(prev.bbox, fi.bbox) > 0.8:
                    fi.notes.append(f"与 fig{prev.index} 区域重合(IoU>0.8)：疑似定位到同一幅图，必须人工核对")
            fi.index = len(figs) + 1
            if fi.kind != "missing":
                used.append(tuple(fi.bbox))
            out_png = out_dir / f"fig{fi.index}.png"
            page = doc[p.index]
            try:
                w, h = crop_png(page, fi.bbox, dpi, out_png)
                fi.notes.append(f"px={w}x{h} dpi={dpi}")
            except Exception as e:                            # pragma: no cover
                fi.notes.append(f"crop-failed: {e}")
            figs.append(fi)
    # 内容级安全网：两张图文件字节相同 -> 一定是定位重复，标出来
    import hashlib
    seen: Dict[str, int] = {}
    for fi in figs:
        png = out_dir / f"fig{fi.index}.png"
        if not png.exists():
            continue
        h = hashlib.sha1(png.read_bytes()).hexdigest()
        if h in seen:
            fi.notes.append(f"与 fig{seen[h]} 的图片文件完全相同：定位重复，必须人工重截")
        else:
            seen[h] = fi.index
    doc.close()
    return figs


# --------------------------------------------------------------------------- #
# 2) 表格提取
# --------------------------------------------------------------------------- #

def _table_quality(rows: Sequence[Sequence[Any]],
                   expected_rows: Optional[int] = None) -> float:
    """表格结构解析质量。

    **必须把"行覆盖"算进去**：PyMuPDF 有时只认出 2 行（把 6 行挤成两行），
    而格子都填满、列数也一致 —— 只按填充率/一致性打分就会给出 1.00 的假高分
    （实测踩过：6 行的带线表格被压成 2 行仍报 quality=1.00）。
    """
    if not rows:
        return 0.0
    n_rows = len(rows)
    widths = [len(r) for r in rows]
    n_cols = max(widths) if widths else 0
    if n_cols == 0:
        return 0.0
    filled = sum(1 for r in rows for c in r if (c is not None and str(c).strip()))
    total = n_rows * n_cols
    consistent = sum(1 for w in widths if w == n_cols) / max(1, n_rows)
    fill_score = filled / max(1, total)
    if expected_rows and expected_rows >= 2:
        coverage = min(1.0, n_rows / float(expected_rows))
    else:
        coverage = 1.0
    return 0.5 * fill_score + 0.3 * consistent + 0.2 * coverage


def _expected_rows(page: PageData, bbox: Sequence[float]) -> int:
    """bbox 内"文本行"的条数（按基线聚成行），用于考核表格行覆盖。"""
    bands: List[float] = []
    for b in page.blocks:
        if b.role in ("header", "footer", "empty"):
            continue
        for ln in b.lines:
            if not ln.text().strip():
                continue
            inter = rect_inter(ln.bbox, bbox)
            if not inter or rect_area(inter) < 0.5 * rect_area(ln.bbox):
                continue
            by = L._weighted_median([c.by for c in ln.chars] or [ln.bbox[3]])
            if not any(abs(by - y) <= 0.6 * max(ln.size, 1.0) for y in bands):
                bands.append(by)
    return len(bands)


def _table_region(page: PageData, capb: Block) -> Tuple[Optional[Tuple[float, float, float, float]], str]:
    """定位表格区域：题注默认在表上方，用横线（\\toprule/\\midrule/\\bottomrule）夹出表格。

    返回 (region 或 None, 说明)。三线表没有竖线、没有边框，用横线定位最可靠。
    """
    cx0, cx1 = L.column_bounds(page, capb.col)
    cb = capb.bbox
    rules = []
    for r in page.rules:
        if r[3] < cb[3] - 2:                       # 必须在题注下方
            continue
        if r[1] - cb[3] > 420:                     # 太远，属于别的内容
            continue
        ov = min(r[2], cx1) - max(r[0], cx0)
        if ov <= 0.3 * (r[2] - r[0]) and ov <= 20:
            continue
        rules.append(r)
    if not rules:
        return None, "region=none(无横线)"
    rules.sort(key=lambda r: r[1])
    # 断开间距过大者（避免把两个表格合并）
    group = [rules[0]]
    for r in rules[1:]:
        if r[1] - group[-1][3] > 150:
            break
        group.append(r)
    top = min(r[1] for r in group)
    bottom = max(r[3] for r in group)
    x0 = min(min(r[0] for r in group), cx0 + 4)
    x1 = max(max(r[2] for r in group), cx1 - 4)
    pad = 5.0
    region = (max(page.rect[0], x0 - pad), max(page.rect[1], top - pad),
              min(page.rect[2], x1 + pad), min(page.rect[3], bottom + pad))
    return region, f"region=rules({len(group)} 条横线)"


def _nearest_caption(bbox: Sequence[float],
                     caps: Sequence[Tuple[str, Block]]) -> Tuple[str, Block, float]:
    """找与表格区域最近的题注，返回 (编号, 题注块, 垂直距离)。"""
    best: Optional[Tuple[str, Block, float]] = None
    for (num, capb) in caps:
        cb = capb.bbox
        if cb[3] <= bbox[1]:                       # 题注在表格上方
            dist = bbox[1] - cb[3]
        elif cb[1] >= bbox[3]:                     # 题注在表格下方
            dist = cb[1] - bbox[3]
        else:
            dist = 0.0
        # 水平方向上必须与表格有重叠，否则不是这张表的题注
        if min(cb[2], bbox[2]) - max(cb[0], bbox[0]) <= 0:
            dist += 200
        if best is None or dist < best[2]:
            best = (num, capb, dist)
    if best is None:
        return "", None, 1e9                          # type: ignore
    return best


def _cols_spec(rows: Sequence[Sequence[str]]) -> str:
    """按列内容判对齐：数字列居中，文本列左对齐。"""
    n_cols = max(len(r) for r in rows) if rows else 1
    spec = []
    for c in range(n_cols):
        cells = [str(r[c]) for r in rows[1:] if c < len(r) and str(r[c]).strip()]
        numeric = sum(1 for v in cells if re.fullmatch(r"[-+]?[\d\.,\s]*\d[\d\.,\s]*%?", v.strip()))
        if cells and numeric >= 0.6 * len(cells):
            spec.append("c")
        else:
            spec.append("l")
    return "".join(spec)


def table_to_latex(t: TableInfo) -> str:
    rows = [["" if c is None else str(c) for c in r] for r in t.rows]
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    rows = [r + [""] * (n_cols - len(r)) for r in rows]
    spec = _cols_spec(rows)
    hrows = max(1, min(int(getattr(t, "header_rows", 1) or 1), max(1, len(rows) - 1)))
    body_lines: List[str] = []
    for i, r in enumerate(rows):
        cells = " & ".join(cell_to_latex(c) for c in r)
        if i < hrows:
            cells = " & ".join("\\textbf{%s}" % cell_to_latex(c) if c.strip() else ""
                               for c in r)
        body_lines.append(cells + r" \\")
        if i == hrows - 1:
            body_lines.append(r"\midrule")
    return ("\\begin{tabular}{%s}\n\\toprule\n%s\n\\bottomrule\n\\end{tabular}"
            % (spec, "\n".join(body_lines)))


def extract_tables(d: Document, out_dir: Path, dpi: float,
                   force_image: bool = False) -> List[TableInfo]:
    tables: List[TableInfo] = []
    doc = fitz.open(d.path)
    for p in d.pages:
        caps = [(num, cap) for (kind, num, cap) in L.find_captions(p) if kind == "table"]
        fpage = doc[p.index]                    # fitz 页面对象（注意别与 PageData 混淆）
        found: List[TableInfo] = []
        if not force_image:
            # 只用 lines / lines_strict 两种策略：
            # `text` 策略会把整段正文当成几十行的"伪表格"（实测：22 行 × 4 列的框把整栏都盖住，
            # 还会连带让真正的公式被当作"表格区域内"而漏掉），只在最后不得已时才用，且门槛更严。
            for strategy in ("lines", "lines_strict"):
                try:
                    finder = fpage.find_tables(strategy=strategy)
                except Exception:
                    continue
                for t in getattr(finder, "tables", []) or []:
                    try:
                        rows = t.extract()
                    except Exception:
                        continue
                    rows = [["" if c is None else str(c).strip() for c in r] for r in rows]
                    n_cols = max((len(r) for r in rows), default=0)
                    bb = (float(t.bbox[0]), float(t.bbox[1]),
                          float(t.bbox[2]), float(t.bbox[3]))
                    area_ratio = rect_area(bb) / max(1.0, fpage.rect.width) \
                        / max(1.0, fpage.rect.height)
                    ti = TableInfo(bbox=bb, rows=rows, n_rows=len(rows), n_cols=n_cols,
                                   strategy=strategy,
                                   quality=_table_quality(rows, _expected_rows(p, bb)))
                    exp = _expected_rows(p, bb)
                    if exp and len(rows) < exp:
                        ti.notes.append(f"行数可疑：识别 {len(rows)} 行，区域内文本行 {exp} 行")
                    if (ti.n_rows >= 2 and ti.n_cols >= 2 and ti.quality >= 0.6
                            and area_ratio <= 0.45 and ti.n_rows <= 60):
                        found.append(ti)
            # 去重：同一区域只留质量最高的
            found.sort(key=lambda t: -t.quality)
            uniq: List[TableInfo] = []
            for ti in found:
                if any(rect_iou(ti.bbox, u.bbox) > 0.5 for u in uniq):
                    continue
                uniq.append(ti)
            found = uniq
        # 与题注配对：有题注时只保留"确实挨着题注"的表格（其余判为误检）
        pairs: List[Tuple[TableInfo, str, Block, float]] = []
        for ti in found:
            if caps:
                num, capb, dist = _nearest_caption(ti.bbox, caps)
                if dist <= 90:
                    pairs.append((ti, num, capb, dist))
                else:
                    ti.notes.append(f"丢弃：离最近表题 {dist:.0f}pt，判为误检")
            else:
                pairs.append((ti, "", None, -1.0))       # type: ignore
        for (ti, num, capb, dist) in pairs:
            ti.page = p.index
            if capb is not None:
                ti.number = num
                ti.caption = caption_text(capb)
                ti.caption_bbox = capb.bbox
            ti.notes.append(f"strategy={ti.strategy} quality={ti.quality:.2f}"
                            + (f" caption-dist={dist:.0f}pt" if dist >= 0 else ""))
            tables.append(ti)
        # 每个题注：先算"文本几何重建"结果，再决定用哪一种
        for (num, capb) in caps:
            region, rule_note = _table_region(p, capb)
            probe = TableInfo(bbox=capb.bbox, caption_bbox=capb.bbox, page=p.index)
            text_region = _shrink_rect_to_content(fpage, probe, p, fpage.rect)
            # 两个候选区域都试一遍几何重建，取更可信的结果：
            # * 横线区域对三线表最准，但会被下方公式的分式横线"污染"而失败；
            # * 文本行区域按"带水平间隙的短行"定位，对无边框表/只有上下线的表更稳。
            built = None
            built_bbox: Optional[Tuple[float, float, float, float]] = None
            best_note = rule_note
            best_score = -1.0
            if not force_image:
                for rg, note in ((region, rule_note), (text_region, "region=text-rows(按文本行定位)")):
                    if rg is None:
                        continue
                    res = L.table_from_text(p, rg)
                    if res is None:
                        continue
                    r_rows, r_notes, r_hdr, r_bb = res
                    q = _table_quality(r_rows, len(r_rows))
                    if len(r_rows) < 2 or max(len(r) for r in r_rows) < 2:
                        continue
                    score = q - 0.05 * (1.0 if note == rule_note else 0.0)
                    if score > best_score:
                        best_score = score
                        built = (r_rows, r_notes + [f"region={note}"], r_hdr, r_bb)
                        built_bbox = r_bb
                        best_note = note
            rule_note = best_note
            geo: Optional[TableInfo] = None
            built_bbox: Optional[Tuple[float, float, float, float]] = None
            if built is not None:
                rows, notes, header_rows, built_bbox = built
                cand = TableInfo(bbox=built_bbox or region, rows=rows, n_rows=len(rows),
                                 n_cols=max(len(r) for r in rows), number=num,
                                 caption=caption_text(capb), caption_bbox=capb.bbox,
                                 strategy="text-geometry", quality=_table_quality(rows, len(rows)),
                                 header_rows=header_rows, page=p.index,
                                 notes=[rule_note] + notes)
                if cand.quality >= 0.6 and cand.n_rows >= 2 and cand.n_cols >= 2:
                    cand.notes.append(f"几何重建成功 quality={cand.quality:.2f} "
                                      f"rows={cand.n_rows} cols={cand.n_cols}")
                    geo = cand
                else:
                    cand.notes.append(f"几何重建质量不足（{cand.quality:.2f}）")
            matched = next((t for t in tables if t.number == num and t.page == p.index), None)
            if geo is not None:
                # 何时改用几何重建：没有 find_tables 结果；或它的质量不高；
                # 或它的框明显比表格区域大得多（把周围正文都包进来了，属伪表格）
                sprawl = (matched is not None and region is not None
                          and rect_area(matched.bbox) > 2.2 * max(1.0, rect_area(region)))
                suspect_rows = matched is not None and any("行数可疑" in n for n in matched.notes)
                if matched is None or matched.quality < 0.8 or sprawl or suspect_rows:
                    if matched is not None:
                        tables.remove(matched)
                        geo.notes.append(f"取代 find_tables 结果（quality={matched.quality:.2f}"
                                         + ("，其框过大疑似伪表格" if sprawl else "") + "）")
                    tables.append(geo)
                    continue
            if matched is not None:
                continue
            # 都没有 -> 截图上位（截图范围优先用"实际用到的行"的并集，其次横线夹出的区域）
            shot = built_bbox or region or capb.bbox
            ti = TableInfo(bbox=shot, number=num, caption=caption_text(capb),
                           caption_bbox=capb.bbox, strategy="image", page=p.index,
                           notes=[rule_note] + ([f"重建失败"] if built is None else []))
            ti.notes.append("结构解析失败：改为截图（tableNN.png），需人工核对裁剪范围")
            tables.append(ti)
    # 排序编号并落盘
    tables.sort(key=lambda t: (t.caption_bbox or t.bbox)[1])
    for i, ti in enumerate(tables, 1):
        idx = "%02d" % i
        if ti.strategy == "image":
            png = out_dir / f"table{idx}.png"
            # 页面必须按 ti.page 取！所有页的 rect 都一样，用"题注 bbox 落在哪一页"
            # 判断会永远命中第 1 页 —— 实测出现过"表格截图截到第 1 页整页"的严重错误。
            pd_page = next((pp for pp in d.pages if pp.index == ti.page), None)
            if pd_page is None:
                pd_page = next((pp for pp in d.pages
                                if ti.caption_bbox and _bbox_on_page(ti.caption_bbox, pp)), None)
            pg = doc[pd_page.index] if pd_page is not None else doc[0]
            if ti.caption_bbox and tuple(ti.bbox) != tuple(ti.caption_bbox):
                rect = L.rect_expand(ti.bbox, 4.0)      # 已由横线/文本行定位，直接用该区域
            elif any("无横线" in n for n in ti.notes):
                # 找不到横线的无边框表：用带明显水平间隙的文本行收紧范围，避免截到整页
                rect = _shrink_rect_to_content(pg, ti, pd_page, pg.rect)
            else:
                rect = _table_crop_rect(pg, ti.caption_bbox or ti.bbox, pd_page, d)
            # 安全网：截图范围若大得离谱（例如整页），说明定位失败，标记出来而不是照单交付
            area_ratio = rect_area(rect) / max(1.0, (pg.rect.width * pg.rect.height))
            if area_ratio > 0.5:
                ti.notes.append(f"截图范围过大（占页面 {area_ratio:.0%}），已收窄为题注所在栏+邻近文本行；"
                                f"请人工确认或直接手工裁图")
                rect = _shrink_rect_to_content(pg, ti, pd_page, rect)
            crop_png(pg, rect, dpi, png)
            ti.notes.append(f"image={png.name} rect={tuple(round(v) for v in rect)}")
        else:
            tex = out_dir / f"table{idx}.tex"
            tex.parent.mkdir(parents=True, exist_ok=True)
            tex.write_text(table_to_latex(ti) + "\n", encoding="utf-8-sig")
            ti.notes.append(f"latex={tex.name}")
    doc.close()
    return tables


def _shrink_rect_to_content(pg: "fitz.Page", ti: TableInfo,
                            pd_page: Optional[PageData],
                            rect: Tuple[float, float, float, float]
                            ) -> Tuple[float, float, float, float]:
    """把过大的截图范围收窄到"题注附近真正像表格的那几行"。

    做法：取题注上下 300pt 内的"非散文短行"（表格单元格行的典型特征），
    **按垂直间隙聚簇**，选离题注最近的一簇 —— 表格的几行是连续且行距均匀的，
    而远处的公式/正文会形成另一簇，不会被带进来。
    """
    cap = ti.caption_bbox or ti.bbox
    cx0, cx1 = (pg.rect.x0, pg.rect.x1)
    max_h = min(280.0, pg.rect.height / 2.8)
    if pd_page is None:
        return (cx0, max(pg.rect.y0, cap[1] - max_h / 2),
                cx1, min(pg.rect.y1, cap[3] + max_h / 2))
    col = -1
    best = None
    for b in pd_page.blocks:
        iou = rect_iou(b.bbox, cap)
        if best is None or iou > best[0]:
            best = (iou, b)
    if best and best[0] > 0.2:
        col = best[1].col
    cx0, cx1 = L.column_bounds(pd_page, col)

    lines: List[L.Line] = []
    for b in pd_page.blocks:
        if b.role in ("header", "footer", "empty", "heading", "caption"):
            continue
        for ln in b.lines:
            if not ln.text().strip():
                continue
            if rect_inter(ln.bbox, cap):              # 题注自身的那几行不算表格内容
                continue
            if ln.bbox[3] < cap[1] - 300 or ln.bbox[1] > cap[3] + 300:
                continue
            if len(ln.text().split()) > 9:            # 长行是正文，不是表格行
                continue
            if _is_prose_line(ln):                    # 散文（≥3 词且数学占比低）排除
                continue
            inter = rect_inter(ln.bbox, (cx0, cap[1] - 300, cx1, cap[3] + 300))
            if not inter:
                continue
            lines.append(ln)
    if not lines:
        return (cx0, max(pg.rect.y0, cap[1] - max_h / 2),
                cx1, min(pg.rect.y1, cap[3] + max_h / 2))
    lines.sort(key=lambda l: l.bbox[1])
    clusters: List[List[L.Line]] = []
    for ln in lines:
        if clusters and (ln.bbox[1] - clusters[-1][-1].bbox[3]) <= 18:
            clusters[-1].append(ln)
        else:
            clusters.append([ln])
    chosen = None
    for cl in clusters:
        if len(cl) < 2:
            continue
        u = rect_union([l.bbox for l in cl])
        if u is None:
            continue
        if u[1] <= cap[3] and u[3] >= cap[1]:
            dist = 0.0
        else:
            dist = min(abs(u[1] - cap[3]), abs(cap[1] - u[3]))
        if chosen is None or dist < chosen[0]:
            chosen = (dist, u)
    if chosen is None:
        u = rect_union([l.bbox for l in lines])
        if u is None:
            return (cx0, max(pg.rect.y0, cap[1] - max_h / 2),
                    cx1, min(pg.rect.y1, cap[3] + max_h / 2))
    else:
        u = chosen[1]
    if (u[3] - u[1]) > max_h:
        center = (cap[1] + cap[3]) / 2.0
        u = (u[0], max(pg.rect.y0, center - max_h / 2), u[2],
             min(pg.rect.y1, center + max_h / 2))
    return (max(cx0, u[0] - 8), max(pg.rect.y0, u[1] - 8),
            min(cx1, u[2] + 8), min(pg.rect.y1, u[3] + 8))


def _bbox_on_page(bbox: Sequence[float], p: PageData, tol: float = 2.0) -> bool:
    return (p.rect[0] - tol <= bbox[0] and bbox[2] <= p.rect[2] + tol and
            p.rect[1] - tol <= bbox[1] and bbox[3] <= p.rect[3] + tol)


def _table_crop_rect(page: "fitz.Page", cap_bbox: Sequence[float],
                     pd_page: Optional[PageData],
                     d: Document) -> Tuple[float, float, float, float]:
    """表格截图兜底的范围：题注所在栏内，向上/向下扩展到最近的正文块。"""
    x0, x1 = page.rect.x0, page.rect.x1
    top, bottom = page.rect.y0, page.rect.y1
    if pd_page is not None:
        cap_col = -1
        best = None
        for b in pd_page.blocks:
            iou = rect_iou(b.bbox, cap_bbox)
            if best is None or iou > best[0]:
                best = (iou, b)
        if best and best[0] > 0.2:
            cap_col = best[1].col
        x0, x1 = L.column_bounds(pd_page, cap_col)
        for b in pd_page.blocks:
            if b.role not in ("body", "list", "heading"):
                continue
            if rect_inter(b.bbox, cap_bbox):
                continue
            if b.y1 <= cap_bbox[1] and (b.col == cap_col or b.col == -1):
                top = max(top, min(b.y1, cap_bbox[1]))
            if b.y0 >= cap_bbox[3] and (b.col == cap_col or b.col == -1):
                bottom = min(bottom, max(b.y0, cap_bbox[3]))
    pad = 6.0
    return (max(page.rect.x0, x0 - pad), max(page.rect.y0, top - pad),
            min(page.rect.x1, x1 + pad), min(page.rect.y1, bottom + pad))


# --------------------------------------------------------------------------- #
# 3) 行间公式提取
# --------------------------------------------------------------------------- #

EQNUM_RE = re.compile(r"^[\(\[]\s*(\d+[a-zA-Z]?)\s*[\)\]]$")


def _find_eq_number(page: PageData, bbox: Sequence[float]) -> str:
    """在与公式同一水平带、靠右边缘处找 (3) 这样的编号。"""
    for b in page.blocks:
        for ln in b.lines:
            t = ln.text().strip()
            m = EQNUM_RE.match(t)
            if not EQNUM_RE.match(t):
                continue
            ymid = (ln.y0 + ln.y1) / 2.0
            if bbox[1] - 6 <= ymid <= bbox[3] + 6 and ln.x0 > bbox[2] - 6:
                return m.group(1)
    # 有些 PDF 把编号并入同一行
    for b in page.blocks:
        for ln in b.lines:
            t = ln.text().strip()
            m = re.search(r"[\(\[]\s*(\d+[a-zA-Z]?)\s*[\)\]]\s*$", t)
            if m and rect_iou(ln.bbox, bbox) > 0.2:
                return m.group(1)
    return ""


def _mostly_inside(bbox: Sequence[float], region: Sequence[float]) -> bool:
    inter = rect_inter(bbox, region)
    if not inter:
        return False
    return rect_area(inter) > 0.6 * max(1e-6, rect_area(bbox))


def _trailing_eqnum(ln: L.Line) -> bool:
    t = ln.text().strip()
    return bool(re.search(r"[\(\[]\s*\d+[a-zA-Z]?\s*[\)\]]\s*[.,;]?$", t))


def _attach_only(ln: L.Line) -> bool:
    """"只会吸附、不会自成一组"的行：**公式编号行**。

    编号（如右边缘的 "(1)"）在 PDF 里的 y 位置可能落在公式主体中间，
    如果让它参与"按行分组"就会把同一个公式切碎，所以只允许它吸附到最近的组。
    """
    t = ln.text().strip()
    if not t:
        return True
    return bool(re.fullmatch(r"[\(\[]?\s*\d+[a-zA-Z]?\s*[\)\]]?[.,;]?", t))


def _has_math_symbol(ln: L.Line) -> bool:
    """行内是否含"不可能出现在普通正文里"的数学符号（Ω ± ≈ × − ∫ ∑ √ ≤ Greek）。

    为什么需要：期刊里常见 "Times 正文 + CM 数学"（mathptmx 之类）排版，
    公式里的数字与拉丁字母用的是**正文字体**，靠字体名判数学会严重漏判
    （实测导致整行公式内容被丢掉、公式被静默截断）。
    """
    for c in ln.chars:
        if not c.c.strip():
            continue
        if c.c in L.SYMBOLS or c.c in L.SUP_MAP or c.c in L.SUB_MAP:
            return True
    return False


def _display_candidates(d: Document,
                        exclude: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None
                        ) -> List[Tuple[PageData, List[L.Line]]]:
    """找行间公式候选：(页, 参与的行)。

    PDF 里一个公式常被 PyMuPDF 拆成多个块/多行（分式的分子分母、矩阵的行、
    上下限），因此这里按**几何相邻 + 同栏**把数学行重新聚成一个公式，
    并在遇到"行尾公式编号"处切断（编号是一式结束的可靠信号）。
    exclude 用于排除已识别为表格的区域（表格单元格里的数字不是公式）。
    """
    out: List[Tuple[PageData, List[L.Line]]] = []
    exclude = exclude or {}
    for p in d.pages:
        regions = exclude.get(p.index, [])
        pool: List[Tuple[L.Line, int]] = []
        marks: List[Tuple[L.Line, int]] = []
        for b in p.blocks:
            if b.role in ("header", "footer", "empty", "caption", "ref", "ref-heading", "heading"):
                continue
            for ln in b.lines:
                if not ln.text().strip():
                    continue
                if any(_mostly_inside(ln.bbox, r) for r in regions):
                    continue
                pool.append((ln, b.col))
                # 数学行判定：
                #  ① 块被判为数学块；② 数学字形占比够高；③ 含明确数学符号**且本身不是散文**
                #  ③ 的条件不能省：正文句里出现 α、× 太常见，只看符号会把整段正文吸进公式
                #  （实测：`where T is the temperature, α the thermal ...` 被吞进 eq 候选）
                if (b.role == "math" or _line_math_ratio(ln) >= 0.32
                        or (_has_math_symbol(ln) and not _is_prose_line(ln)
                            and len(ln.text().split()) <= 4)):
                    marks.append((ln, b.col))
        marks.sort(key=lambda t: (t[0].bbox[1], t[0].bbox[0]))
        primaries = [(ln, col) for (ln, col) in marks if not _attach_only(ln)]
        attaches = [(ln, col) for (ln, col) in marks if _attach_only(ln)]
        groups: List[List[Tuple[L.Line, int]]] = []
        for (ln, col) in primaries:
            joined = False
            if groups:
                g = groups[-1]
                gb = rect_union([x[0].bbox for x in g])
                if gb:
                    gap = ln.bbox[1] - gb[3]
                    x_ov = min(ln.bbox[2], gb[2]) - max(ln.bbox[0], gb[0])
                    gcols = {c for (_, c) in g}
                    same_col = (col in gcols) or col == -1 or (-1 in gcols)
                    same_band = ln.bbox[1] <= gb[3] + 1.5
                    # 必须"横向也是同一片内容"：允许轻微负重叠，但相隔很远（例如左侧另一段文字）
                    overlap_ok = x_ov >= -0.6 * max(1.0, min(ln.w, gb[2] - gb[0]))
                    if same_col and (gap <= max(1.9 * ln.size, 15.0) or same_band) and overlap_ok:
                        g.append((ln, col))
                        joined = True
            if not joined:
                groups.append([(ln, col)])
        # 吸附行（编号/碎片）并入最近的组；距离过远则丢弃
        for (ln, col) in attaches:
            best = None
            for gi, g in enumerate(groups):
                gb = rect_union([x[0].bbox for x in g])
                if gb is None:
                    continue
                if min(ln.bbox[3], gb[3]) - max(ln.bbox[1], gb[1]) <= -1.5:
                    dist = min(abs(ln.bbox[1] - gb[3]), abs(gb[1] - ln.bbox[3]))
                else:
                    dist = 0.0
                x_ov = min(ln.bbox[2], gb[2]) - max(ln.bbox[0], gb[0])
                if x_ov < -10:                  # 横向离得太远（另一栏/另一处内容）
                    continue
                if best is None or dist < best[0]:
                    best = (dist, gi)
            if best is not None and best[0] <= 30:
                groups[best[1]].append((ln, col))
        # 吸收：把落在候选范围内、或紧邻候选的"非散文"行并进来。
        # 这一步专门修"公式被静默截断"：嵌套分式的分子/分母、大运算符的上下限、
        # 公式右侧的 `= 常数` 等局部若用正文字体排印，就不会被单独识别为数学行。
        claimed = {id(x[0]) for g in groups for x in g}   # 全局已占用：一行只能属于一个公式
        for _round in range(2):
            for g in groups:
                gb = rect_union([x[0].bbox for x in g])
                if gb is None:
                    continue
                have = {id(x[0]) for x in g}
                for (ln, col) in pool:
                    if id(ln) in have or id(ln) in (claimed - have) or _is_prose_line(ln):
                        continue
                    if len(ln.text().split()) > 12:          # 长行是正文
                        continue
                    inside = _mostly_inside(ln.bbox, L.rect_expand(gb, 6))
                    txt = ln.text()
                    n_letters = sum(1 for c in txt if c.isalpha())
                    if inside:
                        # 落在候选框内：要求是短片段，或有明确数学痕迹
                        # （嵌套分式的分子 "a+b" 属于"短且无符号"这一类）
                        short = len(txt.split()) <= 4 and n_letters <= 14
                        if not (short or _has_math_symbol(ln) or _line_math_ratio(ln) >= 0.2):
                            continue
                    else:
                        # 框外：只吸收"带明确数学符号"的紧邻行（如 ∫ 右侧的 ΩT(x,t)dV、
                        # ≈12.4±0.8），避免把紧随其后的正文片段吸进来
                        dy = min(abs(ln.bbox[1] - gb[3]), abs(gb[1] - ln.bbox[3]))
                        x_ov = min(ln.bbox[2], gb[2]) - max(ln.bbox[0], gb[0])
                        if not (dy <= 6 and x_ov >= 0.5 * max(1.0, min(ln.w, gb[2] - gb[0]))
                                and _has_math_symbol(ln)):
                            continue
                    g.append((ln, col))
                    have.add(id(ln))
                    claimed.add(id(ln))
                    gb = rect_union([x[0].bbox for x in g]) or gb
        # 扩展：把同一水平带、紧邻的短片段（如 "M ="、矩阵里的数字格 "1 α 0"）并入公式
        FRAG_RE = re.compile(r"^[\d\.\,\+\-\u2212\u00b1\u00d7\(\)\[\]\{\}=<>≤≥αβγδθλμπσφψω]+$")
        for g in groups:
            gb = rect_union([x[0].bbox for x in g])
            if gb is None:
                continue
            have = {id(x[0]) for x in g}
            for b in p.blocks:
                if b.role in ("header", "footer", "empty", "caption", "ref",
                              "ref-heading", "heading"):
                    continue
                for ln in b.lines:
                    if id(ln) in have:
                        continue
                    t = ln.text().strip()
                    if not t or len(t) > 14:
                        continue
                    frag = bool(FRAG_RE.match(t)) or "=" in t
                    if not frag and _line_math_ratio(ln) < 0.25:
                        continue
                    if min(ln.bbox[3], gb[3]) - max(ln.bbox[1], gb[1]) <= 0.5:
                        continue
                    dx = max(gb[0] - ln.bbox[2], ln.bbox[0] - gb[2])
                    if dx <= 14:
                        g.append((ln, b.col))
                        have.add(id(ln))
                        gb = rect_union([x[0].bbox for x in g]) or gb
        # 过滤：太短的碎片不成公式；整栏宽的正文行（行内公式）也不算；
        # 候选若"整条都寄生在正文块里"（每行都与同块的多词文本行相邻）→ 是段内行内公式，不是展示公式
        line_block: Dict[int, Block] = {}
        page_lines: List[Tuple[L.Line, int]] = []
        for b in p.blocks:
            for ln in b.lines:
                line_block[id(ln)] = b
                if ln.text().strip():
                    page_lines.append((ln, b.col))
        for g in groups:
            g.sort(key=lambda t: (t[0].bbox[1], t[0].bbox[0]))
            lines = [x[0] for x in g]
            nchars = sum(len([c for c in ln.chars if c.c.strip()]) for ln in lines)
            if nchars < 3:
                continue
            col = g[0][1]
            cx0, cx1 = L.column_bounds(p, col)
            cw = max(20.0, cx1 - cx0)
            wide = all((ln.w > 0.92 * cw) for ln in lines)
            numbered = any(_trailing_eqnum(ln) for ln in lines)
            if wide and not numbered:
                continue
            if not numbered and all(_line_swallowed_by_prose(ln, line_block)
                                    or _line_inline_in_paragraph(ln, page_lines)
                                    for ln in lines):
                continue
            out.append((p, lines))
    # 合并"共享同一行"的候选：同一公式被拆成若干互相包含的候选时，靠共享行识别并合回一条。
    # 只按共享行合并，**不按距离合并**，避免把相邻的独立公式并成一条。
    for _round in range(3):
        merged: List[Tuple[PageData, List[L.Line]]] = []
        changed = False
        for (p, lines) in out:
            ids = {id(l) for l in lines}
            hit = None
            for i, (p2, l2) in enumerate(merged):
                if p2.index == p.index and (ids & {id(l) for l in l2}):
                    hit = i
                    break
            if hit is None:
                merged.append((p, lines))
                continue
            p2, l2 = merged[hit]
            seen = {id(l) for l in l2}
            merged[hit] = (p2, l2 + [l for l in lines if id(l) not in seen])
            changed = True
        out = merged
        if not changed:
            break
    # 再按几何关系（纵向紧贴 + 横向相近）合并同一公式被拆开的多段
    out = _post_merge(out)
    return out


def _line_inline_in_paragraph(ln: L.Line, page_lines: Sequence[Tuple[L.Line, int]]) -> bool:
    """这一行是否"段内行内公式"：同一基线上紧挨着散文行。

    与 `_line_swallowed_by_prose` 的分工：那个看"同块上下相邻"，这个看"同一条
    视觉行左右相邻"（MuPDF 会按字体把一行正文切成多个行对象，行内公式因此可能
    自成一块，只能靠页面级线索判断）。
    """
    size = max(ln.size, 1.0)
    for (other, _c) in page_lines:
        if other is ln or not _is_prose_line(other):
            continue
        same_band = not (other.bbox[3] < ln.bbox[1] - 0.4 * size
                         or other.bbox[1] > ln.bbox[3] + 0.4 * size)
        if not same_band:
            continue
        dx = max(other.bbox[0] - ln.bbox[2], ln.bbox[0] - other.bbox[2])
        # 必须是"横向相邻且基本不重叠"：x 范围重叠说明是公式上方/下方的正文（正常排版），
        # 不能因此把展示公式判成段内公式
        if -1.0 <= dx <= 4.0 * size:
            return True
    return False


def _post_merge(cands: List[Tuple[PageData, List[L.Line]]],
                max_gap: float = 12.0, min_x_ratio: float = 0.35,
                max_x_dist: float = 14.0) -> List[Tuple[PageData, List[L.Line]]]:
    """把"横向位置相近、纵向几乎贴在一起"的候选并成一条。

    同一公式被 MuPDF 按字体/字形拆成多段时（矩阵的每一行、公式末尾的
    ", α,β∈R." 条件），这些段之间不共享行，只能靠几何关系合并；
    而**不同公式之间通常有 ≥20pt 行距**，用 12pt 阈值是安全的。
    """
    for _round in range(4):
        merged: List[Tuple[PageData, List[L.Line]]] = []
        changed = False
        for (p, lines) in cands:
            b1 = rect_union([ln.bbox for ln in lines])
            hit = None
            for i, (p2, l2) in enumerate(merged):
                if p2.index != p.index or b1 is None:
                    continue
                b2 = rect_union([ln.bbox for ln in l2])
                if b2 is None:
                    continue
                gap = max(b1[1] - b2[3], b2[1] - b1[3])
                if gap > max_gap:
                    continue
                x_ov = min(b1[2], b2[2]) - max(b1[0], b2[0])
                x_dist = max(b2[0] - b1[2], b1[0] - b2[2])
                narrow = max(1.0, min(b1[2] - b1[0], b2[2] - b2[0]))
                if x_ov >= min_x_ratio * narrow or x_dist <= max_x_dist:
                    hit = i
                    break
            if hit is None:
                merged.append((p, lines))
                continue
            p2, l2 = merged[hit]
            seen = {id(l) for l in l2}
            merged[hit] = (p2, l2 + [l for l in lines if id(l) not in seen])
            changed = True
        cands = merged
        if not changed:
            break
    return cands


def _is_prose_line(ln: L.Line) -> bool:
    """这一行是"散文"吗：词数 ≥3（或字母数 ≥12）且数学字形占比 < 0.35。

    **不能只看词数**：数学字体混排的段落被 MuPDF 切成若干"行片段"后，
    片段里的空格可能缺失，`split()` 会把它算成 1 个词 —— 实测因此把整段正文
    （"The time step is fixed at ..."）吸进了公式候选。字母数阈值正是为了兜住这种片段。
    """
    txt = ln.text()
    n_words = len(txt.split())
    n_letters = sum(1 for c in txt if c.isalpha())
    if n_words < 3 and n_letters < 10:
        return False
    return _line_math_ratio(ln) < 0.22


def _line_swallowed_by_prose(ln: L.Line, line_block: Dict[int, Block],
                             max_gap_ratio: float = 0.6) -> bool:
    """这一行是否被"同一个块里的散文行"紧贴着（= 段内行内公式的特征）。

    展示公式上下会有额外垂直间距，且通常自成一块；段内公式则紧贴在正文行旁边。
    """
    b = line_block.get(id(ln))
    if b is None:
        return False
    size = max(ln.size, 1.0)
    for other in b.lines:
        if other is ln or not _is_prose_line(other):
            continue
        # 情形 1：散文行在紧邻的上方/下方（展示公式上下会有额外间距，段内公式不会）
        if 0 <= ln.bbox[1] - other.bbox[3] <= max_gap_ratio * size:
            return True
        if 0 <= other.bbox[1] - ln.bbox[3] <= max_gap_ratio * size:
            return True
        # 情形 2：散文行在同一基线上、紧挨着（段内公式最常见的形态：正文 → 公式 → 正文）
        same_band = not (other.bbox[3] < ln.bbox[1] - 0.4 * size
                         or other.bbox[1] > ln.bbox[3] + 0.4 * size)
        if same_band:
            dx = max(other.bbox[0] - ln.bbox[2], ln.bbox[0] - other.bbox[2])
            if -1.0 <= dx <= 4.0 * size:      # 同上：排除"x 范围重叠的上下行"
                return True
    return False


def _line_math_ratio(ln: L.Line) -> float:
    tot = mathc = 0
    for c in ln.chars:
        if not c.c.strip():
            continue
        tot += 1
        if c.is_math_font or c.c in L.SYMBOLS or c.c in L.SUP_MAP or c.c in L.SUB_MAP:
            mathc += 1
    return (mathc / tot) if tot else 0.0


def extract_equations(d: Document, out_dir: Path, dpi: float,
                      exclude: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None
                      ) -> List[EquationInfo]:
    eqs: List[EquationInfo] = []
    ei_chars: Dict[int, List[Char]] = {}
    doc = fitz.open(d.path)
    for (p, lines) in _display_candidates(d, exclude):
        chars: List[Char] = []
        for ln in lines:
            chars.extend(ln.chars)
        chars = [c for c in chars if c.c.strip()]
        if not chars:
            continue
        bb = (min(c.x0 for c in chars), min(c.y0 for c in chars),
              max(c.x1 for c in chars), max(c.y1 for c in chars))
        num = ""
        for ln in reversed(lines):
            m = re.search(r"[\(\[]\s*(\d+[a-zA-Z]?)\s*[\)\]]\s*[.,;]?$", ln.text().strip())
            if m:
                num = m.group(1)
                break
        if not num:
            num = _find_eq_number(p, bb)
        ei = EquationInfo(index=len(eqs) + 1, number=num, bbox=bb, page=p.index,
                          raw_text=" ".join("".join(c.c for c in [x for x in ln.chars if x.c.strip()])
                                            for ln in lines))
        rules = [r for r in p.rules if rect_inter(r, L.rect_expand(bb, 4))]
        latex, conf, notes, unmapped = L.layout_to_latex(chars, rules, d.body_size)
        # 覆盖自检：候选框内若还有"非散文的数学行"没被纳入，说明这条公式被截断了。
        # 专门防"静默截断"——内容缺失却拿着高置信度一路 confirm 到交付。
        ids = {id(ln) for ln in lines}
        missed: List[L.Line] = []
        for b2 in p.blocks:
            if b2.role in ("header", "footer", "empty", "caption", "ref", "ref-heading", "heading"):
                continue
            for ln2 in b2.lines:
                if id(ln2) in ids or not ln2.text().strip():
                    continue
                if _is_prose_line(ln2) or len(ln2.text().split()) > 12:
                    continue
                if _mostly_inside(ln2.bbox, L.rect_expand(bb, 4)):
                    missed.append(ln2)
        if missed:
            conf = min(conf, 0.6)
            notes.append(f"候选框内另有 {len(missed)} 行未纳入（疑似截断）："
                         + "、".join(repr(l.text()[:14]) for l in missed[:3])
                         + " —— 必须对照原图补全")
        ei.multi_row = any("flattened" not in n for n in notes) and len(lines) > 1
        ei.latex, ei.confidence, ei.notes, ei.unmapped = latex, conf, notes, unmapped
        eqs.append(ei)
        ei_chars[id(ei)] = chars
    # 排序：按页码 + y
    eqs.sort(key=lambda e: (e.page, e.bbox[1]))
    for i, e in enumerate(eqs):
        e.index = i + 1
    # 同一编号出现多次：说明一个多行公式被按行切开，提示人工合并
    from collections import Counter
    cnt = Counter(e.number for e in eqs if e.number)
    for e in eqs:
        if e.number and cnt[e.number] > 1:
            e.notes.append(f"原编号 ({e.number}) 出现 {cnt[e.number]} 次："
                           f"这是同一多行公式的分行，需人工对照原图合并成一条")
    # 落盘
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "draft").mkdir(parents=True, exist_ok=True)
    for ei in eqs:
        idx = "%02d" % ei.index
        png = out_dir / f"eq{idx}_orig.png"
        page = doc[ei.page]
        try:
            w, h = crop_png(page, L.rect_expand(ei.bbox, 3.0), dpi, png)
            ei.notes.append(f"orig={png.name} px={w}x{h}")
        except Exception as e:                                # pragma: no cover
            ei.notes.append(f"crop-failed: {e}")
        body = ei.latex.strip()
        (out_dir / "draft" / f"eq{idx}.tex").write_text(body + "\n", encoding="utf-8")
        # 原文字符转储：供 equations.py 做"符号守恒"机器校验（无需看图即可发现漏符号）
        chars_dump = [{"c": c.c, "x0": round(c.x0, 2), "y0": round(c.y0, 2),
                       "x1": round(c.x1, 2), "y1": round(c.y1, 2),
                       "bx": round(c.bx, 2), "by": round(c.by, 2),
                       "size": round(c.size, 2), "font": c.font, "flags": c.flags}
                      for c in ei_chars.get(id(ei), [])]
        write_json(out_dir / f"eq{idx}_chars.json", chars_dump)
    doc.close()
    return eqs


# --------------------------------------------------------------------------- #
# 4) 源文（供翻译）+ 章节
# --------------------------------------------------------------------------- #

def _heading_text(b: Block) -> str:
    return " ".join(ln.text().strip() for ln in b.lines if ln.text().strip()).strip()


def _heading_number(text: str) -> str:
    m = re.match(r"^\s*((?:\d+\.)*\d+)\s*[\.\s]?\s*", text)
    return m.group(1) if m else ""


def _eq_for_line(eqs: Sequence[EquationInfo], ln: L.Line) -> Optional[EquationInfo]:
    """这一行是否属于某条行间公式（含"右边缘的编号行"）。"""
    txt = ln.text().strip()
    is_num = bool(re.fullmatch(r"[\(\[]?\s*\d+[a-zA-Z]?\s*[\)\]]?[.,;]?", txt))
    for e in eqs:
        if is_num and not (ln.y1 < e.bbox[1] - 2 or ln.y0 > e.bbox[3] + 2):
            return e                     # 编号行：同一水平带即归属该公式
        if rect_iou(e.bbox, ln.bbox) > 0.2 or (
                ln.y0 <= e.bbox[3] and ln.y1 >= e.bbox[1] and
                ln.x0 <= e.bbox[2] and ln.x1 >= e.bbox[0]):
            return e
    return None


def render_line_with_math(ln: L.Line, page: PageData, dom: float,
                          stats: Dict[str, Any]) -> str:
    """把一行文本里的数学片段替换为 $LaTeX$。"""
    runs = L.find_math_runs(ln, dom)
    chars = ln.chars
    if not runs:
        return ln.text()
    out: List[str] = []
    pos = 0
    for (a, b) in runs:
        seg = [c for c in chars[pos:a]]
        out.append(L.chars_to_text(seg) if seg else "")
        mchars = chars[a:b]
        latex, conf, notes, unmapped = L.run_to_latex(mchars,
                                                     [r for r in page.rules
                                                      if rect_inter(r, L.rect_expand(
                                                          (min(c.x0 for c in mchars), min(c.y0 for c in mchars),
                                                           max(c.x1 for c in mchars), max(c.y1 for c in mchars)), 4))],
                                                     dom)
        if not latex.strip():
            out.append(L.chars_to_text(mchars))
        else:
            marker = "" if conf >= L.MIN_MATH_CONFIDENCE else "  % LOWCONF"
            out.append("$%s$%s" % (latex.strip(), marker))
            stats["inline_math"] += 1
            if conf < L.MIN_MATH_CONFIDENCE:
                stats["inline_lowconf"] += 1
                stats["lowconf_items"].append({"page": page.index + 1, "latex": latex,
                                               "conf": round(conf, 2), "notes": notes})
        pos = b
    tail = chars[pos:]
    if tail:
        out.append(L.chars_to_text(tail))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def build_source_md(d: Document, out_dir: Path, figs: List[FigureInfo],
                    tabs: List[TableInfo], eqs: List[EquationInfo]) -> Dict[str, Any]:
    src = out_dir / "source"
    src.mkdir(parents=True, exist_ok=True)
    lines_out: List[str] = []
    stats: Dict[str, Any] = {"inline_math": 0, "inline_lowconf": 0, "lowconf_items": [],
                             "dehyphenation": [], "blocks": 0, "paragraphs": 0}
    sections: List[Dict[str, Any]] = []
    fig_by_page: Dict[int, List[FigureInfo]] = {}
    for f in figs:
        fig_by_page.setdefault(f.page, []).append(f)
    tab_by_page: Dict[int, List[TableInfo]] = {}
    for t in tabs:
        page_idx = t.page if getattr(t, "page", -1) >= 0 else None
        if page_idx is None:
            for p in d.pages:
                if t.caption_bbox and _bbox_on_page(t.caption_bbox, p):
                    page_idx = p.index
                    break
        tab_by_page.setdefault(page_idx if page_idx is not None else 0, []).append(t)
    # 表格区域内的行不再当正文输出（否则表格内容会既以文字、又以 ⟦TABLEnn⟧ 出现两次）
    table_regions: Dict[int, List[Tuple[float, float, float, float]]] = {}
    for t in tabs:
        table_regions.setdefault(t.page, []).append(tuple(t.bbox))
    # 图区域内的文字（坐标轴标签、图例、面板编号）也不进译文：它们随原图保留英文，
    # 让译者看到会误以为需要翻译，而且旋转文字常被抽成反序乱码
    fig_regions: Dict[int, List[Tuple[float, float, float, float]]] = {}
    for f in figs:
        if f.kind != "missing":
            fig_regions.setdefault(f.page, []).append(tuple(L.rect_expand(f.bbox, 2)))
    # 首页"正文之前"的标题/作者/日期等属于 front matter，不算章节（否则 F8 结构核对会误报）
    front_cut: Dict[int, float] = {}
    for p in d.pages:
        ys = [b.y0 for b in p.blocks if b.role in ("body", "list")]
        if ys:
            front_cut[p.index] = min(ys)
    eq_by_page: Dict[int, List[EquationInfo]] = {}
    for e in eqs:
        eq_by_page.setdefault(e.page, []).append(e)

    emitted_eq: set = set()
    emitted_tab: set = set()
    emitted_fig: set = set()

    lines_out.append("<!-- skill-translator source.md：由 extract.py 自动生成，供逐节翻译 -->")
    lines_out.append("<!-- 约定：⟦EQnn⟧/⟦TABLEnn⟧ 必须原样保留；$...$ 为已还原的公式，原样保留；"
                     "标 [!LOWCONF] 的公式必须人工核对 -->")
    lines_out.append("")

    for p in d.pages:
        ordered = order_blocks(p)
        lines_out.append(f"<!-- ===== page {p.index + 1} / {d.n_pages} ===== -->")
        for b in ordered:
            if b.role in ("header", "footer", "empty"):
                continue
            stats["blocks"] += 1
            # 表格区域内的块（含单元格行）跳过：由 ⟦TABLEnn⟧ 承载
            if any(_mostly_inside(b.bbox, r) for r in table_regions.get(p.index, [])):
                continue
            # 图区域内的块（轴标签/图例）跳过：随原图保留英文，只把文字留一条注释备查
            if any(_mostly_inside(b.bbox, r) for r in fig_regions.get(p.index, [])):
                t = re.sub(r"\s+", " ", b.text().strip())
                if t:
                    lines_out.append(f"<!-- 图内文字（随原图保留英文，不翻译）：{t[:180]} -->")
                continue
            # 首页正文之前的标题/作者/日期：作为 front matter 输出，不计入章节
            if (p.index == 0 and b.role == "heading"
                    and b.y1 <= front_cut.get(0, b.y1) + 1):
                txt = _heading_text(b)
                lines_out.append("")
                lines_out.append("<!-- front-matter（标题/作者/日期，不入章节计数） -->")
                lines_out.append("> " + txt)
                lines_out.append("")
                sections.append({"page": 1, "level": 1, "number": "",
                                 "title_en": txt, "block": f"p1b{b.block_no}",
                                 "front_matter": True})
                continue
            if b.role == "heading":
                txt = _heading_text(b)
                num = _heading_number(txt)
                lvl = max(1, min(3, b.level))
                lines_out.append("")
                lines_out.append("<!-- heading level=%d block=p%db%d -->" % (lvl, p.index + 1, b.block_no))
                lines_out.append("#" * lvl + " " + txt)
                lines_out.append("")
                sections.append({"page": p.index + 1, "level": lvl, "number": num,
                                 "title_en": txt, "block": f"p{p.index+1}b{b.block_no}"})
                continue
            if b.role == "ref-heading":
                lines_out.append("")
                lines_out.append("<!-- REFERENCES：以下内容不翻译，原样保留 -->")
                lines_out.append("## " + _heading_text(b))
                lines_out.append("")
                sections.append({"page": p.index + 1, "level": 2, "number": "",
                                 "title_en": _heading_text(b), "block": f"p{p.index+1}b{b.block_no}",
                                 "no_translate": True})
                continue
            if b.role == "ref":
                for ln in b.lines:
                    t = ln.text().strip()
                    if t:
                        lines_out.append(t)
                lines_out.append("")
                continue
            if b.role == "caption":
                fig = next((f for f in fig_by_page.get(p.index, [])
                            if f.caption_bbox and rect_iou(f.caption_bbox, b.bbox) > 0.3), None)
                tab = next((t for t in tab_by_page.get(p.index, [])
                            if t.caption_bbox and rect_iou(t.caption_bbox, b.bbox) > 0.3), None)
                if fig is not None:
                    lines_out.append(f"<!-- FIGCAP{fig.index:02d} 原编号 {fig.number} 页码 {p.index+1} -->")
                    lines_out.append("> " + caption_text(b))
                    lines_out.append(f"<!-- 插图占位：译为 ⟦FIG{fig.index:02d}|中文图题⟧ 放在正文相应位置 -->")
                    lines_out.append("")
                    emitted_fig.add(fig.index)
                elif tab is not None:
                    idx = "%02d" % (tabs.index(tab) + 1)
                    lines_out.append(f"<!-- 表格：原编号 {tab.number} 页码 {p.index+1} "
                                     f"模式 {'截图' if tab.strategy == 'image' else 'LaTeX 三线表'} "
                                     f"质量 {tab.quality:.2f} -->")
                    lines_out.append("> " + tab.caption)
                    lines_out.append(TAB_TOKEN.format(idx))
                    lines_out.append("")
                    emitted_tab.add(id(tab))
                else:
                    lines_out.append("<!-- 未配对的题注，人工核对 -->")
                    lines_out.append("> " + caption_text(b))
                    lines_out.append("")
                continue
            if b.role == "math" or (b.role in ("body", "list") and any(
                    _line_math_ratio(ln) >= 0.55 for ln in b.lines)):
                # 逐行输出，遇到公式行替换为占位符
                buf: List[str] = []
                for ln in b.lines:
                    hit = _eq_for_line(eq_by_page.get(p.index, []), ln)
                    if hit is not None:
                        # 属于某条公式的行：全部跳过；占位符只在第一次遇到时输出
                        if hit.index in emitted_eq:
                            continue
                        if buf:
                            lines_out.append(" ".join(buf))
                            lines_out.append("")
                            buf = []
                        lines_out.append(f"<!-- 行间公式：原编号 ({hit.number or '无'})  置信度 {hit.confidence:.2f} -->")
                        if hit.confidence < L.MIN_MATH_CONFIDENCE:
                            lines_out.append("<!-- [!LOWCONF] 公式草稿置信度低，必须与原图逐符号比对 -->")
                        lines_out.append(EQ_TOKEN.format("%02d" % hit.index))
                        lines_out.append("")
                        emitted_eq.add(hit.index)
                    else:
                        mr = _line_math_ratio(ln)
                        if mr >= 0.55 and len(ln.text().strip()) >= 2:
                            continue      # 由上面的候选统一处理
                        buf.append(render_line_with_math(ln, p, d.body_size, stats))
                if buf:
                    lines_out.append(" ".join(buf))
                    lines_out.append("")
                    stats["paragraphs"] += 1
                continue
            # 普通正文/列表
            # 列表识别
            is_list = False
            first = b.lines[0].text().strip() if b.lines else ""
            if re.match(r"^[\u2022\u25cf\u25aa\u25e6\-\*\u2013]\s+", first) or re.match(r"^\(?\d+[\.\)]\s+", first):
                is_list = True
                b.role = "list"
            if is_list:
                buf_item: Optional[str] = None
                for ln in b.lines:
                    t = render_line_with_math(ln, p, d.body_size, stats).strip()
                    if not t:
                        continue
                    is_bullet = bool(re.match(r"^[\u2022\u25cf\u25aa\u25e6\-\*\u2013]\s+", t)
                                     or re.match(r"^\(?\d+[\.\)]\s+", t))
                    if is_bullet:
                        if buf_item:
                            lines_out.append(buf_item)
                        t = re.sub(r"^[\u2022\u25cf\u25aa\u25e6\-\*\u2013]\s+", "- ", t)
                        buf_item = t
                    elif buf_item is not None:
                        # 列表项的续行（含跨行断词）合并回上一条
                        if L.HYPHEN_END_RE.search(buf_item) and t[:1].islower():
                            buf_item = buf_item[:-1] + t
                        else:
                            buf_item = buf_item + " " + t
                if buf_item:
                    lines_out.append(buf_item)
                lines_out.append("")
            else:
                # 段内逐行做公式替换（保留段结构）；落在公式范围内的行不当作正文
                parts: List[str] = []

                def _flush_parts() -> None:
                    nonlocal parts
                    if parts:
                        lines_out.append(" ".join(parts).strip())
                        lines_out.append("")
                        stats["paragraphs"] += 1
                        parts = []

                for ln in b.lines:
                    e_hit = _eq_for_line(eq_by_page.get(p.index, []), ln)
                    if e_hit is not None:
                        _flush_parts()
                        if e_hit.index not in emitted_eq:
                            lines_out.append(f"<!-- 行间公式：原编号 ({e_hit.number or '无'})  "
                                             f"置信度 {e_hit.confidence:.2f} -->")
                            if e_hit.confidence < L.MIN_MATH_CONFIDENCE:
                                lines_out.append("<!-- [!LOWCONF] 公式草稿置信度低，必须与原图逐符号比对 -->")
                            lines_out.append(EQ_TOKEN.format("%02d" % e_hit.index))
                            lines_out.append("")
                            emitted_eq.add(e_hit.index)
                        continue
                    t = render_line_with_math(ln, p, d.body_size, stats)
                    if not t:
                        continue
                    if parts and L.HYPHEN_END_RE.search(parts[-1]) and t[:1].islower():
                        parts[-1] = parts[-1][:-1] + t
                    elif parts and re.search(r"[A-Za-z]$", parts[-1]) and re.match(r"^[a-z]", t):
                        parts[-1] = parts[-1] + " " + t
                    else:
                        parts.append(t)
                _flush_parts()
                continue
        # 页末：该页表格占位符
        for t in tab_by_page.get(p.index, []):
            if id(t) in emitted_tab:
                continue
            idx = "%02d" % (tabs.index(t) + 1)
            lines_out.append(f"<!-- 表格：原编号 {t.number} 页码 {p.index+1} 模式 {'截图' if t.strategy=='image' else 'LaTeX 重排'} -->")
            lines_out.append("> " + t.caption)
            lines_out.append(TAB_TOKEN.format(idx))
            lines_out.append("")
            emitted_tab.add(id(t))

    md = "\n".join(lines_out)
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    (src / "source.md").write_text(md, encoding="utf-8")
    write_json(src / "sections.json", sections)
    stats["sections"] = len(sections)
    stats["figures"] = len(figs)
    stats["tables"] = len(tabs)
    stats["equations"] = len(eqs)
    stats["equations_lowconf"] = sum(1 for e in eqs if e.confidence < L.MIN_MATH_CONFIDENCE)
    return stats


# --------------------------------------------------------------------------- #
# 5) 清单文件
# --------------------------------------------------------------------------- #

def write_manifests(out_dir: Path, figs: List[FigureInfo], tabs: List[TableInfo],
                    eqs: List[EquationInfo]) -> None:
    lines = ["# 图片清单（figures-manifest.md）", "",
             "| 文件 | 原编号 | 页码 | 类型 | 内含英文文字 | 大小(点) | 备注 |",
             "|---|---|---|---|---|---|---|"]
    for f in figs:
        w = f.bbox[2] - f.bbox[0]
        h = f.bbox[3] - f.bbox[1]
        notes = "; ".join(f.notes)
        lines.append(f"| figures/fig{f.index}.png | {f.number} | {f.page + 1} | {f.kind} | "
                     f"{'是（无法自动翻译）' if f.has_text_inside else '否'} | {w:.0f}x{h:.0f} | {notes} |")
    lines += ["", "## 图题（英文原文，需翻译为中文图题）", ""]
    for f in figs:
        lines.append(f"- **fig{f.index}（原 Figure {f.number}，第 {f.page+1} 页）**：{f.caption}")
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    (out_dir / "figures" / "figures-manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tl = ["# 表格清单（tables-manifest.md）", "",
          "| 序号 | 原编号 | 模式 | 行列 | 质量 | 文件 | 备注 |", "|---|---|---|---|---|---|---|"]
    for i, t in enumerate(tabs, 1):
        idx = "%02d" % i
        f = f"table{idx}.png" if t.strategy == "image" else f"table{idx}.tex"
        tl.append(f"| {idx} | {t.number} | {'截图' if t.strategy=='image' else 'LaTeX 三线表'} | "
                  f"{t.n_rows}x{t.n_cols} | {t.quality:.2f} | tables/{f} | {'; '.join(t.notes)} |")
    tl += ["", "## 表题（英文原文，需翻译为中文表题）", ""]
    for i, t in enumerate(tabs, 1):
        tl.append(f"- **table{'%02d' % i}（原 Table {t.number}）**：{t.caption}")
    (out_dir / "tables").mkdir(parents=True, exist_ok=True)
    (out_dir / "tables" / "tables.json").write_text(
        json.dumps({("%02d" % i): {"number": t.number, "mode": "image" if t.strategy == "image" else "latex",
                                   "file": ("table%02d.png" % i) if t.strategy == "image" else ("table%02d.tex" % i),
                                   "cols": _cols_spec(t.rows) if t.rows else "l",
                                   "caption_en": t.caption, "quality": round(t.quality, 3),
                                   "notes": t.notes}
                    for i, t in enumerate(tabs, 1)}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "tables" / "tables-manifest.md").write_text("\n".join(tl) + "\n", encoding="utf-8")

    el = ["# 公式清单（equations.md）", "",
          "**复核规则**：逐条把 `equations/eqNN_orig.png`（原文截图）与 `equations/draft/eqNN.tex`"
          "（还原的 LaTeX，`equations.py render` 会渲染成图）做视觉比对；确认后在 equations 目录执行"
          " `python equations.py confirm eqNN --note \"...\"`。低于置信度阈值的必须改。", "",
          "| 序号 | 原编号 | 页码 | 置信度 | 多行 | 疑点 | 原文（抽取） | 自动草稿 |",
          "|---|---|---|---|---|---|---|---|"]
    for e in eqs:
        idx = "%02d" % e.index
        raw = e.raw_text.replace("|", "\\|")[:60]
        draft = e.latex.replace("|", "\\|").replace("\n", " ")[:60]
        flag = "**低**" if e.confidence < L.MIN_MATH_CONFIDENCE else f"{e.confidence:.2f}"
        el.append(f"| eq{idx} | {e.number or '无'} | {e.page+1} | {flag} | {'是' if e.multi_row else '否'} | "
                  f"{'; '.join(e.notes)} | `{raw}` | `{draft}` |")
    (out_dir / "equations").mkdir(parents=True, exist_ok=True)
    (out_dir / "equations" / "equations.md").write_text("\n".join(el) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def parse_pages(spec: str, n: int) -> Optional[List[int]]:
    if not spec:
        return None
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, min(int(b), n)))
        else:
            out.append(int(part) - 1)
    return sorted(set(i for i in out if 0 <= i < n))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="英文期刊 PDF -> 可翻译素材（图/表/公式/文本）")
    ap.add_argument("pdf", help="英文期刊 PDF 路径")
    ap.add_argument("--out", default="translator-work", help="工作目录（默认 translator-work）")
    ap.add_argument("--dpi", type=float, default=400.0, help="图片裁剪 DPI（默认 400）")
    ap.add_argument("--pages", default="", help="只处理部分页，如 1-5,8")
    ap.add_argument("--no-tables", action="store_true", help="表格一律截图，不做结构重排")
    ap.add_argument("--keep-source", action="store_true", help="把源 PDF 复制到 <work>/source/")
    args = ap.parse_args(argv)

    pdf = abs_path(args.pdf)
    if not pdf.exists():
        print(f"[x] 找不到 PDF：{pdf}", file=sys.stderr)
        return 2
    work = abs_path(args.out)
    out_dir = work / "extracted"
    for sub in ("figures", "tables", "equations", "source"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    if args.keep_source:
        (work / "source").mkdir(parents=True, exist_ok=True)
        dst = work / "source" / pdf.name
        if not dst.exists():
            dst.write_bytes(pdf.read_bytes())

    print(f"[1/6] 解析版面：{pdf.name}")
    d = L.load_document(str(pdf), progress=False)
    pages = parse_pages(args.pages, d.n_pages)
    if pages is not None:
        d.pages = [p for p in d.pages if p.index in set(pages)]
        d.warnings.append(f"仅处理页 {args.pages}")
    L.mark_headers_footers(d)
    L.classify_blocks(d)
    L.mark_reference_zones(d)
    print(f"      页数={d.n_pages} 栏数={d.n_cols} 正文字号={d.body_size}")

    print("[2/6] 提取图片 -> figures/fig1..n.png")
    figs = extract_figures(d, out_dir / "figures", args.dpi)
    print(f"      找到 {len(figs)} 张图；缺失 {sum(1 for f in figs if f.kind=='missing')} 张")

    print("[3/6] 提取表格 -> tables/")
    tabs = extract_tables(d, out_dir / "tables", args.dpi, force_image=args.no_tables)
    print(f"      找到 {len(tabs)} 张表；截图兜底 {sum(1 for t in tabs if t.strategy=='image')} 张")

    print("[4/6] 提取行间公式 -> equations/")
    excl: Dict[int, List[Tuple[float, float, float, float]]] = {}
    for t in tabs:
        if t.page >= 0:
            excl.setdefault(t.page, []).append(tuple(t.bbox))
    eqs = extract_equations(d, out_dir / "equations", args.dpi, exclude=excl)
    low = sum(1 for e in eqs if e.confidence < L.MIN_MATH_CONFIDENCE)
    print(f"      找到 {len(eqs)} 条公式；低置信度 {low} 条（必须人工复核）")

    print("[5/6] 生成可翻译源文 -> extracted/source/source.md")
    stats = build_source_md(d, out_dir, figs, tabs, eqs)

    print("[6/6] 写清单与结构化数据")
    write_manifests(out_dir, figs, tabs, eqs)
    write_json(out_dir / "doc.json", {
        "path": d.path, "n_pages": d.n_pages, "n_cols": d.n_cols, "body_size": d.body_size,
        "warnings": d.warnings,
        "pages": [{"index": p.index, "n_cols": p.n_cols, "split_x": p.split_x,
                   "blocks": [{"bbox": b.bbox, "col": b.col, "role": b.role, "level": b.level,
                               "conf": round(b.conf, 3), "notes": b.notes,
                               "text": b.text()[:400]} for b in order_blocks(p)]} for p in d.pages],
    })
    manifest = {
        "source_pdf": str(pdf), "work_dir": str(work), "dpi": args.dpi,
        "pages": d.n_pages, "columns": d.n_cols, "body_size": d.body_size,
        "figures": [asdict(f) for f in figs],
        "tables": [asdict(t) for t in tabs],
        "equations": [asdict(e) for e in eqs],
        "stats": stats,
        "warnings": ([f"图 {f.index} 定位失败，需人工截图" for f in figs if f.kind == "missing"] +
                     [f"公式 eq{e.index:02d} 置信度 {e.confidence:.2f}，必须复核"
                      for e in eqs if e.confidence < L.MIN_MATH_CONFIDENCE] +
                     [f"内联公式低置信 {stats['inline_lowconf']} 处" if stats["inline_lowconf"] else ""] +
                     d.warnings),
    }
    manifest["warnings"] = [w for w in manifest["warnings"] if w]
    write_json(out_dir / "manifest.json", manifest)

    print("\n=== 提取完成 ===")
    print(f"工作目录      : {work}")
    print(f"图 fig1~fig{len(figs)} -> extracted/figures/")
    print(f"表 {len(tabs)} 张 -> extracted/tables/")
    print(f"公式 {len(eqs)} 条 -> extracted/equations/（低置信 {low} 条）")
    print(f"章节 {stats['sections']} 个 -> extracted/source/sections.json")
    print(f"内联公式 {stats['inline_math']} 处（低置信 {stats['inline_lowconf']} 处）")
    if manifest["warnings"]:
        print("\n需要人工处理：")
        for w in manifest["warnings"]:
            print("  ! " + w)
    print("\n下一步：读 extracted/source/source.md 逐节翻译；"
          "先跑 `python equations.py render --work <work>` 生成公式对照图并逐条 confirm。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
