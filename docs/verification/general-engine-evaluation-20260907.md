# 通用 PDF 对比引擎：第一阶段实测与接入结论

本阶段完成了可重复的解析器评测入口和保留来源身份的候选匹配接口。**尚未替换桌面默认引擎，也未解决整份 OIF 报告的准确性问题。** 现有长文档进度与终止功能保持原样。下一阶段必须从原始页面、文字及区域关系接入，不能直接复用已经清洗和按章节重排的旧结果。

## 评测方法与证据

本机为 Mac14,10、16 GiB 内存、12 个逻辑 CPU。四个引擎对每个案例读取同一份按物理页生成的 PDF 快照，分别启动独立进程；候选依赖安装在独立环境中。每次运行保存输入 SHA-256、页码映射、引擎版本与设置、原始输出、评测脚本及预期清单。没有预期检查的运行记为 `INCONCLUSIVE`，不能记为通过。[评测实现](../../tools/evaluate_parser_candidates.py)

八组案例包括四个可控版面样本，以及 OIF 两版本的已知反例页、Tektronix DPOJET 手册和一篇双栏论文。可控样本检验正负号、中文、阅读顺序、小图与合法表格；真实样本检验选定正文、图表区域与图注。厂商手册人工参照为 DPOJET 077004818，物理第 88 页／印刷第 64 页 Table 29；论文为 Yeh、Barry 的 *Adaptive Minimum Symbol-Error Rate Equalization for Quadrature-Amplitude Modulation*，物理第 1、5 页的双栏内容及 Figure 1–4。

这些是开发与已知反例回归材料。**八组检查通过数不是识别准确率**：未逐字符标注整份文件，也没有形成按文档家族隔离的独立验收集。表格行列对应、跨页表格、扫描件、混合页、公式和所有真实增删尚未系统验收。普通表格的文字包含检查不能替代单元格归属检查。

本地证据根为 `work/general-evaluation/`。`reviewed/manifest.json`、`reviewed/summary.json` 保存 32 次同机运行；`reviewed/final-docling-checks.json` 用最终来源映射重放已保存的 Docling 原始结果，未重新执行模型。私有 PDF、原始解析内容和模型均不进入 Git。

## 选定检查的结果

| 案例 | 现有原生路径 | PyMuPDF4LLM 1.28.2 | Docling 2.126.0 | OpenDataLoader 2.5.7 |
|---|---|---|---|---|
| 英文、正负号 | 通过 | 通过 | 通过 | 通过 |
| 中文 | 通过 | 通过 | 通过 | 通过 |
| 可控双栏（内部文字交错） | 通过 | 失败 | 失败 | 失败 |
| 可控小图与表格 | 失败 | 通过 | 通过 | 失败 |
| OIF 新版选页 | 通过 | 通过 | 通过 | 通过 |
| 厂商手册选页 | 失败 | 通过 | 通过 | 通过 |
| 论文选页 | 失败 | 通过 | 通过 | 失败 |
| OIF 旧版反例页 | 失败 | 失败 | 通过 | 失败 |

来源：上述本地运行记录。可控双栏是作者明确指定的阅读顺序，其稀疏布局也暴露了结构解释的歧义；不能据此断言某引擎对所有双栏文件都较差。OIF 新版选页检查较少，其“通过”尤其不能解释为整份新版解析正确。

补充对照使用两份渲染像素完全相同的双栏 PDF，只改变内部文字的写入顺序。在文字按整列写入时 Docling 与 OpenDataLoader 通过，左右列逐行交错写入时两者失败；现有原生路径两份都通过，PyMuPDF4LLM 两份都失败。页面由 PyMuPDF 按 2 倍尺寸渲染，像素 SHA-256 完全一致。这是本次样本的实测现象，说明内部存储顺序也需要成为通用工具的验收维度。[本地证据：`stream-order-fixtures/identical-appearance.json`、`stream-order-check/summary.json`] 可控输入生成器同时保留这两个变体，因此现在生成五个样本；上表仍对应最初冻结的八案例，不混改历史结果。

## 长文档耗时

| 输入 | 页数 | PyMuPDF4LLM | Docling |
|---|---:|---:|---:|
| OIF CEI 5.2 | 656 | 95.47 秒 | 451.00 秒 |
| OIF CEI 5.3 | 685 | 102.24 秒 | 437.86 秒 |
| 两份合计 | 1,341 | 197.71 秒 | 888.86 秒 |

来源：`work/general-evaluation/long/summary.json`。这是同机新进程的管线耗时，包含依赖导入、初始化、转换、原始输出保存、结果规范化和选定检查；模型已在较早的小样本运行中下载。**不包含版本比较和报告生成，也不是第一次安装或模型下载的耗时。** 两引擎未启用 OCR；OpenDataLoader 本阶段只测试本地非 hybrid 路线。测试记录的 worker RSS 不包含 JVM／OCR 等子进程，不能当成整个任务峰值内存。

长文计时绑定原先冻结的 runner；随后修正了 Docling 输出映射，通过 `long/*/docling/final-projection.json` 重放原始结果。最终投影按各自 `charspan` 分配跨页正文，不再向每页复制整段；单来源列表项保留文字并记录字符范围口径差异。两份最终投影均无被清空的列表项。计时和修正后投影分开保存，不能把旧计时标成最终适配器重新跑出的时间。

Docling 在选定的 OIF 正文和非表格区域检查中通过，但长文日志仍有表格单元未归入行列的警告。因此不能用模型生成的表格文本直接覆盖原始文字，更不能据此宣布所有表格准确。

## 实施决定

1. **原始来源与结构解释分开保存。** 每个原文出现位置需要文件身份、物理页和坐标；标题树、正文／图表角色与阅读顺序属于可失败的解释。Docling 的结构化文档支持页码、边界框及字符来源，但调用方仍须正确消费这些信息。[Docling 文档结构](https://docling-project.github.io/docling/concepts/docling_document/)
2. **原生文字作为基础，结构引擎继续参与候选评估。** PyMuPDF4LLM 的本次速度和 Docling 的部分复杂区域表现各有价值；当前证据不足以确定统一替换或自动路由阈值。后续应直接核对原始文字与结构区域，验证冲突处理后再接入桌面。
3. **同字串不等于同一处内容。** 新接口给每处内容单独身份，匹配后不能再次消费；顺序逆转可能改变参数所属条件，应保留对应关系待确认。未解释的对侧内容可能是移动、拆分或修改，不能直接授权删除。数值正负号、十进制点、否定词和词间分界均保留。[候选接口](../../src/protocol_pdf_diff/evidence_alignment.py)
4. **同时衡量自动完成覆盖率。** 旧 OIF 提取结果直接桥接新接口时，1,341 页全部未决：旧的整页正文与坐标块不是可逆的一一对应关系。该接入路线已拒绝作为默认功能；“没有误报”不能掩盖“没有自动解决”。本地记录为 `occurrence-oif-replay.json`，仅重放既有提取结果，不是新的完整 PDF 运行。

补充的直接接入验证绕过旧章节结果，使用两份完整 Docling 原始解析输出保留的页码、坐标和区域类型。通用的两级锚点匹配找回了用户反例中物理第 351、352、381 页的共同正文；重复短句只有在可靠的局部范围内才确认对应，全部最终配对统一检查顺序交叉。这里没有使用 OIF 名称、特定页码或原句作为算法条件。[直接接入入口](../../tools/compare_parser_evidence.py)；本地逐项证据：`docling-occurrences-final/known-occurrences.json`。

这次匹配约 0.24 秒，不含之前的解析、JSON 读写或图表报告生成。两边合计 38,561 个非空文字区域，仍有 19,454 个来源单元未决；计数包含页边信息和行号，不能解释为正文错误率。这证明直接接入比旧结果桥接更有希望，但未达到正式发布条件。[本地记录：`docling-occurrences-final/summary.json`]

新评测结果在解析进程中记录完整输入 SHA-256，并在结束时检查文件未变；读取候选结果时拒绝不同输入或无绑定的旧结果。历史 Docling 原始输出只带截断至 64 位的输入 SHA-256，本次重放检查该值，并明确记录 `historical_source_binding_is_truncated`；它不是新增的完整输入／输出证明。来源口径依据本次安装的 docling-core 2.95.0 `types/doc/common/origin.py` 中 `DocumentOrigin.parse_hex_string()`，未补写成“当时已经记录完整哈希”。

## 可运行入口与验证边界

生成可控输入：

```sh
.venv/bin/python tools/build_parser_evaluation_fixtures.py --output work/parser-inputs-new
```

评测已安装的引擎；输出目录必须是新的，旧证据不能覆盖：

```sh
.venv/bin/python tools/evaluate_parser_candidates.py --manifest work/parser-inputs-new/manifest.json --output work/parser-run-new --engines native pymupdf
```

需要 Docling、PyMuPDF4LLM 或 OpenDataLoader 时，通过 `--engine-python` 指定隔离环境；OpenDataLoader 还需要可用 Java。重型模型不会在生产入口自动安装。

候选匹配公开入口：

```sh
.venv/bin/python tools/compare_document_evidence.py old.pdf new.pdf --output work/candidate-new
```

它输出来源 JSON 和文字核验 HTML，明确显示未决内容与相互冲突的来源视图，尚未具备正式报告的图表展示与完整任务控制。原文移动目前只保留对应候选，不认证移动后的所属条件不变。

已有冻结解析结果可直接评估来源配对，参数是评测输出中的两份案例目录：

```sh
.venv/bin/python tools/compare_parser_evidence.py work/parser-run/old-case work/parser-run/new-case --engine docling --output work/parser-correspondence-new
```

此入口输出完整来源与配对 JSON，不替代正式 HTML 图表报告。历史数据缺少可验证输入绑定时会拒绝读取。

限定回归验证使用六类手写预期、独立原文／身份检查、三项实际故障注入和新进程 PDF 入口。执行方式：

```sh
.venv/bin/python docs/verification/build_occurrence_evidence.py
.venv/bin/python /Users/mac/.codex/skills/test-effectiveness-gate/scripts/check_gate.py docs/verification/occurrence-evidence.json --receipt work/occurrence-receipt-new.json
```

该回执即使通过，也只证明声明的候选回归合同。生产反例仍在 [开放缺陷账本](escaped-defects.yaml) 中；OIF 原始报告、跨来源独立验收、扫描路线、真实 100／500／1,000 页规模与不同修改密度的完整对比，仍属于后续工作。新管线通过这些检查前，不发布“通用准确性已保证”或完整对比加速倍数。
