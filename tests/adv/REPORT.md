# skill-translator 对抗性端到端测试报告

**测试日期**：2026-09-20（本机时间 15:40–16:05）
**被测技能**：`D:\Desktop\DSH_work\SKILLs_creat\.dsh\skills\skill-translator\`
**测试者定位**：独立对抗性测试，只新建测试文件、**未修改任何技能脚本**
**本报告所有输出均来自真实运行**；无法目视核对的项已在第 6 节明确标注

---

## 0. 重要前提：被测对象在测试期间被并发修改

测试开始时与结束时，技能包内容**不一致**（有另一进程在同一目录工作）。文件时间戳：

| 文件 | 修改时间 | 说明 |
|---|---|---|
| `scripts/p2zlib.py` | 15:26:55 | 测试前已存在 |
| `scripts/p2zconsole.py` | 15:29:47 | |
| `scripts/p2zpaths.py` | **15:45:20** | 测试开始时**不存在**，测试期间新增 |
| `install.py` | **15:45:43** | 测试开始时不存在 |
| `references/extraction-rules.md` | **15:43:05** | 测试开始时不存在（但当时加载的 `SKILL.md` 已引用它） |
| `scripts/env_check.py` | 15:47:09 | |
| `scripts/extract.py` | 15:53:37 | |
| `scripts/build.py` | 15:54:07 | |
| `scripts/equations.py` | 15:54:07 | |
| `scripts/verify.py` | 15:54:56 | |
| `tests/_work/`、`tests/verify_sample.py`、`tests/debug_*.py`、`tests/*.log` | 测试期间被删除 | 取而代之出现 `tests/mywork/` |

因此我对技能包做了一次**冻结快照**，本报告的问题清单以快照版本为准（可复现），并标注哪些问题在快照版本上仍然存在：

```
快照：tests/adv/snapshot_v1/          哈希清单：tests/adv/snapshot_v1/SNAPSHOT.md
scripts/build.py        sha1 2c17b2bfa9312da2
scripts/env_check.py    sha1 6ab7753dc99f7e04
scripts/equations.py    sha1 2b16036c0242316d
scripts/extract.py      sha1 6fdc0d2373704a32
scripts/mathmap.py      sha1 973e6a74e5863c21
scripts/p2zconsole.py   sha1 16e8f111c6996ae4
scripts/p2zlib.py       sha1 5810793351da1b8e
scripts/p2zpaths.py     sha1 1633e1ff1abc6732
scripts/verify.py       sha1 adc4aca67a01c99b
```

**已在测试期内被修掉的一条**（我从 15:49 与 15:52 两次运行的差异中观察到）：`verify.py` 的源文侧数字归一化曾用 `s.replace("$","")`（把 `to$5.6` 粘成 `to5.6`，被数字正则左边界否掉），造成 F6 报 4 条假"译文多出"（`1.2 / 1.87 / 2.2 / 3.4`）；快照版已改为 `drop_math_dollars()`（`$` 左侧是字母时换成空格），同一 work 目录下 F6 从 `30 个 / 译文 34 个 / 多出 5 个` 变为 `34 / 34 / 多出 1 个`。**该问题已修复，不再计入问题清单。**

**快照之后技能包又被改了一次**：`extract.py`（`6fdc0d23…`→`af9c5931…`）、`verify.py`（`adc4aca6…`→`e699e289…`）、`SKILL.md`、`README.md`、`references/extraction-rules.md` 均再次变化。我在最新活代码上把我们的样张**完整重跑了一遍**（`tests/adv/work3`），逐条复核每个问题是否仍存在，结果见 **第 8 节**。第 3 节的问题清单以**快照版本**为准（可复现），每条的最新状态在第 8 节汇总。

---

## 1. 我造的样张

### 1.1 文件与编译

| 项 | 值 |
|---|---|
| 源文件 | `tests/adv/adv_sample.tex`（单栏、A4、`mathptmx` Times 正文 + CM 数学、`tikz` 画图） |
| PDF | `tests/adv/adv_sample.pdf`，**4 页**，43 445 字节 |
| 编译命令 | `xelatex -interaction=nonstopmode adv_sample.tex`（跑 2 遍） |
| 真实输出 | `Output written on adv_sample.pdf (4 pages).`；`^!` 无匹配（无错误）；`Overfull/Underfull` 无 |

与现有 `tests/sample_en.pdf` 的差异（刻意选相反结构）：

| 维度 | 现有样张 | 本样张 |
|---|---|---|
| 排版 | 双栏 | **单栏** |
| 字体 | Computer Modern，正文全 ASCII | **mathptmx（Times 正文 + CM 数学），含连字/非 ASCII** |
| 图 | `\includegraphics` 栅格图，图内无文字 | **tikz 矢量图，图内含坐标轴/图例英文** |
| 图题注 | 图下 | **图 1 题注在图上方**；图 2 题注在图下方；图 3 整幅宽、题注在上 |
| 表 | 三线表（booktabs） | **表 1 带竖线网格表**；**表 2 完全无边框** |
| 公式 | 9 条，结构简单 | 6 条编号公式：含嵌套 `\frac`、`\sum` 上下限、`\left(\right)`、`cases`、`\int`，另有带上下标的行内公式 `$\eta_{i}^{2}\approx 19.8$` |
| 参考文献标题 | 有编号 | **`\section*{References}` 无编号** |
| 易误判正文 | — | 段首 `Figure 3 shows …`、`Table 2 lists …`、`$10^{-3}$`、`$p<0.05$`、`12.4 ± 0.8`、`2.2%`、DOI URL |

### 1.2 结构与真实分页（`python tests/adv/dump_blocks.py adv_sample.pdf` 节选）

```
PAGE 1  title/authors/abstract/keywords/§1 Introduction/式(1)
PAGE 2  图1 题注(y63)→图1 图形(y100-230)→表1 题注(y244)→表1(y280-362)
        →§2、§2.1、式(2)(3)(4)(5)(6)
PAGE 3  §2.2 参数块（用 \qquad 排成"表格状"文字）→§2.3→§3、§3.1→
        正文"Figure 3 shows …"/"Table 2 lists …"→图2 图形→图2 题注(y552)
        →表2 题注(y598)→表2(y633-701)→§3.2
PAGE 4  图3 题注(y63)→图3 图形(整幅宽)→§3.2 正文→致谢→References
```

### 1.3 我用的中文译稿

`tests/adv/work/paper/paper.md`（6 478 字节）：front matter + `## 摘要` + `## 关键词` + 5 个 `#` 级节 + 6 个 `##` 小节 + `⟦EQ01⟧…⟦EQ06⟧` + `⟦FIG01…03|中文图题⟧` + `⟦TABLE01/02|中文表题⟧` + 英文参考文献原样照抄。数字逐项照抄原文。

---

## 2. 各脚本执行记录（真实命令与输出）

工作目录两套：`tests/adv/work`（首次运行，用当时的活代码）、`tests/adv/work2`（**冻结快照**，报告主证据）。

```
$ python snapshot_v1/scripts/env_check.py --work tests/adv/work2
[v] Python      : Python 3.9.13（满足）
[v] PyMuPDF     : PyMuPDF 1.26.5；表格识别 find_tables=可用
[v] LaTeX 引擎  : xelatex.EXE -> D:\texlive\2026\bin\windows\xelatex.EXE
[v] ctex        : ctex 宏包可用（ctexart.cls / ctex.sty）
[v] Fandol 兜底 : Fandol 兜底字体可用（FandolSong-Regular.otf, …）
[v] 工作目录    : 可写
--- 结论 --- [v] 环境齐备，可以开始
```

```
$ python snapshot_v1/scripts/extract.py adv_sample.pdf --out tests/adv/work2
[1/6] 解析版面：adv_sample.pdf
      页数=4 栏数=1 正文字号=10.9
[2/6] 提取图片 -> figures/fig1..n.png
      找到 3 张图；缺失 0 张
[3/6] 提取表格 -> tables/
      找到 2 张表；截图兜底 1 张
[4/6] 提取行间公式 -> equations/
      找到 7 条公式；低置信度 2 条（必须人工复核）
章节 13 个 / 内联公式 25 处
需要人工处理：
  ! 公式 eq04 置信度 0.65，必须复核
  ! 公式 eq05 置信度 0.60，必须复核
```

```
$ python snapshot_v1/scripts/equations.py render --work tests/adv/work2
[*] 渲染 7 条公式（引擎 xelatex.EXE，DPI 220.0）
  [v] eq01: 渲染成功；对照图 eq01_cmp.png
  [v] eq02: 渲染成功；对照图 eq02_cmp.png
  [x] eq03: 渲染失败 -> eq03_render.log
      ! Double subscript.
      l.5 $\displaystyle S_{n}= \sum_{i=1} _{i},$
  [v] eq04: 渲染成功；对照图 eq04_cmp.png  [!] 符号守恒校验不通过
        原文有而还原缺失：{'(': 1, 'ˆ': 1}；还原多出：{}
  [v] eq05/06/07: 渲染成功
[=] 渲染完成：成功 6，失败 1
```

```
$ python snapshot_v1/scripts/equations.py status --work tests/adv/work2
  [ ] eq01..eq07 待处理（已渲染但未确认）
  [x] eq03 渲染失败  ! Double subscript.
[=] 合计 7 条：已确认 0，待处理 6，渲染失败 1，登记跳过 0
```

```
$ python snapshot_v1/scripts/build.py --work tests/adv/work2          # 自动草稿未修
[*] 字体集：windows（探测通过）—— Windows 系统字体（宋体 + 黑体 + Times New Roman）
[*] 编译失败；页数 1
    编译错误： ! Double subscript.
$ python snapshot_v1/scripts/build.py --work tests/adv/work2          # 手工修草稿 + confirm 后
[*] 编译成功；页数 8
```

```
$ python snapshot_v1/scripts/verify.py --work tests/adv/work2
[=] FAIL 2；WARN 0
```

`verify.py` 的 F1–F13 两态对照（`tests/adv/work2/paper/verify-report.json`、`tests/adv/evidence/verify-report_*.json`）：

| 编号 | 检查项 | 自动草稿、编译失败时 | 修草稿+confirm 后 | 我的判定 |
|---|---|---|---|---|
| F1 | 图片提取 | PASS（fig1~3 齐全尺寸正常） | PASS | **误判**：3 张图全部裁错（见 S1） |
| F2 | 图片在译文中被使用 | PASS | PASS | 正确 |
| F3 | 公式复核 | FAIL（待确认 6、渲染失败 1） | PASS（已确认 6、跳过 1） | 门禁本身有效，但一旦 confirm 就无法发现草稿错（S3） |
| F4 | 占位符全部解决 | PASS | PASS | 正确 |
| F5 | 表格提取 | PASS（2 张表文件齐全） | PASS | **误判**：表 1 数值被压行、表 2 截错页（S2/S4） |
| F6 | 数字一致性 | FAIL（−300 / +10.1016） | FAIL（同） | 2 条都是误报/自伤（M2） |
| F7 | 引用标注一致性 | PASS | PASS | 正确 |
| F8 | 章节结构一致性 | FAIL（源 12 / 译 10） | FAIL（同） | **误报**：源侧 12 个"标题"含论文标题与日期行（M1） |
| F9 | 漏译扫描（英文残留） | PASS | PASS | 正确（但见 M3 的图内文字泄漏） |
| F10 | 参考文献未翻译 | PASS | PASS | 正确 |
| F11 | PDF 编译 | FAIL（`! Double subscript.`） | PASS（8 页，前 3 页 1005 个中文字符） | 正确 |
| F12 | 字体嵌入 | PASS | PASS（15 个字体，SimSun/SimHei 已嵌入） | 正确 |
| F13 | 标题/摘要/关键词/说明 | PASS | PASS | 正确 |

**F6 是否因为换了一份 PDF 而大面积误报？** 没有大面积误报：34 vs 34 个显著数字，只报 2 条，且都能定位到根因（见 M2）；但**这 2 条没有一条是真实差异**。

---

## 3. 问题清单（按严重程度）

### 🔴 严重（会导致交付物错误，而质检门禁显示通过）

---

#### S1 图片裁剪整体失效：3/3 张图走 `fallback=nearest-cluster`，裁剪框覆盖 27%–61% 整页，把表格/正文/公式/参考文献一起切进 PNG

**现象**
`figures-manifest.md` 三张图全部带 `fallback=nearest-cluster`，尺寸远超图形本身；中文 PDF 的第 3、5、7 页整页只有一幅大图。

**复现命令**
```
python snapshot_v1/scripts/extract.py tests/adv/adv_sample.pdf --out tests/adv/work2
python tests/adv/analysis3.py tests/adv/adv_sample.pdf tests/adv/work2
```

**真实输出片段**
```
# tests/adv/work2/extracted/figures/figures-manifest.md
| figures/fig1.png | 1 | 2 | vector | 是 | 469x656 | fallback=nearest-cluster; px=3911x5472 dpi=400.0 |
| figures/fig2.png | 2 | 3 | vector | 是 | 409x485 | fallback=nearest-cluster; px=3410x4044 dpi=400.0 |
| figures/fig3.png | 3 | 4 | vector | 是 | 399x344 | fallback=nearest-cluster; px=3325x2869 dpi=400.0 |

# tests/adv/snap_analysis3.txt
Figure 1 (page 2) notes=['fallback=nearest-cluster']
  bbox=(63.0,95.0,532.2,751.5) 469x656pt 占页面 61%
  混入 27 个非图块，共 1102 个字符
     y= 100.0 'Temperature (K)'        ← 图内轴标签（本应留在图里）
     y= 213.8 '0 2 4 6 8 10 300'
     y= 244.4 role=caption  'Table 1: Measured and simulated peak temperatures…'  ← 另一张表的题注
     y= 280.0 role=body     'Specimen Thickness (mm) Tmax (K) Error (%) S-01 2.0 341.2 1.8…' ← 表 1 全部数据
     y= 383.3 role=heading  '2 Method'
     y= 412.9 role=heading  '2.1 Error estimator'
     y= 472.2 'λeff = a+b ,c+d/e (2)'   ← 公式(2)
     y= 532.9 'Sn = n ∑ i=1 wix2 i , (3)' ← 公式(3)
     y= 650-687 'f(x) = … (5)'          ← 公式(5)
     y= 714-739 '∫ … T(x,t)dV ≈ 12.4±0.8. (6)' ← 公式(6)
Figure 2 (page 3) 409x485pt 占页面 40%，混入 13 块 1303 字符（含 §2.2/§2.3/§3/§3.1 标题与整段正文）
Figure 3 (page 4) 399x344pt 占页面 27%，混入 8 块 506 字符（含 Limitations 段落、Acknowledgements、英文参考文献）
```
编译后的中文 PDF（`tests/adv/evidence/paper_windows_fontset.pdf`）：
```
===== page 3  CJK字符=66  图像对象=1
    image xref=32 3911x5472 px  placed=417x584pt 占页面积 49%
    y= 700.0 '图1 五铺层试样加热120 s 后的温度剖面。…'
===== page 6  CJK字符=52  图像对象=1
    image xref=44 4961x7016 px  placed=454x641pt 占页面积 58%
    y=  94.4 '表2 四个网格层级最大残差的网格收敛性，…'
```
即：**第 3 页是一张包含英文表 1 和英文正文的截图**，第 6 页是整页英文截图（见 S2）。而 `verify.py` F1 判 PASS。

**我怀疑的根因**
`extract.py:locate_figure()` 用"题注上/下最近的 body 块"当作探测带边界（`band_above()/band_below()`，第 195–207 行）。但**图内坐标轴标签、图例本身被 `classify_blocks` 判成 `body`**，于是：
- 图题注在图上方时（图 1）：`band_above` 是页顶到题注（无图形，未命中），`band_below` 被第一个轴标签 `Temperature (K)`（y=100）截住，只剩 85.9→100 共 14.1pt 的缝隙，探针带里没有任何图形簇 → 两条带都没命中；
- 图题注在图下方时（图 2）：`band_above` 被图内最下方的刻度标签 `0 1 2 3 4 5`（y=531→539）截住，只剩 12.8pt 缝隙，同样落空。
两条带都落空后进入 `fallback=nearest-cluster`，该分支直接把 region 撑成 `(cx0-6, page.rect[1], cx1+6, page.rect[3])`（整页高），再用 `_inner_short_text_blocks` 收进区域内所有 ≤14 词的短块 → 最终 bbox 覆盖几乎整页。
**注意**：现有 `tests/sample_en.pdf` 的两张图是 `\includegraphics` 插入的栅格图、图内**无任何文字**，因此 `region=below/region=above` 都能命中——这正是不够的样张设计掩盖该缺陷的原因。

**影响面**
任何"图内带文字"的矢量图（tikz/pgfplots/Illustrator 导出的 PDF 图、期刊常见的矢量图）都会裁错；错误会一路进入交付 PDF，且 F1 PASS、figures-manifest 只说"需人工核对"，缺省流程（步骤 2 靠人眼看 figN.png）能拦住，但读数/自动核对全部失真。**这是本技能包最严重的缺陷。**

> **状态更新（见第 8 节）**：最新活代码（`extract.py` sha1 `af9c59315e0a5006`）新增了 `_figure_internal_ids()`，本类"整页级误裁"已修复（3 张图都变成 `region=above/below`、尺寸降为 329x140 / 349x140 / 371x159pt）。但同一处逻辑仍会**切掉整幅宽大图的一条边**：图 3 右侧 40.4pt（子图 (b) 的右缘）在裁剪框之外——见第 8 节。

---

#### S2 无边框表格的"截图兜底"截到了**另一页**（第 1 页整页），且是逐字节可证的错误页面

**现象**
`tables.json` 里表 2 的截图范围是 `rect=(0, 0, 595, 842)`（整页），而表题实际在第 3 页。

**复现命令**
```
python snapshot_v1/scripts/extract.py tests/adv/adv_sample.pdf --out tests/adv/work2
python -c "…比较 table02.png 与各页 600dpi 渲染的逐字节差异…"   # 见 tests/adv 下的比对片段
```

**真实输出片段**
```
# tests/adv/work2/extracted/tables/tables.json
"02": { "mode":"image", "file":"table02.png",
        "notes":["region=none(无横线)","重建失败",
                 "结构解析失败：改为截图（tableNN.png），需人工核对裁剪范围",
                 "image=table02.png rect=(0, 0, 595, 842)"] }

table02.png = 4961 x 7016
  与第1页 @600dpi 尺寸一致；逐字节相同=True，不同字节数=0
  与第2页 @600dpi 尺寸一致；逐字节相同=False，不同字节数=8852958
  与第3页 @600dpi 尺寸一致；逐字节相同=False，不同字节数=9463797
  与第4页 @600dpi 尺寸一致；逐字节相同=False，不同字节数=9662376
```
（`adv_sample.pdf` 是单栏无页眉页脚差异的样张，第 1 页=封面/摘要页。）编译后中文 PDF 第 6 页即此图，题注却是"表2 四个网格层级最大残差的网格收敛性"。

**我怀疑的根因**
`extract.py:499-504`：
```python
pd_page = None
for p in d.pages:
    if ti.caption_bbox and _bbox_on_page(ti.caption_bbox, p):
        pd_page = p
        break
```
`_bbox_on_page()` 只判断 bbox 是否落在**页矩形之内**（`p.rect[0]-tol <= bbox[0] and bbox[2] <= p.rect[2]+tol ...`），而一篇论文所有页尺寸相同，于是**永远命中 `d.pages[0]`（第 1 页）**。表题真正所在的页索引从未被使用。随后 `_table_crop_rect()` 在第 1 页上按题注坐标找块，一个也找不到，`top/bottom` 保持整页默认值 → `(0,0,595,842)`。

**影响面**
所有走"截图兜底"路径的表格（无边框表、无规则线表、复杂合并单元格表、`--no-tables` 的全部表格）都会截到第 1 页；输出是**完全无关的图片**，而 F5 只检查"文件齐全"故判 PASS，manifest 的措辞（"需人工核对裁剪范围"）会让人以为只是边缘多裁了一点。在双栏样张上同样存在（只是 `column_bounds` 会把它裁成第 1 页的左/右半栏，看起来更像"某段文字"）。

---

#### S3 公式被**静默截断**：原文/草稿/权威截图三者同时缺内容，符号守恒校验给假保证，错误草稿可以一路 confirm 到交付

**现象**
3 条公式（eq02/eq03/eq06）的实际内容缺失 25%–75%，`confidence` 却是 1.00 / 0.80 / 0.82（都在 0.75 阈值之上，**不会进"必须复核"清单**）；手动改成正确公式后，符号守恒校验反而报"还原多出"。eq06 丢失的内容在**任何产物里都找不到**。

**复现命令**
```
python snapshot_v1/scripts/extract.py tests/adv/adv_sample.pdf --out tests/adv/work2
python snapshot_v1/scripts/equations.py render --work tests/adv/work2
python tests/adv/analysis3.py tests/adv/adv_sample.pdf tests/adv/work2
```

**真实输出片段**
```
# tests/adv/work2/extracted/equations/equations.md （自动产物）
| eq02 | 2 | 2 | 1.00 | 否 | `λeff= c+d/e,` | `\lambda^{eff}=,c+d/e` |
| eq03 | 3 | 2 | 0.80 | 是 | `∑ Sn= i, i=1` | `S_{n}= \sum_{i=1} _{i},` |
| eq06 | 6 | 2 | 0.82 | 是 | `∫ ¯T=1` | `\int T¯=1` |

# tests/adv/snap_analysis3.txt（裁剪 vs 原公式真实字形范围）
eq02 裁剪=(264,479,331,498) 原公式字形范围=(264,472,331,498) 上缺7pt   ← 分子 a+b (y=472.2) 不在裁剪内
eq03 裁剪=(267,540,328,564) 原公式字形范围=(267,533,328,564) 上缺8pt   ← 求和上限 n (y=532.9) 不在裁剪内
eq06 裁剪=(223,715,260,739) 原公式字形范围=(68,700,441,747) 右缺181pt  ← 右侧 2/3 不在裁剪内

# render 的符号守恒结果
eq04: [!] 符号守恒校验不通过  原文有而还原缺失：{'(': 1, 'ˆ': 1}
eq02 / eq03 / eq06: 渲染成功，symbol_ok = true（无任何告警）

# eq06 的原文字符转储只有 1038 字节，且
#   原文该行真实文字（PyMuPDF words）：
#   x=260.21 'ΩT(x,t)dV'    x=313.65 '≈12.4±0.8.'    x=514.52 '(6)'
#   而 source.md 里既没有这些内容、也没有 ⟦EQ06⟧ 之外的残留：
$ Select-String work2/extracted/source/source.md -Pattern "dV|12\.4|6\)"
95: <!-- 行间公式：原编号 (6)  置信度 0.82 -->
（其余命中均为参考文献年份，与公式无关）

# 我按原图手工写成正确公式后重跑 render：
eq06: [!] 符号守恒校验不通过
      原文有而还原缺失：{'¯': 1}
      还原多出：{'T':1,'1':1,'V':2,'Ω':1,'(':1,'x':1,',':2,'t':1,')':1,'d':1,'≈':1,'2':1,'.':2,'4':1,'±':1,'0':1,'8':1}
```

**我怀疑的根因**（两层，都可以定位到代码）
1. **数学/正文判别失效导致整行被丢**。`_line_math_ratio()` 依赖字形字体名（`mathmap.MATH_FONT_HINTS`：CMMI/CMSY/CMEX/…）或 Unicode 数学符号表。本样张是期刊最常见的 "Times 正文 + CM 数学"（`mathptmx`）：公式里的数字与拉丁字母被报成正文体 `NimbusRomNo9L-Regu`，于是整块的 `math_ratio` 只有 **0.18**（< 0.55，不进 `role=math`），逐行门槛 `_line_math_ratio >= 0.32` / 碎片门槛 `>= 0.25` 又把下列整行丢掉：
   - `'a+b'`（ratio 0.00）→ 丢失嵌套分式的分子（eq02）
   - `'ΩT(x,t)dV'`、`'≈12.4±0.8.'`（纯字母/数字/≈/±）→ eq06 右侧整体丢失
   实测同一块内 `'λeff ='`=0.20、`'a+b'`=0.00、`',c+d/e'`=0.33、`'(2)'`=0.00（靠 `_attach_only` 吸附）——**阈值恰好把最需要保留的行排除在外**。
2. **"符号守恒校验"的基准错了**。`equations.py` 用 `extract.py` 写出的 `eqNN_chars.json`（上面已经残缺的字符集合）与 `draft/eqNN.tex` 做多重集比对。基准 = 抽取结果而不是原图，因此①抽取残缺时校验通过（假阴性，eq02/03/06）；②按原图改正确时校验报"多出/缺失"（假阳性，eq04/eq06）。另外 `¯`(U+00AF)↔`\bar`、`ˆ`(U+02C6)↔`\hat` 未做等价化，会误报"缺符号"。
3. 附带的门槛边界问题：`MIN_MATH_CONFIDENCE = 0.75` 用 `<` 比较，现有样张的 eq07 恰好是 **0.75**（不合格草稿 `\Phi==\frac{\sum^{Ni=1}}{N}\sum_{w_{i=1}}^{^{2}w^{i}x^{iN}}`）却没进"必须复核"清单。

**影响面**
公式是技能包自我宣称的"核心质量环节、硬要求"。实际效果是：**错的草稿能通过全部自动校验并 confirm 成功**（我实测 eq01/02/03/05 不带 `--force` 全部 confirm 通过），而人工修好后反而必须 `--force`（`--force` 的文档理由只覆盖"PDF 文本层缺陷"，不覆盖"分段器漏行"）。eq06 丢失的内容在 `source.md`、`draft/eq06.tex`、`eq06_orig.png` 三处同时不存在，译者**不可能**发现。

---

#### S4 带竖线表格的结构重排把 6 行压成 2 行，且报 `quality=1.00`

**现象**
表 1（`|l|c|r|c|`，只有顶/表头下/底 3 条横线）被重排为 2 行，5 个试样的数据各自拼进了一个单元格。

**复现命令**
```
python snapshot_v1/scripts/extract.py tests/adv/adv_sample.pdf --out tests/adv/work2
python tests/adv/probe_tables.py tests/adv/adv_sample.pdf
```

**真实输出片段**
```latex
% tests/adv/work2/extracted/tables/table01.tex
\begin{tabular}{lccc}
\toprule
\textbf{Specimen} & \textbf{Thickness (mm)} & \textbf{T (K) max} & \textbf{Error (\%)} \\
\midrule
S-01 S-02 S-03 S-04 S-05 & 2.0 2.5 3.0 3.5 4.0 & 341.2 348.7 352.4 359.1 364.6 & 1.8 0.9 2.3 2.1 2.4 \\
\bottomrule
\end{tabular}

# tables-manifest.md
| 01 | 1 | LaTeX 三线表 | 2x4 | 1.00 | tables/table01.tex | strategy=lines quality=1.00 caption-dist=11pt |

# probe_tables.py
find_tables(lines) bbox=(170.4, 278.3, 425.2, 363.7) rows=2 cols=4 quality=1.00
     row: S-01\nS-02\nS-03\nS-04\nS-05 | 2.0\n2.5\n3.0\n3.5\n4.0 | 341.2\n… | 1.8\n…
```

**我怀疑的根因**
`extract_tables()` 先试 PyMuPDF 的 `find_tables(strategy="lines"|"lines_strict"|"text")`，只有全部失败才落到自研的 `L.table_from_text()` 几何重建（几何重建在现有样张上是**成功**的：`region=rules(6 条横线); 几何重建成功 quality=1.00 rows=6 cols=4`）。PyMuPDF 的 `lines` 策略以**横线**切行，本表只有 3 条横线 → 6 行数据落进同一条行带、被拼成一个单元格；而 `_table_quality()` 只看"非空填充率 + 各行列数一致性"，这种"行列数完全一致但内容合并"的坏结果恰好得满分。代码在 `quality >= 0.6` 时直接采信，不再交叉验证。

**影响面**
期刊里"全竖线、只在表头和表尾加横线"的网格表非常常见（Elsevier/IEEE 都有）。这类表会被静默错列，F5 PASS，manifest 还标"LaTeX 三线表 quality=1.00"。

---

#### S5 `--dpi` 参数被静默忽略；按技能文档建议用 `--dpi 600` 会让整次提取崩溃

**现象**
默认 `--dpi 400` 与 `--dpi 150` 产出的 PNG **像素完全相同**（3911×5472，实际是 600 dpi），manifest 却写 `dpi=150.0`；`--dpi 600` 直接抛 `TypeError`、退出码 1，产物残缺（无 `manifest.json`、`source.md`、`tables/`）。

**复现命令**
```
python snapshot_v1/scripts/extract.py tests/adv/adv_sample.pdf --out tests/adv/work2_dpi600 --dpi 600
python snapshot_v1/scripts/extract.py tests/adv/adv_sample.pdf --out tests/adv/work2_dpi150 --dpi 150
```

**真实输出片段**
```
# --dpi 600
[3/6] 提取表格 -> tables/
Traceback (most recent call last):
  File "…/extract.py", line 1194, in main
    tabs = extract_tables(d, out_dir / "tables", args.dpi, force_image=args.no_tables)
  File "…/extract.py", line 510, in extract_tables
    crop_png(pg, rect, dpi, png)
  File "…/extract.py", line 86, in crop_png
    pix = page.get_pixmap(clip=r, dpi=max(dpi, 600), alpha=False)
  File "…/pymupdf/__init__.py", line 11833, in get_pixmap
    pix.set_dpi(dpi, dpi)
TypeError: in method 'fz_pixmap_xres_set', argument 2 of type 'int'
EXITCODE=1        # 且 work2_dpi600 下没有 manifest.json

# --dpi 150（与默认 400 完全一致）
work2       dpi参数= 400.0 [['fallback=nearest-cluster', 'px=3911x5472 dpi=400.0']]
work2_dpi150 dpi参数= 150.0 [['fallback=nearest-cluster', 'px=3911x5472 dpi=150.0']]
```

**我怀疑的根因**
`crop_png()`（extract.py:60–91）把 argparse 的 `float` dpi 直接传给 `page.get_pixmap(clip=..., dpi=dpi)`；PyMuPDF 1.26.5 的 `get_pixmap` 在带 `clip` 时会走 `pix.set_dpi(dpi, dpi)`，而该底层 setter 只接受 **int** → 抛 `TypeError` → 被 `except Exception: continue` 吞掉 → `best is None` → 落到兜底 `page.get_pixmap(clip=r, dpi=max(dpi, 600))`。默认 400.0 与 600 比较得 **int 600**，于是"成功"但固定 600 dpi；而 `--dpi 600` 时 `max(600.0, 600)` 返回 **float 600.0**，兜底同样抛异常且这次无人捕获 → 崩溃。

**影响面**
①分辨率不可控，图片体积恒为请求值的 2.25 倍（fig1.png 638 KB、table02.png 1.17 MB）；②manifest 的 `dpi=…` 备注是假的；③技能文档第七节"常见故障"表格明确写着"必要时 `--dpi` 提到 600"——**照做会毁掉整次提取**（且失败点在表格阶段，`source.md`/`manifest.json` 都不会生成）。

---

### 🟠 中等

#### M1 F8 把论文标题与日期行当章节标题，造成结构性误报
**现象**：F8 报"源文可译标题 12 个；译文 10 个 → 可能有整节漏译"。我的译稿逐节翻译、无漏译。
**证据**
```
# tests/adv/work2/extracted/source/sections.json
{ "page":1,"level":1,"title_en":"Adaptive Mesh Refinement for Transient Thermal Diffusion in Layered…"}   ← 论文标题
{ "page":1,"level":3,"title_en":"Received 14 March 2024; accepted 2 August 2024"}                        ← 日期行
{ "page":1,"level":3,"title_en":"Abstract"} …
# verify.py
[x] F8: 源文可译标题 12 个；译文 \section 4 + \subsection 6 + \subsubsection 0 = 10
    源文标题：Adaptive Mesh Refinement for Transient T; Received 14 March 2024; accepted 2 Augus; Abstract; …
```
**根因**：`p2zlib.classify_blocks()` 的 `is_big`/`is_bold_short`/`is_known_section` 规则把大字号标题块与日期行都判成 `heading`；F8 直接数 `sections.json` 中非 `no_translate` 的标题数。另外原文的 `Abstract` 计入源侧标题，而译文的 `## 摘要` 被模板渲染为 `abstract` 环境、不计入译文标题数，进一步拉大差额。
**影响**：只要原文标题/日期行被判成标题（很常见），F8 就必然 FAIL；文档要求"FAIL 必须清零"，于是译者被逼着在译稿里编造 1–3 个假标题。

#### M2 F6 的两条差异都是误报/自伤
**证据**
```
[x] F6 数字一致性: 源文显著数字 34 个 / 译文 34 个；源文有而译文无 1 个；译文多出 1 个
    【源文有、译文没有】300
    【译文多出】10.1016
```
- `-300`：图 1 的 y 轴刻度 `300` 与 x 轴刻度被拼成一行 `0 2 4 6 8 10 300`（`source.md` 第 57 行）进入正文侧 → 门禁要求译文里也有 300。根因是**图内文字泄漏进可翻译源文**（同 M3），不是漏译。
- `+10.1016`：`paper.tex` 第 87 行由模板打印首页信息 `DOI：10.1016/j.ijheatmasstransfer.2024.124501`，第 225 行是我照抄原文的 DOI URL → 同一数字出现两次，源文侧只有一次。**这是 build.py 自己的排版造成的不一致**，却要作者写进 allowlist 才能过门禁。
**影响**：F6 在这份样张上没有抓到任何真实差异，反而产生 2 条必须人工豁免的噪声；重复元数据（DOI/年份同时出现在首页与正文）是常见写法，该噪声具有普遍性。

#### M3 图内英文文字泄漏进 `source.md`（要求译者"翻译"图内文字），旋转文字被抽成反序乱码
**证据**（`tests/adv/work2/extracted/source/source.md`）
```
41: Temperature (K)
43: 380
45: interface 3 sharp smoothed
47: 360
49: experiment
55: Depth z (mm)
57: 0 2 4 6 8 10 300
165: )mm(zssenkciht-hguorhT          ← 原为竖直旋转的 "Through-thickness z (mm)"，字符顺序被反转
167: refined unrefined
```
**根因**：`extract.build_source_md()` 把图内短文本块当普通段落输出（`_inner_short_text_blocks` 只把它们并入裁剪框，没有从源文里剔除）；旋转文字的字符顺序未按旋转矩阵还原。
**影响**：与技能红线"图片内部英文不翻译"直接冲突——译者按 `source.md` 逐节翻译会把这些轴标签译成中文并写进正文（图仍是英文），或必须逐个手工删除；F9 也查不出这种"图内文字被译"的情况。顺带污染 F6（M2 的 `300`）。

#### M4 无边框表格的数字被误判成一条"行间公式"，且表格内容在源文里被拆散错序
**证据**
```
# equations.md
| eq07 | 无 | 3 | 1.00 | 否 | `3.4×10−3 4.000 9.1×10−4 2.000 1.90 …` | `4.000 3.4\times 10^{-3}2.000 9.1\times 10^{-4}1.901.000 …` |
# source.md 139-148
139: Mesh Cells h (mm) Residual Rate M1
141: <!-- 行间公式：原编号 (无)  置信度 1.00 -->
142: ⟦EQ07⟧
144: M2
146: M3
148: M4
```
**根因**：表 2 走了截图兜底，`extract.py:1198-1201` 的 `excl`（用于从公式候选中排除表格区域）只收集 `t.bbox`，而截图模式下 `t.bbox == 题注 bbox`（不含表格本体），于是表格单元格落入 `_display_candidates`，因含 `×`/`−`/数字而被判成数学块 → 生成 phantom eq07（原编号为空、置信度 1.00）。同时因为表格没被识别成表格，其文本块按普通块顺序输出，表格内容被"标题行 + ⟦EQ07⟧ + M2/M3/M4"切碎。
**影响**：manifest 声称 7 条公式（实际 6 条），多出的一条必须人工 skip；`source.md` 里表格内容顺序错乱，译者极易把表 2 抄错位置。

#### M5 行内公式把数字/上下标切断
**证据**（`source.md`）
```
21:  … falls below 1$0^{-3}$after four refinement cycles …          ← "10^{-3}" 被切成 "1" + "$0^{-3}$"
98:  … the tolerance on the linear residual is 1$0^{-3}$. …
107: $\alpha$=$1.15\times 10^{-6}$m2/s …
109: k = 4$5.0$W/(m K) h = 1$2.5$W/(m2 K) T$_{\infty}=$29$8.15$K   ← 45.0 / 12.5 / 298.15 全被切开
125: … the run-to-run spread of the peak temperature is 1$2.4\pm 0.8$K …
98:  … the dimensionless group$\eta^{2}$ $_{i}\approx 19.8$was …      ← 上标与下标脱离底数
```
**根因**：`find_math_runs()` 的区间左端点判定（首字符未被标记，其余被标记）导致"数字的第一位留在正文、其余进数学"，`\eta^{2}_{i}` 的上下标被拆成两个独立数学片段。
**影响**：verify 侧因 `$`→空格归一化而未触发 F6，但**译者抄写时极易错**（`4$5.0$` 里的 `45.0`、`29$8.15$` 里的 `298.15` 都要靠人拼）；`$\eta^{2}$ $_{i}$` 会让渲染出的下标与底数错位。

#### M6 参考文献被编号为"4 致谢 / 4.1 参考文献"
**证据**
```
# paper.tex
221: \section{致谢}
225: \subsection{参考文献}
# 中文 PDF 第 4 页
y= 564.5 '4 致谢'
y= 650.6 '4.1 参考文献'
```
**根因**：`SKILL.md` 第四节自己给出的 `paper.md` 契约把参考文献写成 `## 参考文献`，build.py 忠实转成 `\subsection` → 被 LaTeX 自动编号，并挂在"致谢"节下面成为 4.1。原文的 `References` 是无编号标题（源文 `sections.json` 也确实标了 `level:2, no_translate:true`）。
**影响**：交付 PDF 的参考文献标题编号与原文不符，违反红线"原文编号体系不动"。

#### M7 图表题注里的行内公式被转义成字面量
**证据**
```
# paper.tex:211
\caption{五铺层层合板在 \$t = 120\$ s 时的重构温度场（子图 a）…}
# 中文 PDF 第 7 页
y= 588.1 '图3 五铺层层合板在$t = 120$ s 时的重构温度场（子图a）…'
```
**根因**：`⟦FIGnn|题注⟧` 的题注文本走 `escape_latex_text()`，不做行内公式处理，`$` 被转义为 `\$`。
**影响**：题注里出现可见的 `$t = 120$` 字面量；译者只能手动改成 `\(t=120\)`，且没有任何门禁会提示。

#### M8 用户手写的 `skip-list.txt` / `verify-allowlist.txt` 若带 UTF-8 BOM，第一条被**静默忽略**
**现象**：按文档写跳过登记，`status` 仍显示"待处理"，无任何警告。
**复现命令与输出**
```
# 带 BOM（PowerShell 5.1 的 Set-Content -Encoding UTF8 默认加 BOM；记事本亦然）
$ python write_utf8.py work2/extracted/equations/skip-list.txt bom "eq07 这不是公式：…"
  [ ] eq07 待处理（已渲染但未确认）  eq07_cmp.png
[=] 合计 7 条：已确认 0，待处理 7，渲染失败 0，登记跳过 0        ← 静默失效

# 同一内容、无 BOM
$ python write_utf8.py work2/extracted/equations/skip-list.txt nobom "eq07 这不是公式：…"
  [s] eq07 已登记跳过（这不是公式：…）
[=] 合计 7 条：已确认 0，待处理 6，渲染失败 0，登记跳过 1

# allowlist 同理：带 BOM 时第一行 "-300" 被当成 "\ufeff-300"
[x] F6 … 源文有而译文无 1 个 → 300 仍在
（无 BOM 时）[v] F6 … 源文有而译文无 0 个；译文多出 0 个
```
**根因**：这两个文件都是**用户手写**的输入文件，但读取时用 `encoding="utf-8"`（快照版 `equations.py` 读 `skip-list.txt` 仍是 `utf-8`；`verify.py` 的 `load_allowlist` 已改成 `utf-8-sig`）。BOM 只影响第一行 → 报错信息完全没有提示。
**影响**：文档指定的两条"人工留痕/豁免"通道在最常见的 Windows 编辑器下会悄悄失效。`verify.py` 侧已修，`equations.py` 侧未修。

#### M9 单栏文档下表格区域宽度被撑到整页宽
**证据**
```
$ python analysis3.py → page 2 split_x=None；L.column_bounds(p,0) = (0.0, 595.28)
$ extract.py --no-tables → tables.json:
  "image=table01.png rect=(-4, 269, 599, 495)"    ← 比页宽还大（负 x、超出 595）
```
**根因**：`p2zlib.column_bounds()` 在 `split_x is None` 时返回整页宽度；`_table_region()` 用 `column_bounds` 作为表格左右边界。
**影响**：单栏论文里所有"区域"类产物（表格区域、截图范围）横向都包含整个页宽与页边空白，浪费版面并可能带进页边内容。

#### M10 `_table_region()` 会把表格下方 400pt 内的任意横线并进表格区域
**证据**
```
$ python probe_tables.py
_table_region -> (0.0, 272.9, 595.3, 490.8)  region=rules(8 条横线)
# 表 1 真实范围 y=278.3~363.7（3 条横线）；区域底 490.8 已越过公式(2)的分式横线（y=485.4）
```
**根因**：`group` 的断开阈值是 `r[1] - group[-1][3] > 150`（150pt），而表底到式(2)分式横线只差 121.7pt；`page.rules` 里包含**分式横线**（LaTeX 分式是填充矩形，`parse_page` 会把 `re`/`l` 项也登记为 rule）以及 tikz 的坐标轴/网格线。
**影响**：表格截图（`--no-tables` 或几何重建作为区域时）会把下一行的公式横线一起拍进去；也让 region 与"表格"的语义脱节。

---

### 🟡 轻微

| 编号 | 现象 | 证据 | 根因/影响 |
|---|---|---|---|
| L1 | `build.py`、`env_check.py` 的中文输出是 **GBK 字节**，而 extract/equations/verify 是 UTF-8 | `"build.py --help"` 前几个中文 = `\xd6\xd0\xce\xc4`（GBK 的"中文"）；`extract.py --help` 同一位置 = `\xe5\xb7\xa5\xe4\xbd\x9c`（UTF-8） | 这两个脚本没有 `import p2zconsole`（`p2zconsole` 的文档说"其它脚本导入即生效"）。管道/GUI/CI 里同一个技能包的中文一半乱码一半正常 |
| L2 | `build.py --help` 里 `--passes` 无说明文字 | `--passes PASSES` 后面是空行 | 用户不知道 `--passes 2` 是 xelatex 编译遍数 |
| L3 | `cases` 环境的大括号在 PDF 文本层是私有区字符 | 中文 PDF 第 2 页出现 `'\uf8f1 \uf8f4 \uf8f2'`、`'\uf8f4 \uf8f3'` | 显示正常但复制/检索得到乱码；F11/F12 都不检查这类"文本层不可读" |
| L4 | `env_check --json` 的文字重复："PyMuPDF PyMuPDF 1.26.5" | `"pymupdf_msg": "PyMuPDF PyMuPDF 1.26.5；…"` | 版本串拼接 |
| L5 | 文档与目录结构不同步 | 我加载技能时 `references/extraction-rules.md` 不存在（15:43 才出现）；`tests/_work`、`tests/verify_sample.py`、`tests/debug_*.py` 测试期间被删除 | 技能包在我测试期间被另一进程改动，`SKILL.md` 里还引用了当时不存在的文件 |
| L6 | 交付物契约把 `equations/skip-list.txt` 列成"产物"，但只有用到时才创建 | 首次 `extract`+`status` 后该文件不存在，`Get-Content` 报 Cannot find path | 契约应写成"按需创建"，否则按清单核对交付物时会误判缺失 |

---

### 我在测试中顺带确认"不是缺陷"的项

| 项 | 证据 |
|---|---|
| 单栏/双栏判定正确 | `页数=4 栏数=1`；4 页 `split_x` 全为 `None` |
| `\tag` 保留原编号 | `paper.tex` 第 108/138/146/154/162/170 行分别 `\tag{1}…\tag{6}`；中文 PDF 第 2 页文本含 `(1)…(6)` |
| 中文字体集首次运行自动探测成功 | `[*] 字体集：windows（探测通过）`（无缓存首次运行）；F11「PDF 中文字符（前3页）1005 个，中文渲染正常」；F12 嵌入 SimSun/SimHei |
| Fandol 兜底路径可用（Linux 近似） | `build.py --work work2 --fontset fandol` → `编译成功；页数 8`，字体为 `FandolSong-Regular`/`FandolHei-Regular` + `TeXGyreTermes/Heros`，前 3 页 1005 个中文字符（证据 `tests/adv/evidence/paper_fandol_fontset.pdf`） |
| 参考文献区被正确标为不翻译 | `source.md` 有 `<!-- REFERENCES：以下内容不翻译，原样保留 -->`；`sections.json` 中 `"title_en": "References", "no_translate": true`；F10 PASS |
| confirm 的三道闸门有效 | 无 `--note` → `[x] confirm 必须带 --note…`（exit 2）；符号不过 → `[x] eq04 符号守恒校验不通过…请先修 draft/eq04.tex`（exit 2）；渲染失败 → `[x] eq03 尚未渲染成功：先跑 render。`（exit 2） |
| render 失败会留下可定位日志 | `equations.py render` 失败时写出 `extracted/equations/eq03_render.log`，内容 `! Double subscript. l.5 $\displaystyle S_{n}= \sum_{i=1} _{i},$ No pages of output.`；`status` 也标 `[x] eq03 渲染失败`（**没有出现"渲染失败却显示通过"的情况**） |
| 其他 CLI 行为与 help 一致 | `--pages 1-2` → `页数=2`、6 条公式、1 图 1 表；`--keep-source` → `<work>/source/adv_sample.pdf`（43 445 B）；`--no-tables` → 两张表均 `mode=image`；`--out` 默认 `translator-work` 在 CWD 生成；`env_check --json` 输出合法 JSON |
| 无 pandoc / 无 matplotlib 依赖 | `grep "^import|^from"` 结果只有标准库 + `fitz` + 本地模块（p2zlib/mathmap/p2zconsole/p2zpaths） |

---

## 4. `--help` 与实际行为的一致性

| 脚本 | 参数 | help 声称 | 实际 | 判定 |
|---|---|---|---|---|
| extract.py | `--dpi` | "图片裁剪 DPI（默认 400）" | **参数被忽略**（恒为 600 dpi 渲染，备注却写请求值）；`--dpi 600` 直接崩溃 | ❌ **不一致（严重）** |
| extract.py | `--out` | 默认 `translator-work` | 一致（实测在 CWD 建目录） | ✅ |
| extract.py | `--pages` | "只处理部分页，如 1-5,8" | 一致 | ✅ |
| extract.py | `--no-tables` | "表格一律截图，不做结构重排" | 一致（两张表都变 `image`） | ✅ |
| extract.py | `--keep-source` | 复制源 PDF 到 `<work>/source/` | 一致 | ✅ |
| equations.py | `render [--only] [--dpi]` / `confirm --note（必填）/--force` / `status` | 与 help 一致，`--note` 与 `--force` 的约束都真的生效 | ✅ | ✅ |
| build.py | `--work / --md / --tex / --fontset / --reprobe` | 一致；`--fontset` 列出的 5 个 id 与代码 `FONTSETS` 完全相同 | ✅ | ✅ |
| build.py | `--passes` | **无任何说明文字** | 实际是 xelatex 编译遍数（默认 2） | ⚠️ help 残缺 |
| verify.py | `--work` | 一致（默认 `translator-work`） | ✅ | ✅ |
| env_check.py | `--install-deps / --work / --json` | `--json`、`--work` 实测一致 | `--install-deps` 未验证（本机已装 PyMuPDF） | ⚠️ 部分未验证 |

另外：`SKILL.md` 第六节交付自查里写"`equations.py status` 显示：待处理 0、渲染失败 0"——在**自动草稿未修**的状态下必然做不到（eq03 因草稿本身语法错而渲染失败）；而"渲染失败"的根因是抽取阶段漏行，文档没有把这条因果链写清楚。

---

## 5. 真实缺陷 / 可接受的设计限制 / 未验证项

### 5.1 真实缺陷（应当修）
S1 图片裁剪 fallback 导致整页误裁 ｜ S2 表格截图页定位错误（`_bbox_on_page` 语义错）｜ S3 公式分段漏行 + 符号守恒基准错误 + 置信度阈值边界 ｜ S4 PyMuPDF `lines` 策略压行且 `quality` 指标无效 ｜ S5 `--dpi` float 崩溃/被忽略 ｜ M1 F8 计入非标题 ｜ M2 F6 两条结构性噪声 ｜ M3 图内文字泄漏进源文（含旋转文字反序）｜ M4 无边框表格被误判为公式且源文顺序错乱 ｜ M5 行内公式切断数字/上下标 ｜ M6 参考文献被编号 ｜ M7 题注行内公式被转义 ｜ M8 `equations.py` 读 `skip-list.txt` 不吃 BOM ｜ M9 单栏下区域宽度=页宽 ｜ M10 表格区域吞掉公式分式横线 ｜ L1 build/env_check 控制台编码 ｜ L2–L6

**在最新活代码（`extract.py af9c5931…` / `verify.py e699e289…`）上的状态**：

| 状态 | 条目 |
|---|---|
| ✅ 已修 | **S1** 的"整页级误裁"（新增 `_figure_internal_ids()`；3 张图改为 `region=above/below`） |
| ⚠️ 部分修 | **S1 残留**：整幅宽大图仍被切边（图 3 右切 40.4pt）；**M1**（F8）在 `verify.py` 改动后本样张仍 FAIL，未复核是否已修 |
| ❌ 仍在 | **S2**、**S3**、**S4**、**S5**、**M4**（逐条实测见第 8 节）；M2/M5–M10/L1–L6 未在最新版上逐条复测（`equations.py`/`p2zlib.py`/`build.py` 与快照相同，故 M5–M7、M9–M10、L1–L2 的代码路径未变，推断仍在） |

### 5.2 可接受的设计限制（需人工复核，不是 bug）
- **PDF 图像是"渲染像素"，无法从矢量图里分离文字**：图内英文只能沿用原图；但当前实现把图内文字**同时**输出到 `source.md` 是设计缺陷（M3），建议至少在源文里标注"图内文字，勿译"。
- **公式草稿只承诺"高概率正确"**：文档已说明需人工比对；但前提是"权威截图 + 符号守恒"真的可信——本报告的 S3 正是在攻击这个前提，因此它**不能**算作可接受限制。
- **表格重排失败改截图**：可接受；但截图范围必须正确（S2 使其不可接受）。
- **题注统一放图下/表上**：与样张原文（图 1 题注在上）不同，属于模板统一化的设计取舍，可接受（需在翻译说明里交代）。
- **`figN` 编号按原文图号**：本样张 fig1/2/3 与原文 Figure 1/2/3 一致 ✅。
- **verify 的 WARN/FAIL 阈值**：F8"标题数不少于源文"这类硬计数本身偏脆弱，但只要把非标题块排除即可接受。

### 5.3 本轮**未验证**项（不谎报）
1. **目视核对**：当前模型不接受图片输入，`read_image` 报 `model "deepseek-v4-flash" does not declare image input`。因此所有图片/对照图结论都是**几何与字节级**证据（裁剪 bbox、混入文本块清单、像素尺寸、与页渲染的逐字节比对），没有"看起来对不对"的人工目视确认；`eqNN_cmp.png` 的视觉相似度未评估。
2. **三栏排版**：样张是单栏，`detect_document_columns()` 只返回 1 或 2，三栏布局完全未测（推测会误判为 2 栏并错排阅读顺序）。
3. **真正的跨栏（span）大图**：单栏下 `page.split_x is None` → `locate_figure` 的 `full-width` 分支（`if page.split_x else False`）与 `classify_block_column` 的跨栏分支是**死代码**，未能触发。
4. **真实 Linux 运行**：只做了静态检查（无硬编码绝对路径、`os.name/sys.platform` 分支齐全、无 3.9+/3.10+ 语法或 API、`graphicspath` 用正斜杠、依赖仅标准库+PyMuPDF）+ 在本机用 TeX Live 的 Fandol 字体集实跑成功。**没有**在 Linux 上验证 `fc-list` 字体探测、无 SimSun 时的探测顺序、文件名大小写。
5. **`tests/selftest.py` 未运行**：它会在技能包 `tests/` 下 `rmtree` 并重建工作目录，而该目录测试期间正被另一进程改动，运行会互相干扰（风险由我主动规避）。
6. **`env_check.py --install-deps` 未验证**（本机 PyMuPDF 已就绪，不存在缺失场景）。
7. **复杂期刊版面未覆盖**：合并单元格表格、跨页续表、脚注/尾注、附录、补充材料、多图组（(a)(b)(c) 子图分栏）、公式跨栏/跨页、页眉 running head 变化。
8. **多份论文的横向对比未做**：只有一份新样张（加现有样张作为对照）。
9. **`verify.py` 的 F2/F3/F9/F10 反向用例未逐一构造**（例如故意漏插一张图、故意在译文里留英文段），只验证了它们在正常流程下的结果。

---

## 6. 结论

**一句话结论**：这套技能包**目前不能直接用于真实英文期刊论文的交付**——流水线能跑通、中文 PDF 能编译出来、字体与数字门禁基本有效，但"表/公式/图"三个核心素材环节在这份刻意贴近真实期刊（单栏、Times+CM 字体、图内带文字的矢量图、带竖线/无边框表格、嵌套分式）的样张上**成规模失效，而 F1/F5 PASS、公式能 confirm 通过**，错误会原样进入交付物。

**按最新活代码（第 8 节复核）排序的三大风险**
1. **表格（S2）**：无边框表的"截图兜底"截到**第 1 页整页**（`table02.png` 与源 PDF 第 1 页 600dpi 渲染**逐字节相同**）；根因是 `_bbox_on_page()` 只判断坐标是否在页内，永远命中 `d.pages[0]`。改截图这条兜底路径等于把无关页面塞进交付 PDF，而 F5 只看文件是否存在，判 PASS。
2. **公式（S3）**：分段器在 "Times 正文 + CM 数学" 下把整行数学（分式分子、求和上下限与求和项、公式右半段）当正文丢掉，三条公式的 `confidence` 却都高于阈值；"符号守恒校验"以同一份残缺抽取为基准，**既漏报又把正确修订判成编造**，eq02/03/06 的错误草稿能一路 `confirm` 成功，eq06 丢失的内容在 `source.md`、`draft/eq06.tex`、`eq06_orig.png` 三处同时缺失，无法恢复。
3. **带竖线表格结构重排（S4）+ `--dpi` 崩溃（S5）**：6 行数据被压成 1 行且报 `quality=1.00`；`--dpi 600`（技能文档自己推荐的补救措施）抛 `TypeError`、退出码 1 并留下残缺工作目录。**两条官方兜底路径（改截图 / 提高 DPI 重截）在最新版上一条截错页、一条直接崩溃。**

（快照版的图片整页误裁 S1 已在最新活代码中修复，但整幅宽大图仍会被切掉一条边——图 3 右切 40.4pt。）

**结论分级**
- 若目标只是"把英文 PDF 快速转成一份能读的中文 PDF 草稿"，当前版本可用，但**必须逐张检查 figN.png / tableNN.png、逐条按原图重写公式草稿**，且要把 F5/F1 的 PASS 视为"文件存在"而非"内容正确"。
- 若目标是"可交付的中文版论文"（本技能包的自我定位），当前版本**不达标**：图、表、公式三类素材都存在"自动产物错误 + 门禁通过"的组合，人工复核成本高于手工重做。

---

## 7. 复现材料清单（全部在测试目录内，未改动技能脚本）
| 路径 | 内容 |
|---|---|
| `tests/adv/adv_sample.tex` / `adv_sample.pdf` | 我造的对抗性样张（4 页）+ 编译产物 |
| `tests/adv/snapshot_v1/` + `SNAPSHOT.md` | 冻结的技能包快照与 sha1 清单（本报告证据所对应的版本） |
| `tests/adv/work/` | 首次运行（活代码）的工作目录 |
| `tests/adv/work2/` | **快照版**完整工作目录（`extracted/`、`paper/paper.md`、`paper.tex`、`paper.pdf`、`质检报告.md`、`verify-report.json`） |
| `tests/adv/work2_dpi600/`、`work2_dpi150/`、`work_pages12/`、`work_keepsrc/` | S5 与 CLI 行为测试的产物 |
| `tests/adv/evidence/` | 归档证据：`extracted_snapshot/`（`fig1-3.png`、`table01.tex`、`table02.png`、`source.md`、`sections.json`、`figures-manifest.md`、`tables-manifest.md`、`tables.json`、`manifest.json`、`equations.md`、`status_after_confirm.json`、`eq06_orig.png`、`eq06_chars.json`、`eq03_render.log`）、`paper_windows_fontset.pdf`、`paper_fandol_fontset.pdf`、`paper_clean.tex`、`fontset_windows.tex`、`verify-report_clean.json`（快照 work2 的最终态）、`verify-report_work_after_confirm.json`（首次 work 的确认后态）、`质检报告_clean.md` |
| 两态 F1–F13 的原始 stdout | `snap_E_verify1.txt`（自动草稿 + 编译失败：FAIL 4）与 `snap_I_verify2.txt`（修草稿 + confirm：FAIL 2） |
| `tests/adv/orig_drafts/` | extract.py 自动生成的 7 份原始公式草稿（修订前备份，S3 的证据） |
| `tests/adv/*.py` | 我写的辅助脚本：`dump_blocks.py`、`analysis3.py`、`probe_tables.py`、`probe_eqgroup.py`、`check38.py`、`snapshot.py`、`write_utf8.py`、`thumb.py`、`probe_f6.py`、`dump_built.py`、`fix_eq05.py` |
| `tests/adv/work3/` | **最新活代码**（`extract.py` sha1 `af9c59315e0a5006`）的重跑产物，用于第 8 节的复核 |
| `tests/adv/probe_fig_new.py` | 第 8 节用的量测脚本（裁剪框 vs 图形真实范围） |
| `tests/adv/*.txt` | 各步骤的原始 stdout：`snap_A_extract.txt`、`snap_B_render.txt`、`snap_C_status.txt`、`snap_D_build1.txt`、`snap_E_verify1.txt`、`snap_H_build2.txt`、`snap_I_verify2.txt`、`snap_analysis3.txt`、`built_dump.txt`、`extract_stdout.txt`、`eq_render_stdout.txt`、`verify_stdout_run1.txt`、`verify_stdout_run2.txt`、`build_stdout*.txt`、`envcheck_default.raw.txt` |

---

## 8. 对"最新活代码"的复核（快照之后技能包又被改了一次）

快照冻结之后，活代码又变了（不是我改的；`tests/adv` 之外我没有任何写操作）：

```
scripts/extract.py             6fdc0d2373704a32 -> af9c59315e0a5006   ← 已变化
scripts/verify.py              adc4aca67a01c99b -> e699e2893887cb72   ← 已变化
SKILL.md / README.md / references/extraction-rules.md               ← 已变化
build.py / equations.py / p2zlib.py / mathmap.py / p2zconsole.py / p2zpaths.py / preamble-zh.tex / translation-rules.md  ← 与快照相同
```

我用最新活代码对我们的样张完整重跑了一次（`tests/adv/work3`、`tests/adv/work3_dpi600`）：

**已修复的**

| 问题 | 快照版 | 最新活代码版 | 证据 |
|---|---|---|---|
| **S1** 图片整体误裁（fallback=nearest-cluster、整页级 crop） | `fallback=nearest-cluster`；fig1 469x656pt（61% 页） | **`region=below` / `region=above`；fig1 329x140pt、fig2 349x140pt、fig3 371x159pt** | `work3/extracted/figures/figures-manifest.md` |

新增的 `_figure_internal_ids()`（把"图内轴标签/图例"识别出来、不再当作边界块）以及"用其它题注当上下边界、同页多图不重复占用同一图形簇"的处理，正好命中我在 S1 里给出的根因。

**仍然存在的**（最新活代码上逐条复现）

| 问题 | 最新活代码上的真实输出 |
|---|---|
| **S2** 表格截图截到第 1 页 | `"image=table02.png rect=(0, 0, 595, 842)"`；`table02.png 4961x7016 与第1页@600dpi 逐字节相同 = True` —— 完全没变 |
| **S3** 公式静默截断 | eq02 草稿仍是 `\lambda^{eff}=,c+d/e`（conf 1.00）、eq03 仍 `S_{n}= \sum_{i=1} _{i},`（conf 0.80，且 render 仍报 `! Double subscript.` 失败）、eq06 仍 `\int T¯=1`（conf 0.82）；只有 eq04 被判"符号守恒不通过" |
| **S4** 带竖线表格压行 | `table01.tex` 仍是 2 行 4 列、`quality=1.0`，5 个试样挤在同一行 |
| **S5** `--dpi` | `--dpi 600` 仍然 `TypeError: in method 'fz_pixmap_xres_set', argument 2 of type 'int'`，`EXIT=1` |
| **M4** 无边框表格被误判为公式 | `work3` 仍是 7 条公式，eq07 原编号为空、置信度 1.00 |

**新暴露的一个残留缺陷（新修订引入/未覆盖）：整幅宽大图会被切掉一条**

```
$ python tests/adv/probe_fig_new.py tests/adv/adv_sample.pdf
Figure 1 p2 notes=['region=below']
  裁剪框 = (133.3,95.0,462.0,234.8) 329x140 ；框内图形并集 (171.6,120.4,394.8,207.5)  → 图形完全在框内，无裁切
Figure 2 p3 notes=['region=above']
  裁剪框 = (123.2,404.5,472.1,544.3) 349x140 ；框内图形并集 (167.8,430.3,391.0,509.8)  → 无裁切
Figure 3 p4 notes=['region=below']
  裁剪框 = (90.5,95.8,461.9,255.1) 371x159
  框内图形并集 = (109.2,137.1,502.3,238.1) 393x101
  → **右侧被切掉 40.4pt**（框右边界 461.9 < 图形最右 502.3）
```
图 3 是整幅宽的两面板图，子图 (b) 的网格/边框一直画到 x=502.3，而裁剪框右边界是 461.9，即**子图 (b) 右侧约 21% 的宽度被切掉**。这说明"整幅宽大图"的裁剪仍会漏掉未被选中的图形簇：`tight = rect_union(chosen + 区域内短文本)` 只并入了"命中探测带的簇"和"短文本块"，而伸到右侧的图形簇既没命中 14.9pt 高的探测带、也不是文本，于是被排除在外。**结论：题述"跨栏/整幅宽大图会不会被切掉"的答案是——会（本样张实测右切 40.4pt）。**

**建议**：`tight` 的并集应至少并入"与 `region` 相交的全部图形簇"，而不只是 `chosen`；或对 `region=above/below` 命中的簇向外扩张到所有与之连通（gap≤10pt）的簇。
