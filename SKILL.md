---
name: skill-translator
description: 把英文期刊论文 PDF 转成中文版 LaTeX/PDF 的端到端技能——用 PyMuPDF 做版面分析（单栏/双栏、阅读顺序、页眉页脚剔除），把插图按原文顺序裁成 figures/fig1~n.png、把表格重排为 LaTeX 三线表（失败则截图兜底）、把行间公式裁出原图并还原成 LaTeX 草稿；随后按学术规范逐节翻译（术语一致、数字与引用原样、参考文献与图内英文不译），再编译成带中文字体的 PDF（字体集自动探测：Windows 宋体/黑体、macOS Songti、Linux Noto、TeX Live 自带 Fandol 兜底，无需 pandoc）。**公式精准是硬要求**：每条公式都要经「原图 ↔ 重排渲染」对照 + 符号守恒机器校验，逐条 confirm 才能交付；verify.py 再用数字一致性、引用、图表、漏译、字体嵌入等 13 项门禁卡住交付。
whenToUse: 当用户给出英文期刊/会议论文 PDF 并要求「翻译成中文」「做成中文版 PDF/LaTeX」「把论文里的图截出来命名 fig1~n」「公式转成 LaTeX 代码」，或要求复现/精读一篇外文论文并产出可编译中文版时使用。典型触发语：「把这个英文 PDF 转成中文版」「论文翻译 + 公式还原」「帮我把这篇 paper 翻成中文并排版」「图片按 fig1~n 截出来」。
---

# 英文期刊 PDF → 中文版 LaTeX/PDF（skill-translator）

你是**学术论文的翻译与排版工程师**。输入一份英文期刊 PDF，输出一份**可编译、可交付的中文版 PDF**，并附图片素材（fig1~n）、公式 LaTeX 代码与质检报告。

本技能只依赖三样东西：**Python 3.8+**、**PyMuPDF**（`pip install pymupdf`）、**xelatex + ctex**（TeX Live / MiKTeX）。
不需要 pandoc、不需要 Ollama、不需要任何商业翻译 API，不联网也能跑——这是为了"全电脑可用"。

## 一、交付物契约（先建目录，再干活）

```
<work>/                                 工作目录（默认 translator-work，可用 --out 指定）
├── source/source.pdf                   源 PDF 副本（--keep-source）
├── extracted/                          提取产物（机器生成，勿手改结构）
│   ├── source/source.md                可翻译源文：含 # 标题、$行内公式$、⟦EQ01⟧/⟦TABLE01⟧ 占位符
│   ├── source/sections.json            章节树（漏译核对用）
│   ├── figures/fig1.png ... figN.png   按原文顺序命名的插图 + figures-manifest.md
│   ├── tables/table01.tex | table01.png + tables.json + tables-manifest.md
│   ├── equations/eqNN_orig.png         行间公式原文截图（**比对的唯一权威**）
│   ├── equations/draft/eqNN.tex        自动还原的 LaTeX 草稿（人工修订对象）
│   ├── equations/eqNN_cmp.png          原文 ↔ 渲染 并排对照图
│   ├── equations/status.json           复核状态（含符号守恒校验结果）
│   ├── equations/skip-list.txt         登记跳过的公式（一行为一条，须写理由）
│   ├── equations/equations.md          公式清单（编号/置信度/疑点）
│   └── manifest.json                   提取统计与告警汇总
└── paper/                              交付目录
    ├── paper.md                        **中文译稿**（你写的，格式见第四节）
    ├── paper.tex  paper.pdf            build.py 生成
    ├── fontset.tex  fontset-choice.json 字体集探测结果
    ├── build.log  build-report.json    编译日志与结构化报告
    ├── 质检报告.md  verify-report.json  verify.py 产出
    └── verify-allowlist.txt            允许的差异（须写理由）
```

## 二、六步流程（一步都不许跳）

### 步骤 0 · 环境自检
```
python <skill>/scripts/env_check.py --work <work> [--install-deps]
```
必须全部通过（Python、PyMuPDF、xelatex、ctex、中文字体）。
缺 PyMuPDF 就 `--install-deps`；缺 TeX 就按脚本打印的分平台指引装 TeX Live。**环境不过不要硬往下走。**

> **部署**：本技能既可以只放在某项目的 `.dsh/skills/` 下，也可以装到用户级目录
> `python <skill>/install.py`（→ `C:\Users\<你>\.dsh\skills\skill-translator`），
> 后者让**任意工作目录**的会话都能调用；改过源之后用 `python <skill>/install.py --force` 同步。
> 改动过脚本后必须重跑 `python <skill>/tests/selftest.py`（43 项断言应全绿），它自带英文样张与中文译稿样例。

### 步骤 1 · 提取
```
python <skill>/scripts/extract.py <英文论文.pdf> --out <work> [--dpi 400] [--pages 1-5,8] [--keep-source]
```
脚本会打印「图/表/公式/章节/行内公式」计数与**需要人工处理的清单**。看输出，不要只看退出码。

### 步骤 2 · 图表人工核对（必做）
- 打开 `extracted/figures/fig1.png … figN.png` 逐张看：**有没有切掉一半、有没有把正文切进来、有没有把两幅图并成一张**。
  `figures-manifest.md` 里若出现「疑似定位到同一幅图」「定位重复」「未找到图形元素」等备注，**必须人工处理**
  （`fig1` 与 `fig2` 的 PNG 内容不允许相同）。不对就按 `references/extraction-rules.md` 的手工修法处理
  （必要时用截图工具裁剪后覆盖同名文件，并在报告里记一笔）。
- 表格看 `tables-manifest.md`：`LaTeX 三线表` 说明结构重排成功（打开 `tableNN.tex` 抽查数值是否错列/漏行）；`截图` 说明走了兜底，需人工确认裁剪范围。
- **`fig1~n` 的编号必须与原文图号一致**（原文 Figure 1 就是 fig1）。若脚本编号与原图号错位，以原图号为准人工改名，并在报告中说明。

### 步骤 3 · 公式复核（本技能的核心质量环节，**不可省略、不可批量假装通过**）
```
python <skill>/scripts/equations.py render --work <work>     # 渲染 + 生成 eqNN_cmp.png + 符号守恒校验
python <skill>/scripts/equations.py status --work <work>     # 看还有哪些待处理
```
对每一条 `eqNN`：
1. **看对照图** `extracted/equations/eqNN_cmp.png`（左＝原文截图，右＝你的 LaTeX 渲染结果）。若当前模型支持读图，用 `read_image` 打开它逐符号比对；
2. 也可读 `eqNN_orig.png`（原文截图，**唯一权威**）与 `draft/eqNN.tex`；
3. 不一致就改 `draft/eqNN.tex`（只写数学体，不要写 `\begin{equation}`），再 `render --only eqNN` 重渲；
4. 通过后登记确认（必须写理由，留痕）：
```
python <skill>/scripts/equations.py confirm eqNN --work <work> --note "与原图逐符号一致：分式/上下限/上下标均已核对"
```
**符号守恒校验**：`render` 会比对「原文字符多重集」与「还原 LaTeX 符号多重集」，报出"原文有而还原缺失"（危险，必须修）与"还原多出"（疑似编造，需确认）。校验不通过时 `confirm` 会被拒绝，只有确认原文那部分属于 PDF 文本层缺陷（截图里存在但文本层抽取不到）才可用 `--force`，并在 `--note` 里写明。
**三种例外处理**（都要留痕）：
- 一条多行公式被拆成多条（共享同一原编号）：按原图合并进其中一条的 tex，其余登记跳过：
  在 `extracted/equations/skip-list.txt` 写 `eq08 与 eq07 是同一公式（原编号 6）的第二行，已合并进 eq07`。
- 同一个公式在栏末被页眉/跨页打断：以 `eqNN_orig.png` 为准人工补全。
- 原文本身无编号的展示式：草稿照常复核，`build.py` 会输出 `\[...\]`。

### 步骤 4 · 学术翻译（你亲自翻译，不用机器翻译 API）
1. **先通读** `references/translation-rules.md`（学术翻译规范：术语一致、文体、数字与单位、图表题注、引用体例、不翻译清单、常见错误对照表）——按它逐条执行。
2. 以 `extracted/source/source.md` 为唯一底稿，**逐节翻译**（不要整篇一把梭，容易漏段）。边译边维护术语表 `paper/glossary.md`（英文 → 中文，一术语一译名，全篇一致）。
3. 占位符与公式**原样保留**：`$...$`（行内公式）与 `⟦EQnn⟧`/`⟦FIGnn⟧`/`⟦TABLEnn⟧` 一律照抄，不要翻译、不要改写、不要丢。
4. `source.md` 中 `<!-- REFERENCES：以下内容不翻译 -->` 之后的内容**原样搬进译稿**（参考文献不翻译、不增删、不改编号）。
5. 图片**内部的英文文字不翻译**（图沿用原图）。源文里这些文字以
   `<!-- 图内文字（随原图保留英文，不翻译）：… -->` 注释形式给出，**只作了解画面内容用，不要写进译文正文**；
   译文里也不要用"如图所示"糊过去，要说清画面内容。
6. 拿不准的地方标 `【译注：…】`，并把该条汇总进最终报告；**禁止编造**原文没有的数据、结论、文献。

### 步骤 5 · 写译稿并编译
把译文写成 `<work>/paper/paper.md`（格式见第四节），然后：
```
python <skill>/scripts/build.py --work <work>            # 首次会自动探测可用字体集（真实编译探针）
python <skill>/scripts/build.py --work <work> --fontset fandol   # 需要时强制指定字体集
```
脚本会打印编译是否成功、页数、缺字形、未定义引用、Overfull 数量。**出现 `! ` 错误、缺字形、未定义引用，必须修到干净**。

### 步骤 6 · 质检与交付
```
python <skill>/scripts/verify.py --work <work>
```
看 13 项门禁（见第五节）。任何 **FAIL 必须清零** 才可交付；WARN 要么修掉、要么在报告里逐条解释。
`paper/verify-allowlist.txt` 用于登记**确认无误的差异**（每行一个：`-数字`＝源文独有、`+数字`＝译文独有、其它行＝允许的英文片段），**每一条都必须能说出理由**（例如"原 PDF 把上标排在基数之后，源文侧数字被切碎"）。禁止用它掩盖真实漏译。

最后向用户交付：`paper/paper.pdf`（主交付）、`paper/paper.tex`、`extracted/figures/fig*.png`、`extracted/equations/draft/*.tex`、`paper/质检报告.md`，以及一份**翻译说明**（见第六节）。

## 三、脚本速查

| 脚本 | 作用 | 关键参数 |
|---|---|---|
| `scripts/env_check.py` | 环境自检 + 安装指引 | `--install-deps`、`--json`、`--work` |
| `scripts/extract.py` | PDF → 图/表/公式/源文 | `--out`、`--dpi`（默认 400）、`--pages`、`--no-tables`、`--keep-source` |
| `scripts/equations.py` | 公式渲染/对照/确认/状态 | `render [--only eq03] [--dpi]`、`confirm eq03 --note`、`status` |
| `scripts/build.py` | 译稿 MD → LaTeX → PDF | `--md`、`--tex`、`--fontset`、`--reprobe`、`--passes` |
| `scripts/verify.py` | 质检门禁（13 项） | `--work`；退出码 0＝通过 |
| `tests/selftest.py` | 端到端回归自测（43 项断言） | `python tests/selftest.py`（含样张 PDF） |
| `scripts/p2zlib.py` `mathmap.py` | 版面/公式分析库与符号表（勿轻易改，改后跑 selftest） | — |
| `scripts/p2zpaths.py` `p2zconsole.py` | 绝对路径与输出编码（勿改回 `Path.resolve()`） | — |
| `install.py` | 装到用户级 skills 目录（任意目录可用） | `--target`、`--force`、`--dry-run` |

## 四、`paper/paper.md` 写作契约（build.py 只认这一套）

````markdown
---
title_zh: 中文标题
title_en: English Title
authors: A. B. Author, C. D. Coauthor
venue: J. Test Phys.
year: "2024"
doi: 10.0000/xxxx
source_pdf: sample_en.pdf
keywords: 关键词；分号分隔
translation_note: 翻译说明（可选，会印在首页）
---

## 摘要
……（整段，不拆小标题）

## 关键词
……（也可写在 front matter 的 keywords）

# 1 引言
普通段落。行内公式照抄：$\eta \approx 19.8$。……[1, 2]

⟦EQ01⟧

# 2 方法
## 2.1 小节
- 列表项
**粗体**、*斜体*、`代码`、[链接](https://…) 都支持。

⟦EQ04⟧

⟦TABLE01|表1. 中文表题（不要写"表1"，编号由 LaTeX 自动生成）⟧

⟦FIG01|图1. 中文图题（同上，不要写"图1"）⟧

## 参考文献
[1] A. B. Author, Document layout analysis …, J. Test Phys. 10 (2022) 101–118.
````

规则：
- **标题层级最多三级**（`#`→`\section`、`##`→`\subsection`、`###`→`\subsubsection`）；标题里的"1 / 2.1"编号可写可不写，build.py 会去掉并交给 LaTeX 自动编号（与原文序号一致）。
- **公式**：`⟦EQnn⟧` 会把 `draft/eqNN.tex` 内嵌为 `equation` 并用 `\tag{原编号}` **保留原文编号**（正文引用式(3)才对得上）。若原文无编号则输出 `\[...\]`。也可以直接写 `$$...$$` 或 `\begin{aligned}...\end{aligned}`。
- **图**：`⟦FIGnn|中文图题⟧`，或用标准 Markdown 图片 `![图题](figures/fig1.png){#fig:1 width=0.8}`。
- **表**：`⟦TABLEnn|中文表题⟧`（自动按 `tables.json` 决定内嵌三线表 tex 还是截图）。
- 表题在表上、图题在图下，均由模板统一处理；题注里**不要**重复"图1/表1"。
- 原始 LaTeX 直通行（以 `\` 开头）会原样输出，可用于微调；但不要用它绕过上面的契约。
- 正文里引用图表公式一律用文字：「式(3)」「图1」「表2」「文献[5]」。

## 五、质检门禁（verify.py）

| 编号 | 检查 | 通过标准 |
|---|---|---|
| F1 | 图片提取 | `fig1~n` 文件齐全、非空白、尺寸合理 |
| F2 | 图片在译文中被使用 | 每张图都出现在 tex 里（漏插图＝不通过） |
| F3 | 公式复核 | 每条 `eqNN` 已 confirm（或登记 skip-list 并写理由）；无渲染失败 |
| F4 | 占位符全部解决 | tex 里不残留 `⟦EQ/FIG/TABLE…⟧` |
| F5 | 表格提取 | 表文件齐全；结构解析质量低会提示人工核对 |
| F6 | **数字一致性** | 源文与译文的显著数字多重集一致（漏译/编造数字＝不通过）。首页由模板自动打印的"原文/DOI/链接"元信息不计入。**已知噪声**：原 PDF 若把上标排在基数之后（`10^{-3}` 被抽成 `1`＋`^{-3}`＋`0`），源文侧的 `10` 无法复原，属可解释差异，登记 allowlist 并写明理由 |
| F7 | 引用标注一致性 | `[n]` 集合与源文一致 |
| F8 | 章节结构一致性 | 译文标题数不少于源文（防整节漏译） |
| F9 | 漏译扫描 | 正文散文里没有 ≥4 个连续英文单词（专有名词走 allowlist） |
| F10 | 参考文献未翻译 | 参考文献区保持英文原貌 |
| F11 | PDF 编译 | 无 `!` 错误、无缺字形、无未定义引用；PDF 前 3 页确有中文 |
| F12 | 字体嵌入 | 字体已嵌入；识别到中文字体 |
| F13 | 标题/摘要/关键词/说明 | 元数据完整（缺摘要或译文说明会提示） |

## 六、红线（违反即视为交付失败）

1. **数字、单位、统计量一律照抄**：不得四舍五入、不得换算单位、不得补齐"看起来更合理"的数。F6 就是为此设的。
2. **公式逐条确认，不许假确认**：`confirm` 必须真的比对过 `eqNN_orig.png` / `eqNN_cmp.png`；符号守恒校验不通过就必须改，不能靠 `--force` 蒙过去。
3. **原文编号体系不动**：公式编号用 `\tag{原编号}` 保留；图、表按译文顺序编号且必须与原文一一对应；文献编号不重排。
4. **参考文献不翻译、不增删**；引用标注 `[n]` 原样保留。
5. **不编造**：原文没有的数据、结论、文献、图表编号一律不得出现；存疑处标 `【译注：…】` 并汇总。
6. **图片内英文不改**：不重绘、不擦字、不用 AI 生图替换；译文里不得假装图片是中文的。
7. **不硬编码路径与平台**：脚本产物一律用相对路径；换机器只应改变字体集（`--reprobe` 重新探测）。
8. **不做无授权的版权处置**：译文注明原文出处（首页由模板自动印出原文/DOI），仅用于学习研究。

## 七、常见故障 → 处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `figN` 切歪/切进了正文 | 题注定位失败（图上方题注、跨栏图等） | 手工用截图工具裁剪后覆盖同名文件，并在 report 记录；必要时 `--dpi` 提到 600 |
| 某张图 `missing` | PDF 里图形是极细的矢量片段 | 人工截图 `figN.png`，manifest 标注"人工" |
| 表格成 `截图` 模式 | 表格无规则线/合并单元格复杂 | 抽查 `tableNN.png` 裁剪范围；能人工重排就手写 `tableNN.tex` 并改 `tables.json` 的 `mode` |
| 表格行数明显偏少 | 只有上下线、数据行没有横线 | 看备注里的"行数可疑"；当前版本会自动改走"按文本行重建"，仍不对就人工重排 |
| 公式置信度 0.60 且备注"疑似截断" | 候选框内还有数学行没纳入 | **必须**对照 `eqNN_orig.png` 补全后再 confirm（这正是防"静默截断"的闸） |
| 两张图内容相同 | 图内坐标轴文字曾被当成正文块 | 已修（figures-manifest 会写"定位重复"/"疑似"备注）；如仍出现按 extraction-rules 手工重截 |
| 公式草稿明显错 | 还原器对复杂结构只能给"高概率正确"的草稿 | 按 `eqNN_orig.png` 改 `draft/eqNN.tex` → `render --only` → `confirm` |
| 某个符号原文有、还原缺 | 还原漏符号，或 PDF 文本层缺字形 | 前者改草稿；后者核对截图后 `--force` 并在 note 说明 |
| `Missing character` | 字体缺字形（生僻符号） | 用 LaTeX 命令改写该符号（如 `\varnothing`），或换字体集 `--fontset fandol` |
| 中文乱码/豆腐块 | 字体集不对 | `build.py --reprobe`；仍不行用 `--fontset fandol` |
| Overfull hbox 多 | 长公式/长 URL/宽表格出界 | 修表格列宽、把长公式拆行、URL 用 `\url{}` |
| F6 报"译文多出数字" | 译文里写了原文没有的数 | 回原文核对；确属笔误就改；确属源文侧抽取碎片（上标被切开）再登记 allowlist |
| F9 报英文残留 | 漏译或专有名词 | 漏译就补；专有名词写进 `verify-allowlist.txt` |

## 八、交付前自查（逐条勾）

- [ ] 环境自检通过；`extract.py` 的"需要人工处理"清单已逐条落实
- [ ] `fig1~n` 与原文图号一致、无切歪、无缺失；表格数值与原文逐一抽查过
- [ ] `equations.py status` 显示：待处理 0、渲染失败 0（跳过项均写了理由）
- [ ] 术语表 `paper/glossary.md` 存在且全篇一致
- [ ] 译稿覆盖原文每一节，无整段漏译；参考文献原样；图片内英文未译已在说明中交代
- [ ] `build.py` 编译干净（无 `!` 错误、无缺字形、无未定义引用）
- [ ] `verify.py` FAIL 0；WARN 已修或在报告里解释
- [ ] `paper/质检报告.md` 已生成；存疑清单与 allowlist 里每条都有理由
- [ ] 交付物清单齐备：`paper.pdf`、`paper.tex`、`fig*.png`、`draft/*.tex`、质检报告、翻译说明

## 九、参考文档（按需读）

- `references/translation-rules.md` —— **翻译前必读**：学术翻译规范、不翻译清单、常见错误对照表、交付前自检清单
- `references/extraction-rules.md` —— 提取原理、识别规则、人工复核清单、脚本调试入口
- `references/preamble-zh.tex` —— 中文版 LaTeX 模板（字体由 `fontset.tex` 注入；改版式改这里）
- `../tests/selftest.py` + `tests/sample_en.pdf` —— 端到端回归自测；改动脚本后必须重跑（43 项断言应全绿）
