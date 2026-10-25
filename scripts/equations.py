# -*- coding: utf-8 -*-
"""equations.py —— 公式还原的"可视化复核"工具（skill-translator 的关键质检环节）。

为什么需要它：PDF 里的公式是排版结果的字形序列，机器还原成 LaTeX 只能做到"高概率正确"。
唯一可靠的验收方式是**把还原结果重新渲染成图，与原文截图并排目视比对**。本工具负责：

    render   把 equations/draft/eqNN.tex 逐个用 LaTeX 渲染成 PNG，并与 eqNN_orig.png
             拼成并排对照图 eqNN_cmp.png（左=原文，右=还原结果）；编译失败会写日志并标红。
    confirm  人工（agent）看过对照图后登记确认；记录草稿文件的 sha1，草稿一改即失效。
    status   汇总每条公式的复核状态，输出可勾选的清单（给 verify.py 与终检用）。

用法：
    python equations.py render  --work translator-work [--only eq03] [--dpi 220]
    python equations.py confirm eq03 --work translator-work --note "与原文逐符号一致"
    python equations.py status  --work translator-work
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import os
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
import fitz  # noqa: E402

STANDALONE_TEX = r"""\documentclass[border=3pt]{standalone}
\usepackage{amsmath,amssymb,amsfonts,bm}
\usepackage{ctex}
\begin{document}
$\displaystyle %s$
\end{document}
"""

STATUS_FILE = "status.json"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def find_tex_engine() -> Optional[str]:
    for name in ("xelatex", "latexmk", "pdflatex"):
        p = shutil.which(name)
        if p:
            return p
    return None


def eq_dir(work: Path) -> Path:
    return work / "extracted" / "equations"


def load_status(work: Path) -> Dict[str, Any]:
    f = eq_dir(work) / STATUS_FILE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def save_status(work: Path, st: Dict[str, Any]) -> None:
    f = eq_dir(work) / STATUS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def draft_files(work: Path) -> List[Path]:
    d = eq_dir(work) / "draft"
    if not d.exists():
        return []
    return sorted(d.glob("eq*.tex"))


def read_draft(p: Path) -> str:
    t = p.read_text(encoding="utf-8-sig")
    t = re.sub(r"(?m)^\s*%.*$", "", t)          # 去掉整行注释
    return t.strip()


def load_orig_chars(work: Path, tag: str) -> List[Any]:
    """读回 extract.py 转储的原文字符（用于符号守恒校验）。"""
    import p2zlib as L
    f = eq_dir(work) / f"{tag}_chars.json"
    if not f.exists():
        return []
    try:
        obj = json.loads(f.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    out = []
    for d in obj:
        out.append(L.Char(c=d["c"], x0=d["x0"], y0=d["y0"], x1=d["x1"], y1=d["y1"],
                          bx=d["bx"], by=d["by"], font=d.get("font", ""),
                          size=d.get("size", 10.0), flags=int(d.get("flags", 0))))
    return out


def symbol_check(work: Path, tag: str, latex: str) -> Dict[str, Any]:
    """不依赖图像识别的公式校验：原文符号 ↔ 还原 LaTeX 符号的多重集比对。"""
    import p2zlib as L
    chars = load_orig_chars(work, tag)
    if not chars:
        return {"ok": True, "skipped": "没有原文字符转储（旧版本产物？请重跑 extract.py）",
                "missing": {}, "extra": {}}
    res = L.symbol_diff(chars, latex)
    res["skipped"] = ""
    return res


# --------------------------------------------------------------------------- #
# 渲染单条公式
# --------------------------------------------------------------------------- #

def compile_equation(tex_body: str, engine: str, build_dir: Path, tag: str) -> Tuple[bool, str, Optional[Path]]:
    """把公式体编译成 PDF，返回 (成功, 日志尾部, PDF 路径)。"""
    build_dir.mkdir(parents=True, exist_ok=True)
    src = build_dir / f"{tag}.tex"
    src.write_text(STANDALONE_TEX % tex_body, encoding="utf-8-sig")
    cmd = [engine, "-interaction=nonstopmode", "-halt-on-error",
           "-output-directory", str(build_dir), str(src)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90,
                           cwd=str(build_dir), errors="replace")
    except subprocess.TimeoutExpired:
        return False, "编译超时(90s)", None
    except Exception as e:                                  # pragma: no cover
        return False, f"编译无法启动：{e}", None
    log = (r.stdout or "") + (r.stderr or "")
    pdf = build_dir / f"{tag}.pdf"
    if r.returncode != 0 or not pdf.exists():
        tail = "\n".join(_error_lines(log)) or log[-800:]
        return False, tail, None
    return True, "", pdf


def _error_lines(log: str, max_lines: int = 12) -> List[str]:
    out = []
    lines = log.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("!") or "Undefined control sequence" in ln or "! LaTeX Error" in ln:
            out.append(ln.strip())
            out.extend(x.strip() for x in lines[i + 1:i + 4])
            if len(out) >= max_lines:
                break
    return out


def render_pdf_to_png(pdf: Path, out_png: Path, dpi: float, max_width: int = 2400) -> Tuple[int, int]:
    doc = fitz.open(str(pdf))
    page = doc[0]
    scale = dpi / 72.0
    w = page.rect.width * scale
    if w > max_width:
        scale = max_width / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))
    doc.close()
    return pix.width, pix.height


def compose_compare(orig_png: Path, render_png: Path, out_png: Path, tag: str,
                    note: str = "") -> Tuple[int, int]:
    """把原文截图与还原渲染并排合成一张对照图（左原文 / 右还原）。"""
    doc = fitz.open()
    imgs = []
    for p in (orig_png, render_png):
        if p.exists():
            imgs.append(p)
    if not imgs:
        raise FileNotFoundError("没有可用的对照图片")
    sizes = []
    for p in imgs:
        with fitz.open(str(p)) as d:
            r = d[0].rect
            sizes.append((r.width, r.height))
    gap = 24
    label_h = 22
    total_w = sum(s[0] for s in sizes) + gap * (len(sizes) + 1)
    total_h = max(s[1] for s in sizes) + label_h + gap * 2
    page = doc.new_page(width=total_w, height=total_h)
    x = gap
    labels = ["原文截图 (original)", "还原 LaTeX 渲染 (restored)"]
    for i, p in enumerate(imgs):
        with fitz.open(str(p)) as d:
            pdfbytes = d.convert_to_pdf()
        imgdoc = fitz.open("pdf", pdfbytes)
        w, h = sizes[i]
        rect = fitz.Rect(x, gap + label_h, x + w, gap + label_h + h)
        page.show_pdf_page(rect, imgdoc, 0)
        imgdoc.close()
        page.insert_text(fitz.Point(x + 2, gap + 14), labels[i] if i < len(labels) else "",
                         fontsize=11, color=(0.1, 0.1, 0.6))
        x += w + gap
    if note:
        page.insert_text(fitz.Point(gap + 2, total_h - 6), note[:160], fontsize=9,
                         color=(0.35, 0.35, 0.35))
    scale = 200 / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))
    doc.close()
    return pix.width, pix.height


def render_one(work: Path, tag: str, engine: str, dpi: float, status: Dict[str, Any],
               quiet: bool = False) -> Dict[str, Any]:
    ed = eq_dir(work)
    draft = ed / "draft" / f"{tag}.tex"
    orig = ed / f"{tag}_orig.png"
    render_png = ed / f"{tag}_render.png"
    cmp_png = ed / f"{tag}_cmp.png"
    rec = dict(status.get(tag, {}))
    rec["draft_sha1"] = sha1_text(read_draft(draft)) if draft.exists() else ""
    if not draft.exists():
        rec.update({"render_ok": False, "error": "缺少草稿文件", "confirmed": False})
        if not quiet:
            print(f"  [x] {tag}: 找不到 {draft.name}")
        return rec
    body = read_draft(draft)
    if not body:
        rec.update({"render_ok": False, "error": "草稿为空", "confirmed": False})
        if not quiet:
            print(f"  [x] {tag}: 草稿为空")
        return rec
    # 符号守恒校验（不需要看图也能发现"漏了符号/多了符号"）
    chk = symbol_check(work, tag, body)
    rec["symbol_ok"] = bool(chk.get("ok"))
    rec["symbol_missing"] = chk.get("missing", {})
    rec["symbol_extra"] = chk.get("extra", {})
    rec["symbol_skipped"] = chk.get("skipped", "")
    with tempfile.TemporaryDirectory(prefix="p2z_eq_") as td:
        ok, log, pdf = compile_equation(body, engine, Path(td), tag)
        if ok and pdf is not None:
            render_pdf_to_png(pdf, render_png, dpi)
            rec.update({"render_ok": True, "error": "", "render_png": render_png.name})
            if orig.exists():
                compose_compare(orig, render_png, cmp_png, tag)
                rec["cmp_png"] = cmp_png.name
        else:
            rec.update({"render_ok": False, "error": log, "render_png": "", "cmp_png": ""})
            (ed / f"{tag}_render.log").write_text(log, encoding="utf-8")
    if not quiet:
        if rec.get("render_ok"):
            extra = f"对照图 {rec.get('cmp_png')}" if rec.get("cmp_png") else "缺少原图，无法并排比对"
            print(f"  [v] {tag}: 渲染成功；{extra}")
        else:
            print(f"  [x] {tag}: 渲染失败\n      " + str(rec.get("error", ""))[:400].replace("\n", "\n      "))
        if not rec.get("symbol_ok", True):
            print(f"  [!] {tag} 符号守恒校验不通过：原文有而还原缺失 "
                  f"{rec.get('symbol_missing')}；还原多出 {rec.get('symbol_extra')}")
    return rec


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #

def cmd_render(args: argparse.Namespace) -> int:
    work = abs_path(args.work)
    ed = eq_dir(work)
    if not ed.exists():
        print(f"[x] 找不到 {ed}，请先运行 extract.py", file=sys.stderr)
        return 2
    engine = find_tex_engine()
    if not engine:
        print("[x] 找不到 LaTeX 引擎（xelatex/pdflatex）。请安装 TeX Live 或 MiKTeX。", file=sys.stderr)
        return 2
    tags = [args.only] if args.only else [p.stem for p in draft_files(work)]
    if not tags:
        print("[x] draft/ 下没有 eq*.tex，请先运行 extract.py", file=sys.stderr)
        return 2
    status = load_status(work)
    print(f"[*] 渲染 {len(tags)} 条公式（引擎 {Path(engine).name}，DPI {args.dpi}）")

    def job(tag: str) -> Tuple[str, Dict[str, Any]]:
        rec = render_one(work, tag, engine, args.dpi, status, quiet=True)
        return tag, rec

    results: List[Tuple[str, Dict[str, Any]]] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, int(args.jobs))) as ex:
        for tag, rec in ex.map(job, tags):
            results.append((tag, rec))
    ok = bad = 0
    for tag, rec in sorted(results):
        if rec.get("render_ok"):
            ok += 1
            if not args.quiet:
                extra = f"对照图 {rec.get('cmp_png')}" if rec.get("cmp_png") else "缺少原图，无法并排比对"
                flag = "" if rec.get("symbol_ok", True) else "  [!] 符号守恒校验不通过"
                print(f"  [v] {tag}: 渲染成功；{extra}{flag}")
                if not rec.get("symbol_ok", True):
                    print(f"        原文有而还原缺失：{rec.get('symbol_missing')}"
                          f"；还原多出：{rec.get('symbol_extra')}")
        else:
            bad += 1
            print(f"  [x] {tag}: 渲染失败 -> {tag}_render.log\n      "
                  + str(rec.get("error", ""))[:300].replace("\n", "\n      "))
        prev = status.get(tag, {})
        # 草稿变了 -> 之前的确认失效
        if prev.get("confirmed") and prev.get("draft_sha1") != rec.get("draft_sha1"):
            rec["confirmed"] = False
            rec["invalidated"] = "草稿已修改，原确认失效，需重新比对"
        status[tag] = rec
    save_status(work, status)
    print(f"\n[=] 渲染完成：成功 {ok}，失败 {bad}；对照图在 {ed}")
    if bad:
        print("    失败公式会留 *_render.log，按日志修好 draft/*.tex 后重跑 render。")
    print("    下一步：逐张 read_image 查看 eqNN_cmp.png，核对无误后 confirm。")
    return 0 if bad == 0 else 1


def cmd_confirm(args: argparse.Namespace) -> int:
    work = abs_path(args.work)
    status = load_status(work)
    tag = args.tag
    if not re.fullmatch(r"eq\d{2}", tag or ""):
        print("[x] 公式编号形如 eq03", file=sys.stderr)
        return 2
    ed = eq_dir(work)
    draft = ed / "draft" / f"{tag}.tex"
    if not draft.exists():
        print(f"[x] 找不到 {draft}", file=sys.stderr)
        return 2
    rec = status.get(tag, {})
    if rec.get("draft_sha1") and rec.get("draft_sha1") != sha1_text(read_draft(draft)):
        print(f"[x] {tag} 的草稿在渲染后又改过：先重跑 render 再确认。", file=sys.stderr)
        return 2
    if not rec.get("render_ok"):
        print(f"[x] {tag} 尚未渲染成功：先跑 render。", file=sys.stderr)
        return 2
    if not rec.get("symbol_ok", True) and not args.force:
        print(f"[x] {tag} 符号守恒校验不通过（原文有而还原缺失 {rec.get('symbol_missing')}）。"
              f"\n    请先修 draft/{tag}.tex；若确认原文那部分本就不该出现（例如 PDF 文本层缺陷），"
              f"用 --force 并说明理由。", file=sys.stderr)
        return 2
    if not args.note or len(args.note.strip()) < 4:
        print("[x] confirm 必须带 --note，写明你比对的是哪些符号（留痕，便于追责）。", file=sys.stderr)
        return 2
    rec["confirmed"] = True
    rec["note"] = args.note.strip()
    rec["draft_sha1"] = sha1_text(read_draft(draft))
    status[tag] = rec
    save_status(work, status)
    print(f"[v] {tag} 已确认：{rec['note']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    work = abs_path(args.work)
    status = load_status(work)
    tags = [p.stem for p in draft_files(work)]
    if not tags:
        print("[x] 没有公式草稿；先运行 extract.py", file=sys.stderr)
        return 2
    conf = pend = fail = skipped = 0
    skip: Dict[str, str] = {}
    skf = eq_dir(work) / "skip-list.txt"
    if skf.exists():
        for ln in skf.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                parts = ln.split(None, 1)
                skip[parts[0]] = parts[1] if len(parts) > 1 else "（未写理由）"
    for tag in tags:
        rec = status.get(tag, {})
        draft = eq_dir(work) / "draft" / f"{tag}.tex"
        cur = sha1_text(read_draft(draft)) if draft.exists() else ""
        if tag in skip:
            skipped += 1
            print(f"  [s] {tag} 已登记跳过（{skip[tag]}）")
        elif rec.get("confirmed") and rec.get("draft_sha1") == cur:
            conf += 1
            print(f"  [v] {tag} 已确认  {rec.get('note','')}")
        elif not rec.get("render_ok", False) and rec.get("error"):
            fail += 1
            print(f"  [x] {tag} 渲染失败  {str(rec.get('error'))[:80]}")
        else:
            pend += 1
            why = "未渲染" if not rec.get("render_ok") else (
                "草稿已改，确认失效" if rec.get("confirmed") else "已渲染但未确认")
            sym = "" if rec.get("symbol_ok", True) else \
                f"  [!] 符号缺失 {rec.get('symbol_missing')}"
            print(f"  [ ] {tag} 待处理（{why}）  {rec.get('cmp_png','')}{sym}")
    print(f"\n[=] 合计 {len(tags)} 条：已确认 {conf}，待处理 {pend}，"
          f"渲染失败 {fail}，登记跳过 {skipped}")
    return 0 if (pend == 0 and fail == 0) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="公式还原的可视化复核工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("render", help="渲染全部/单条公式并生成原文-渲染对照图")
    p1.add_argument("--work", default="translator-work")
    p1.add_argument("--only", default="", help="只处理一条，如 eq03")
    p1.add_argument("--dpi", type=float, default=220.0)
    p1.add_argument("--jobs", type=int, default=4)
    p1.add_argument("--quiet", action="store_true")
    p1.set_defaults(func=cmd_render)

    p2 = sub.add_parser("confirm", help="登记某条公式已人工比对通过")
    p2.add_argument("tag")
    p2.add_argument("--work", default="translator-work")
    p2.add_argument("--note", default="", help="比对说明（必填）")
    p2.add_argument("--force", action="store_true",
                    help="符号守恒校验不通过时强制确认（必须在 --note 里说明原因）")
    p2.set_defaults(func=cmd_confirm)

    p3 = sub.add_parser("status", help="汇总复核状态")
    p3.add_argument("--work", default="translator-work")
    p3.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
