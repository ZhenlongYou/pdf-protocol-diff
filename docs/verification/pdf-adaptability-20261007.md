# PDF 适用性：2026-10-07 定向验证

本轮修复扫描文字合并、分区阅读和无标题纯重分页，增加局部截图 OCR、固定图示补查、统一覆盖清单，以及协议/通用用途和累计 OCR 设置。验证只抽取指定页窗，没有重跑整本 PDF。以下 PASS 只对应列明的预期，不能解释为整页完整标注、跨标准准确率或公式语义已验证。

## 环境与代码

- 仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`；任务分支 `codex/pdf-adaptability-20261007`，基线 `c2dbc9a511d91d7647d1660e1652bded520e97be`；最终代码身份为包含本记录的任务提交。
- 解释器：项目 `.venv/bin/python`，Python 3.12.1；pdfplumber 0.11.10、PyMuPDF 1.28.0、pypdfium2 5.11.0、pytesseract 0.3.13、Pillow 12.3.0、Tesseract 5.5.2。
- 主代理修改源码，两个只读子代理协助建立原页预期及抽取观察；未启用独立交付审查或 STRICT 门禁。真实比较和生成反例均关闭 OCR 缓存。

## 已观察结果

| 场景 | 输入及预期 | 本次结果 |
|---|---|---|
| 扫描大小写与重复句 | 脚本生成 1 页栅格 PDF；分别只删除一次 `Call enable`、一次重复要求 | PASS：各只报告预期的一次删除，没有额外新增/替换；仍需人工复核，约 2.71/1.17 秒 |
| OCR 额度耗尽 | 同一扫描 PDF，额度 0 且缓存关闭 | PASS：保留累计 OCR 耗尽原因，不允许“无差异”；桌面实际选择通用模式、中英语言和 0 分钟后，JSON 确认设置到达生产入口 |
| 混合页面小截图 | 正文不变，独立栅格截图 `3.3 V→5.5 V` | PASS：单独一个图像文字复核项，未制造正文条款修改；最终脚本约 1.17 秒 |
| 正文与图示同时变 | 正文限值改变，固定图题下蓝圆变红圆 | PASS：正文变化保留，1 个固定图示区域提示变化；整页覆盖仍为不完整，约 0.21 秒 |
| OIF Np | `oif2024.058.11.pdf` 物理 p13 → `.13.pdf` p15；原页 32.3.1.6.1 和 32.3.1.6.2 两处 Np 53→60，Dp 不变 | PASS：两个替换各落在正确章节和物理页；约 1.04 秒 |
| OIF 图示负例 | `OIF-CEI-5.1.pdf` p105 → `OIF-CEI-05.3.pdf` p109，Fig.2-21 与相邻技术内容经原页查看无技术改动 | PASS：无实质正文/表格变化；保留 1 个中性复核项。原图并非逐像素相等，不能当像素一致样本；约 4.23 秒 |
| CEM 跨页表格 | r4 p42–43 → r5.1 p51–52；Table5/6 → Table4-1/4-2，新增 12V-2x6 Connector | PASS（有限）：各表题及新连接器锚点保留，表格仍作为一个不确定对应组供复核；并未证明逐单元格正确；约 6.62 秒 |
| AMSER 窄双栏 | `amser.pdf` p1/p5，对照原图检查整栏顺序、字形坐标与图注 | PASS：88/46 个比较块，697/419 个精细词；非空白字符 3818/2046，重排前后完全守恒，全部精细词坐标/字体/字号保留；左右栏不交错，Fig.1/4 在右、Fig.2/3 在左；两页共约 2.20 秒 |
| DPOJET 参数表 | `DPOJET-077004818.pdf` 物理 p88（印刷 p64），Table29 和 explicit-clock 段落 | PASS：1 表 8 行，PLL Model、Damping、JTF BW、Loop BW 的 Type II 条件各属原行；`not derived` 仍属正文。约 1.38 秒；仅抽取检查，不代表跨版本准确率 |

OIF 两对和 CEM 的完整本地路径、每侧 SHA256、物理页及先验事件见[切片清单](pdf-adaptability-slices-20261007.json)。原文件不随代码提交；验证工具拒绝哈希失配、空预期和单侧超过三页的清单。

补充来源：

- AMSER：`/Users/mac/Documents/嵌入去嵌/sources/2026-09-05-equalization-research/publications/amser.pdf`，SHA256 `0f597fb1f9777d731d02a8e7ea18757c297782460c795ba956c48154947df3e3`，物理 p1/p5。原页两栏间距约 12pt。验证时 `region_reading.py` SHA256 为 `5ac0166486de320eba79f85e4660767c471422e39ffdfaf4b9ca01601a29826a`。
- DPOJET：`/Users/mac/Documents/嵌入去嵌/outputs/cdr-research-rebuild-20260906/DPOJET-077004818.pdf`，SHA256 `0f8954d26a5dcd749c6127333e8bd4c833fc46623384d40ad2fe8cd4f5965411`。Table29 bbox 为 `[186.42,106.46,553.02,314.34]`，explicit-clock 段落位于其下。表格行在扁平文本末尾是既有表示方式；源表格坐标与行归属仍保留。
- Np 原页定位：32.3.1.6.1 旧/新 y200.48–216.86pt；32.3.1.6.2 y397.43–413.81pt。以上预期来自原 PDF 文本和原页查看，不以本工具自己的输出充当真值。

## 自动与可见检查

最终相关回归 **294 项通过，8.708 秒**。包含旧读者准确性反例，保护大小写、否定、数值及未检查范围；新增测试覆盖无标题 2→3 页纯重分页及真实改字反例、全宽标题/窄双栏、数值表不误重排、OCR 次数与预算、通用信息在多种报告格式保留、固定图示范围及 GUI 配置。另对 31 个变动 Python 文件完成 AST 解析，`git diff --check` 无异常。未跑完整单测套件、全书准确率或外部语料总门禁。

```bash
PROTOCOL_PDF_DIFF_OCR_CACHE=off .venv/bin/python -B -m unittest \
  tests.test_reader_accuracy_regressions tests.test_comparison_profile \
  tests.test_fallback_repagination tests.test_image_regions tests.test_graphic_region_review \
  tests.test_visual_coverage_disclosure tests.test_content_only tests.test_ocr_policy \
  tests.test_page_ocr tests.test_ocr_cache tests.test_region_reading tests.test_parser_routing \
  tests.test_layout_blocks tests.test_evidence_alignment tests.test_options_validation \
  tests.test_webview_gui tests.test_candidate_visual_evidence -q

.venv/bin/python -B tools/verify_adaptability_slices.py \
  --output work/adaptability-rerun \
  --manifest docs/verification/pdf-adaptability-slices-20261007.json
```

第二条命令要求输出目录尚不存在；只需受控反例时省略 `--manifest`。各样本没有随机采样，因此没有随机种子。抽取补充页可用公开 `extract_pdf_text(path, start_page=p, end_page=p)` 分别运行，禁止将 p1/p5 写成整个 p1–5 范围。本轮最后的导入清理、提示文字、兼容字段顺序及英文选项标签修正不改变已验证的 PDF 抽取行为；相关回归覆盖最终代码，不因这些改动重复整批真实 PDF。

已在实际桌面窗口操作设置和一次扫描比较；已在浏览器查看真实生成的 Np 报告、展开统一覆盖清单和混合页 OCR 明细，点击图示核对区域，确认蓝/红圆原图定位。HTML 在实际窄窗口可阅读。最终英文选项在实际界面资源的浏览器面板中已可选择；未为这处标签修正重复运行 OCR。截图只用于验收，不作为产品输出长期保留。

## 仍有边界

- 窄栏重排是保守规则；参数列、图表/旁注归属不明时仍降级。AMSER p5 原字体已有 15 个 unknown 词和部分 cid 字符，本次只保留来源，未证明公式识别正确。
- 无标题重分页仅在有序文字精确相同时消噪；重分页同时改字的多对多匹配仍可能出现噪声。
- CEM 的跨版表格拆并、行列变化尚无完整单元格真值；旧页的部分印刷行号仍会进入文本。报告保留复核，不能宣称全表准确。
- 图示补查要求唯一原文图题、相同 bbox 和页尺寸；不覆盖移动/缩放/带原生文字图形，也不解释图形语义。
- 局部 OCR 与 Np 第一处变化的精确文字高亮尚未可靠定位；报告明确显示“截图定位未确定”，可用物理页和区域证据复核。Np 第二处与固定图示定位已实际查看。
- 通用模式保留元信息，仍沿用纯排版及引用改号过滤；不是逐字节文档对照。累计额度只限制 OCR 调用，缓存可在额度 0 时复用，不保证整个任务五分钟内结束。

## 临时数据

本轮生成的 PDF、报告、位图及诊断仅在任务 scratch 下使用；原始 PDF、旧任务 `work/page-window-gate` 和其他代理状态均保留。清理前按文件大小合计为 79,667,273 字节（约 79.7 MB），这是逻辑文件大小，不是磁盘空闲空间增量。确认无下游依赖后删除本轮八个 `work/adaptability-20261007-*` 目录（controls、final-controls、real、gui、config、accepted、mixed、region-final），三个 `/tmp/pdf_*_20261007.py` 临时助手及 `/tmp/pdf-adaptability-extraction-20261007`。子代理此前另删已看过的 PNG 126,837 字节。保留的切片清单是小型维护输入，不含 PDF 或图片；此文和脚本用于后续复跑，不保留原始运行报告。
