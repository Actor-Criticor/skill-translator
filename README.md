# skill-translator —— 英文期刊 PDF → 中文版 LaTeX/PDF

一个可移植的技能包：把英文期刊论文 PDF 转成**可编译的中文版 PDF**，同时产出
按原文顺序命名的插图（`fig1~n.png`）、可编辑的公式 LaTeX 代码、以及一份质检报告。

- **依赖极少**：Python 3.8+ 与 PyMuPDF（解析）＋ xelatex/ctex（排版）。
  **不需要 pandoc、不需要联网、不需要任何翻译 API。**
- **跨平台**：中文字体自动探测（Windows 宋体/黑体 → macOS Songti/Heiti → Linux Noto/思源 →
  TeX Live 自带 Fandol 兜底），生成的中文 `.tex` 带 UTF-8 BOM，避免 Windows 上 XeTeX 按本地代码页读取导致乱码。
- **公式有硬门禁**：每条行间公式都要经过「原文截图 ↔ 重排渲染」对照 + **符号守恒机器校验**，
  逐条 `confirm` 才能交付；质检还会比对全文数字、引用、图表与漏译情况。

---

## 一、安装

DSH 的技能发现顺序（靠前者优先）：
`<项目>/.dsh/skills` → `<项目>/.agents/skills` → **`~/.dsh/skills`（用户级：任意工作目录都可用）** → `~/.agents/skills`。

### 方式 0：从 GitHub 一键装
```powershell
git clone https://github.com/Actor-Criticor/skill-translator
cd skill-translator
python install.py --force      # 装到 ~/.dsh/skills/skill-translator，全工作域可用
```

### 方式 A：装到用户级（**全工作域可用**，推荐）
```powershell
python <skill>/install.py            # 复制到 C:\Users\<你>\.dsh\skills\skill-translator
python <skill>/install.py --force    # 覆盖更新（改过源之后用它同步）
python <skill>/install.py --dry-run  # 只看会做什么
python <skill>/install.py --target "D:\my\skills"   # 装到自定义 skills 根目录
```
装好后**任何目录**下的新会话都能调用本技能（已实测：在 `D:\...\other_project` 下用 C 盘副本
跑完整流水线，成功产出中文 PDF）。

### 方式 B：只在某个工作区用
把技能目录放到该项目的 `<项目>/.dsh/skills/skill-translator/` 即可，DSH 自动发现。

> **维护约定**：把技能目录当作*源*（在这里改脚本、跑 `tests/selftest.py`），
> 改完执行一次 `python install.py --force` 把改动部署到用户级目录。
> 同一工作区若同时存在项目级与用户级两份，**项目级会遮蔽用户级**（内容相同则无影响）。

### 依赖
```powershell
python <skill>/scripts/env_check.py --install-deps
```
脚本会检查并给出分平台指引：
- **Python + PyMuPDF**：`python -m pip install pymupdf`
- **TeX**：Windows/macOS 装 TeX Live（或 MiKTeX）；Debian/Ubuntu `sudo apt install texlive-xetex texlive-lang-chinese texlive-latex-extra`；
  Fedora `sudo dnf install texlive-xetex texlive-collection-langchinese`
- **中文字体**：Windows/macOS 系统自带；Linux `sudo apt install fonts-noto-cjk`；
  实在没有也会用 TeX Live 自带的 Fandol 字体兜底

## 二、最短上手路径

```powershell
# 0) 自检
python <skill>/scripts/env_check.py --work mywork

# 1) 提取：图 / 表 / 公式 / 可翻译源文
python <skill>/scripts/extract.py "论文.pdf" --out mywork --keep-source

# 2) 看 figures/ 里的 fig1~n.png 对不对；看 tables/ 走的是三线表还是截图

# 3) 公式：渲染 + 生成"原文↔渲染"对照图 + 符号守恒校验
python <skill>/scripts/equations.py render --work mywork
python <skill>/scripts/equations.py status  --work mywork
#    逐条比对 eqNN_cmp.png（或 eqNN_orig.png），改 draft/eqNN.tex 后重渲，再确认：
python <skill>/scripts/equations.py render  --work mywork --only eq03
python <skill>/scripts/equations.py confirm eq03 --work mywork --note "与原图逐符号一致"

# 4) 写中文译稿 mywork/paper/paper.md（格式见 SKILL.md 第四节 / 下面示例）

# 5) 编译中文版 PDF（首次自动探测可用字体集）
python <skill>/scripts/build.py --work mywork

# 6) 质检（13 项门禁，FAIL 必须清零）
python <skill>/scripts/verify.py --work mywork
```

## 三、译稿样例（`mywork/paper/paper.md`）

```markdown
---
title_zh: 面向 PDF 到 LaTeX 自动转换的双栏回归样例
title_en: A Two-Column Regression Sample for Automated PDF-to-LaTeX Conversion
authors: A. B. Author, C. D. Coauthor
venue: J. Test Phys.
year: "2024"
doi: 10.0000/jtp.2024.345
source_pdf: sample_en.pdf
keywords: PDF 转 LaTeX；双栏版面；回归测试
---

## 摘要
……（整段）

# 1 引言
……行内公式照抄：$\eta \approx 19.8$ …… [1, 2]

⟦EQ01⟧

## 2.1 数值模型
……定积分给出

⟦EQ05⟧

⟦TABLE01|表1. 五个试样的标称特性与实测特性⟧

⟦FIG01|图1. 探测器响应随时间变化的实测曲线与参考曲线⟧

# 参考文献
[1] A. B. Author, Document layout analysis for scientific PDF files, J. Test Phys. 10 (2022) 101–118.
```

占位符只有三种，**必须原样保留**（编号对应 `extracted/` 下的文件）：
`⟦EQnn⟧`、`⟦FIGnn|中文图题⟧`、`⟦TABLEnn|中文表题⟧`。

## 四、产物

| 文件 | 说明 |
|---|---|
| `paper/paper.pdf` | **主交付**：中文版 PDF |
| `paper/paper.tex` | 可继续编辑的 LaTeX 源（改版式改 `references/preamble-zh.tex`） |
| `paper/质检报告.md` | 13 项质检结果与处理指引 |
| `extracted/figures/fig1~n.png` | 按原文顺序命名的插图 |
| `extracted/tables/tableNN.tex` \| `.png` | 重排的三线表（或截图兜底） |
| `extracted/equations/draft/eqNN.tex` | 每条行间公式的 LaTeX 代码（复核确认版） |
| `extracted/equations/eqNN_orig.png` \| `eqNN_cmp.png` | 公式原文截图与对照图 |
| `extracted/source/source.md` | 可翻译源文（含占位符） |

## 五、自测

```powershell
python <skill>/tests/selftest.py
```
用附带的 `tests/sample_en.pdf`（三页双栏英文样张，含 8 条编号公式、2 图、1 表、6 条文献）
跑完整流程并做 43 项断言（提取计数、公式渲染与确认、编译、中文渲染、数字一致性…）。
**改动任何脚本后都应重跑**，应全部通过。

## 六、已知限制（务必如实告知使用者）

1. **公式是"机器草稿 + 人工确认"**：还原器覆盖分式/根式/上下限/上下标/矩阵/分段/多行对齐等常见结构，
   但嵌套复杂、多行对齐、PDF 文本层缺字形的情况必须人工按 `eqNN_orig.png` 修；
   没有视觉能力的模型只能靠 `eqNN_cmp.png` 之外的符号守恒校验与人工核对，务必逐条确认。
2. **图片内部文字不翻译**：图沿用原图，所以中文版里图上的英文仍在（这是设计选择，不重绘、不擦字）。
3. **表格复杂结构**可能退化为截图；需要可编辑表格时人工重写 `tableNN.tex`。
4. **三栏/横排页/极端版式**的阅读顺序可能需要人工抽查。
5. **译文页数**通常多于原文（中文信息密度与行距不同），页数不作为验收标准。
6. **少数数字会被 PDF 文本层切碎**：若原 PDF 把上标排在基数之后，`10^{-3}` 在文本层里是
   `1` + `^{-3}` + `0`，源文侧无法复原成 `10`（截图里有）。质检 F6 会报出这类差异，
   核实后用 `paper/verify-allowlist.txt` 登记并写明理由即可。
7. **路径处理不依赖 `Path.resolve()`**：目标环境（Windows + Anaconda Python 3.9）上
   `Path("x").resolve()` 对不存在的路径会返回相对路径，脚本统一改用 `scripts/p2zpaths.abs_path()`。

## 七、目录结构

```
skill-translator/
├── SKILL.md                     技能主文档：流程、契约、红线、自查清单
├── README.md                    本文件（安装与上手）
├── install.py                   安装到用户级 skills 目录
├── scripts/
│   ├── env_check.py             环境自检 + 安装指引
│   ├── extract.py               PDF → 图/表/公式/源文
│   ├── equations.py             公式渲染 / 对照 / 确认 / 状态
│   ├── build.py                 译稿 MD → LaTeX → PDF（字体集探测）
│   ├── verify.py                13 项质检门禁
│   ├── p2zlib.py                版面/文本/公式分析库
│   ├── mathmap.py               Unicode ↔ LaTeX 符号表
│   └── p2zconsole.py            控制台输出编码统一
├── references/
│   ├── translation-rules.md     学术翻译规范（翻译前必读）
│   ├── extraction-rules.md      提取原理与人工复核清单
│   └── preamble-zh.tex          中文版 LaTeX 模板
└── tests/
    ├── sample_en.tex/.pdf       英文回归样张
    ├── fixture_zh.md            中文译稿样例（自测用）
    └── selftest.py              端到端自测（43 项断言）
```
