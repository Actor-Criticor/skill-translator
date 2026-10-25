# -*- coding: utf-8 -*-
"""公式转换用数据表：Unicode/字形 -> LaTeX。

本模块只放数据，不放逻辑，便于人工扩充（扩充后无需改动其它脚本）。
所有映射都服务于同一个目标：把 PDF 里抽取出的数学片段尽可能**无损**地
还原为 LaTeX 代码。

约定：
* SYMBOLS     单字符 -> LaTeX 数学命令（不含 $...$）
* SUP_MAP / SUB_MAP  上标/下标 Unicode 字符 -> 普通字符（再由生成器决定 {}^{} 层级）
* FUNCTIONS   数学函数名 -> 规范 LaTeX（多数应直立排版）
* UPRIGHT_WORDS 需以 \\mathrm{} 直立排版的多字母标识符/缩写
* BIG_OPS     大运算符（可能带上限/下限）
* OPEN_DELIM / CLOSE_DELIM 需要成对处理的定界符
* ITALIC_STOPWORDS 斜体但不是变量的常见词（拉丁缩写等），避免误判为数学
"""

from __future__ import annotations

# ---------------------------------------------------------------- 单字符符号
SYMBOLS = {
    # 希腊字母（小写）
    "\u03b1": r"\alpha", "\u03b2": r"\beta", "\u03b3": r"\gamma",
    "\u03b4": r"\delta", "\u03b5": r"\epsilon", "\u03f5": r"\epsilon",
    "\u03b6": r"\zeta", "\u03b7": r"\eta", "\u03b8": r"\theta",
    "\u03b9": r"\iota", "\u03ba": r"\kappa", "\u03bb": r"\lambda",
    "\u03bc": r"\mu", "\u03bd": r"\nu", "\u03be": r"\xi",
    "\u03c0": r"\pi", "\u03c1": r"\rho", "\u03c2": r"\varsigma",
    "\u03c3": r"\sigma", "\u03c4": r"\tau", "\u03c5": r"\upsilon",
    "\u03c6": r"\phi", "\u03d5": r"\phi", "\u03c7": r"\chi",
    "\u03c8": r"\psi", "\u03c9": r"\omega", "\u03d1": r"\vartheta",
    "\u03d6": r"\varpi", "\u03f1": r"\varrho", "\u03c8": r"\psi",
    # 希腊字母（大写）
    "\u0393": r"\Gamma", "\u0394": r"\Delta", "\u0398": r"\Theta",
    "\u039b": r"\Lambda", "\u039e": r"\Xi", "\u03a0": r"\Pi",
    "\u03a3": r"\Sigma", "\u03a5": r"\Upsilon", "\u03a6": r"\Phi",
    "\u03a8": r"\Psi", "\u03a9": r"\Omega",
    # 运算与关系
    "\u2212": "-", "\u00b1": r"\pm", "\u2213": r"\mp",
    "\u00d7": r"\times", "\u00f7": r"\div", "\u22c5": r"\cdot",
    "\u00b7": r"\cdot", "\u2217": "*", "\u2218": r"\circ",
    "\u2297": r"\otimes", "\u2295": r"\oplus", "\u2299": r"\odot",
    "\u2264": r"\le", "\u2265": r"\ge", "\u2260": r"\ne",
    "\u2248": r"\approx", "\u2245": r"\cong", "\u2243": r"\simeq",
    "\u2261": r"\equiv", "\u221d": r"\propto", "\u223c": r"\sim",
    "\u226a": r"\ll", "\u226b": r"\gg", "\u225c": r"\triangleq",
    "\u224d": r"\asymp", "\u2262": r"\not\equiv",
    # 集合与逻辑
    "\u2208": r"\in", "\u2209": r"\notin", "\u220b": r"\ni",
    "\u2282": r"\subset", "\u2286": r"\subseteq", "\u2283": r"\supset",
    "\u2287": r"\supseteq", "\u222a": r"\cup", "\u2229": r"\cap",
    "\u2205": r"\emptyset", "\u2200": r"\forall", "\u2203": r"\exists",
    "\u2204": r"\nexists", "\u00ac": r"\neg", "\u2227": r"\wedge",
    "\u2228": r"\vee", "\u22a4": r"\top", "\u22a5": r"\perp",
    "\u2223": r"\mid", "\u2225": r"\parallel", "\u2216": r"\setminus",
    "\u2295": r"\oplus",
    # 大运算符与微积分
    "\u2211": r"\sum", "\u220f": r"\prod", "\u222b": r"\int",
    "\u222c": r"\iint", "\u222d": r"\iiint", "\u222e": r"\oint",
    "\u2202": r"\partial", "\u2207": r"\nabla", "\u221e": r"\infty",
    "\u221a": r"\sqrt",
    # 箭头
    "\u2192": r"\to", "\u2190": r"\leftarrow", "\u2194": r"\leftrightarrow",
    "\u21d2": r"\Rightarrow", "\u21d0": r"\Leftarrow", "\u21d4": r"\Leftrightarrow",
    "\u21a6": r"\mapsto", "\u2191": r"\uparrow", "\u2193": r"\downarrow",
    # 括号与定界符（成对处理见 OPEN_DELIM/CLOSE_DELIM）
    "\u27e8": r"\langle", "\u27e9": r"\rangle",
    "\u2308": r"\lceil", "\u2309": r"\rceil",
    "\u230a": r"\lfloor", "\u230b": r"\rfloor",
    # 其它常用
    "\u2032": "'", "\u2033": "''", "\u00b0": r"^\circ",
    "\u2026": r"\dots", "\u22ef": r"\cdots", "\u22ee": r"\vdots",
    "\u22f1": r"\ddots", "\u2020": r"\dagger", "\u2021": r"\ddagger",
    "\u2113": r"\ell", "\u210f": r"\hbar", "\u2135": r"\aleph",
    "\u211d": r"\mathbb{R}", "\u2115": r"\mathbb{N}",
    "\u2124": r"\mathbb{Z}", "\u211a": r"\mathbb{Q}",
    "\u2102": r"\mathbb{C}", "\u2220": r"\angle",
    "\u222e": r"\oint", "\u25a1": r"\square", "\u25b3": r"\triangle",
    "\u2a7d": r"\leqslant", "\u2a7e": r"\geqslant",
    "\u2261": r"\equiv", "\u220b": r"\ni",
    # 文本型符号（在数学环境中仍需直立）
    "\u00a7": r"\S", "\u00b6": r"\P", "\u2020": r"\dagger",
}

# ------------------------------------------------------- 上标/下标 Unicode 映射
SUP_MAP = {
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
    "\u2074": "4", "\u2075": "5", "\u2076": "6", "\u2077": "7",
    "\u2078": "8", "\u2079": "9", "\u207a": "+", "\u207b": "-",
    "\u207c": "=", "\u207d": "(", "\u207e": ")", "\u207f": "n",
    "\u2071": "i", "\u1d43": "a", "\u1d47": "b", "\u1d48": "d",
    "\u1d49": "e", "\u1d4d": "g", "\u1d4f": "k", "\u1d52": "o",
    "\u1d56": "p", "\u1d57": "t", "\u1d58": "u", "\u1d5b": "v",
    "\u1d5d": r"\beta", "\u1d5e": r"\gamma", "\u1d5f": r"\delta",
    "\u1d60": r"\varphi", "\u1d61": r"\chi",
}

SUB_MAP = {
    "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3",
    "\u2084": "4", "\u2085": "5", "\u2086": "6", "\u2087": "7",
    "\u2088": "8", "\u2089": "9", "\u208a": "+", "\u208b": "-",
    "\u208c": "=", "\u208d": "(", "\u208e": ")", "\u2090": "a",
    "\u2091": "e", "\u2092": "o", "\u2093": "x", "\u2095": "h",
    "\u2096": "k", "\u2097": "l", "\u2098": "m", "\u2099": "n",
    "\u209a": "p", "\u209b": "s", "\u209c": "t", "\u1d62": "i",
    "\u1d63": "r", "\u1d64": "u", "\u1d65": "v",
}

# --------------------------------------------------------------- 数学函数名
FUNCTIONS = {
    "sin": r"\sin", "cos": r"\cos", "tan": r"\tan", "cot": r"\cot",
    "sec": r"\sec", "csc": r"\csc", "arcsin": r"\arcsin",
    "arccos": r"\arccos", "arctan": r"\arctan", "sinh": r"\sinh",
    "cosh": r"\cosh", "tanh": r"\tanh", "coth": r"\coth",
    "exp": r"\exp", "log": r"\log", "ln": r"\ln", "lg": r"\lg",
    "lim": r"\lim", "max": r"\max", "min": r"\min", "sup": r"\sup",
    "inf": r"\inf", "det": r"\det", "dim": r"\dim", "ker": r"\ker",
    "deg": r"\deg", "gcd": r"\gcd", "hom": r"\hom", "arg": r"\arg",
    "Pr": r"\Pr", "tr": r"\operatorname{tr}", "rank": r"\operatorname{rank}",
    "diag": r"\operatorname{diag}", "mod": r"\bmod", "erf": r"\operatorname{erf}",
    "erfc": r"\operatorname{erfc}", "sgn": r"\operatorname{sgn}",
    "var": r"\operatorname{Var}", "cov": r"\operatorname{Cov}",
    "std": r"\operatorname{std}", "median": r"\operatorname{median}",
    "mean": r"\operatorname{mean}", "Re": r"\operatorname{Re}",
    "Im": r"\operatorname{Im}", "s.t.": r"\text{s.t.}",
    "maximize": r"\text{maximize}", "minimize": r"\text{minimize}",
    "subject to": r"\text{subject to}",
}

# -------------------------------------------- 需直立排版的多字母标识符 / 缩写
UPRIGHT_WORDS = {
    "RMS", "SNR", "MSE", "RMSE", "MAE", "MAPE", "PSNR", "SSIM", "FWHM",
    "PDF", "CDF", "AUC", "ROC", "ODE", "PDE", "FFT", "DFT", "DCT",
    "IMU", "GPS", "CPU", "GPU", "TP", "TN", "FP", "FN", "CI", "SD", "SE",
    "RM", "CV", "MLE", "MAP", "KL", "JS", "TV", "SVD", "PCA", "ICA",
    "ANN", "CNN", "RNN", "LSTM", "GAN", "SVM", "KNN", "RF", "GBM",
    "ReLU", "softmax", "sigmoid", "tanh", "MLP", "IoU", "mAP",
}

# ------------------------------------------------------------------ 大运算符
BIG_OPS = {"\u2211": r"\sum", "\u220f": r"\prod", "\u222b": r"\int",
           "\u222c": r"\iint", "\u222d": r"\iiint", "\u222e": r"\oint",
           "\u22c3": r"\bigcup", "\u22c2": r"\bigcap",
           "\u2210": r"\coprod", "\u2a01": r"\bigoplus"}

# 需要 \left \right 处理的定界符
OPEN_DELIM = {"(": ")", "[": "]", "{": "}", "\u27e8": "\u27e9",
              "\u2308": "\u2309", "\u230a": "\u230b", "|": "|",
              "\u2016": "\u2016"}
CLOSE_DELIM = {v: k for k, v in OPEN_DELIM.items()}
DELIM_LATEX = {"(": "(", ")": ")", "[": "[", "]": "]",
               "{": r"\{", "}": r"\}", "|": "|",
               "\u27e8": r"\langle", "\u27e9": r"\rangle",
               "\u2308": r"\lceil", "\u2309": r"\rceil",
               "\u230a": r"\lfloor", "\u230b": r"\rfloor"}

# 允许被"吞进"数学片段的普通字符（续接用）
INLINE_CONTINUE = set("0123456789=+-,()[]{}|<>:'\"/^_")

# 斜体但不是变量（避免把 et al. / in vivo 误判为数学）
ITALIC_STOPWORDS = {
    "et", "al", "vs", "cf", "ie", "eg", "i.e", "e.g", "no", "in", "of",
    "to", "by", "on", "at", "as", "is", "an", "a", "the", "and", "or",
    "if", "it", "we", "us", "do", "so", "up", "per", "via", "sub", "ex",
    "de", "la", "le", "fig", "figs", "tab", "eq", "eqs", "ref", "refs",
    "vol", "pp", "ed", "eds", "ibid", "idem", "supra", "infra", "passim",
}

# 字体名中出现即视为数学字体的片段（正则用，大小写不敏感）
MATH_FONT_HINTS = [
    r"CMMI", r"CMSY", r"CMEX", r"CMBSY", r"CMSS", r"CMTT",
    r"MSAM", r"MSBM", r"EUSM", r"EUFM", r"rsfs",
    r"\bSymbol\b", r"MTMI", r"MTEX", r"MTSY", r"MathematicalPi",
    r"STIXMath", r"XITSMath", r"LatinModernMath", r"AsanaMath",
    r"TeXGyre\w*Math", r"Cambria\s?Math", r"Euclid", r"NimbusMath",
    r"NewCMSY", r"MathJax", r"KaTeX", r"MATHJAX",
]

# 正文（非数学）常见字体名片段，用于版面判定的字体聚类
BODY_FONT_HINTS = [r"CMR\d", r"NimbusRom", r"Times", r"Minion", r"Charter",
                   r"Palatino", r"Bookman", r"Georgia", r"Garamond",
                   r"Helvetica", r"Arial", r"Latin Modern Roman",
                   r"TeXGyreTermes", r"TeXGyrePagella", r"DejaVu"]
