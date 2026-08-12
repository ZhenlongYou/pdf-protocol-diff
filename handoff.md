# PDF Protocol Diff Handoff

- task_id: `pdf-diff-accuracy-phase1-20260810`
- goal: 在保留可回退基线的前提下，提高疑难 PDF 识别率，并防止规则、报告降噪和准确率认证退化成 OIF 专用实现。
- repository: `ZhenlongYou/pdf-protocol-diff`
- canonical_path: `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- persistent_project_branch: `project/pdf-protocol-diff`
- base_main: `86e1a26e6cc7c20abf7905fbddd3935ce6e41165`
- implementation_commit: `b48eb82d24fd5245498ec17ecdbf7e68f6ad9f95`
- status: `validated_and_dual_attested`
- oif_entrypoint: `PROTOCOL_PDF_DIFF_BUILD_COMMIT=b48eb82d24fd5245498ec17ecdbf7e68f6ad9f95 .venv/bin/python main.py --old-pdf /Users/mac/Documents/文件对比工具/oif2024.532.04.pdf --new-pdf /Users/mac/Documents/文件对比工具/oif2024.532.05.pdf --layout-backend native --output-dir /Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813/532`
- non_oif_entrypoint: `PROTOCOL_PDF_DIFF_BUILD_COMMIT=b48eb82d24fd5245498ec17ecdbf7e68f6ad9f95 .venv/bin/python main.py --old-pdf '/Users/mac/Documents/New project/work/word_render_v1/PCIe_technical_report_word_v1_20260704.pdf' --new-pdf '/Users/mac/Documents/New project/work/word_render_v2/PCIe_technical_report_word_v2_20260704.pdf' --layout-backend native --output-dir /Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813/pcie`
- accepted_oif_report: `/Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813/532/protocol_diff_20260813_073607/protocol_diff_report.html`
- accepted_non_oif_report: `/Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813/pcie/protocol_diff_20260813_073717/protocol_diff_report.html`

## Rollback Baselines

- 本轮本地注释标签：`backup/pdf-protocol-diff-before-generality-hardening-20260810`
- 标签提交：`86e1a26e6cc7c20abf7905fbddd3935ce6e41165`
- 上一阶段远端注释标签：`backup/pdf-protocol-diff-before-accuracy-phase1-20260810`
- 上一阶段报告：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/532/protocol_diff_20260810_041209/protocol_diff_report.html`
- 安全回退步骤见 `docs/回退说明.md`；禁止用 `git reset --hard` 或强制推送覆盖历史。

## Generality Hardening

- 同一 SHA-256 字节快照且选择页窗相同，正文、表格和公式语义变化固定为空；解析器自身不稳定不能给同一文件制造变化卡。抽取与视觉覆盖风险仍由 assessment/provenance 保留，不能冒充可靠解析。
- 读者层不再凭词形把 `MCB`、`HCB`、`TP4a`、`scope`、`reference`、`Generator Calibration` 等短技术文本猜成图示噪声；在取得真实图形区域归属证据前，这类标签保持可见。
- 普通编号章节下的完整叙述句列表以“已有编号父章节、严格连续、句子形态明确、相邻后项证明”作为结构证据；前瞻按内容连续性跨页扫描到下一结构边界（最多 256 个非空片段），不再写死两页。显式数量引导只对紧邻数量、至多含两个修饰词的 `items/steps/observations/requirements` 等通用可枚举名词短语授权；跨行引导按未闭合句回溯并限制在 320 字符，而非固定行数。`chapters define requirements`、`cycles before/of tests`、普通正文、章内表格或下一真实章节均失败可见。规则不使用 OIF、PCIe 或厂商关键词。
- Part/Annex/Appendix/附录支持紧邻的 en/em dash 标题，后续数字章节保持在该容器下；明确分隔符、Title Case 和 sentence-case 名词短语均提供标题正向证据。空格连接时，只有闭合的 modal/auxiliary、带宾语或补语的单一或多段并列文档谓语、引用短语或中文谓语结构能证明正文句，即使 PDF 软换行暂时移除了句末标点；容器 ID 独占一行时可联合检查同页或下一物理页紧邻的下一行，但页码不连续或存在空段时失败可见，冒号、句点和破折号等强标题分隔符也不被该联合门禁推翻。标题末尾句点不能单独覆盖 Title Case/ALL-CAPS 名词证据，无宾语的谓语串、`States the Receiver Supports.`、`说明、要求和示例`、`描述、定义、缩写` 等歧义文本保持结构可见；`describes, defines, and documents the calibration method`、`contains and very clearly explains the receiver limits`、`说明及明确规定接收机限值` 等有补语句子才归正文。单个中文 `的/了/着/过` 不能作为句法证明，避免吞掉技术名词。该门禁依赖通用结构语法，而非出版方词表。
- 位于顶部窄区域且在绝大多数选定页重复的运行页眉从正文流分离，但原文按页作为独立比较单元保留；尾页只可忽略每个都已在至少两个共同页精确重复证明的页眉值，即使尾页只保留稳定值的子集。尾页新出现 `BETA`、共享页 `ALPHA→BETA`、`ALPHA→alpha` 或跨页分布变化仍会进入五种报告输出。
- 带框 `Note:` 说明块只有在所有非空行稳定落在同一物理列时才退出表格路径；Figure 内小线框需要明确图题、短标签区和无正文句三项坐标证据。PCIe Note 框不再产生 11 张伪表卡，OIF 的 MCB/HCB/Reference 图示小框也不再冒充表格；Figure 与候选网格之间如有更近的明确 Table 表题，真实小表失败可见。Figure/Table caption 必须从行首开始，数字正文与 `A-2`、`AA.2` 等字母附录均支持；几何层小写 `a-2` 可识别，但裸 `for`、`See/Refer to Table A-1` 不能冒充编号或表题。页面级真实抽取前置门已复用同一表题语法，公开 `extract_pdf_text` 入口证明带网格的 `Table A-1`/`Table AA.2` 会进入结构化表格证据。
- Gold Accuracy 新增 `minimum_distinct_families` 和 case `family`；只计算实际产出指标且两侧源 SHA 不同的版本对，同一 old/new 字节对重复申报不同 family 只计一次，family 先规整内部空白与大小写。family 名仍是经审阅 manifest 的受信声明，不自动证明出版方身份。
- README 与 corpus 规则明确：当前随仓 Gold 示例只有 `oif-serdes` 一个文档族，不能单独认证跨标准通用性；对外通用性结论至少需要一组非 OIF 的真实 old/new 人工标注版本对。
- 源码门禁继续扫描共享行为模块中的 OIF/PCIe 出版方字面量；未知正文、单位、技术标识符和领域缩写默认保留比较。个别表格/公式恢复器仍包含工程符号和调制枚举作为正向识别信号，但不能触发读者层隐藏，也不能使未知文档得到假一致结论。

## Preserved Safety Contracts

- 报告顺序保持“表格补充证据 → 公式复核 → 技术正文”；读者层隐藏纯 Section/Table/Figure/Condition/Equation 引用编号顺延，JSON/CSV 保留原始审计事实。
- 工程数值、限值、单位、正负号及大小写技术标识符严格比较；编号中和只作用于可证明的定位语法，不能吞掉 `mV/MV`、`UI/ui`、`CMIT-LT/CMIS-LT` 等变化。
- 视觉漏检哨兵绑定抽取快照 SHA，重复、空文字或未安全配对页面失败关闭。仅引用编号页、复杂重排页和缺失逐字符坐标页不能靠整行屏蔽认证一致。
- Docling 保持显式可选和快照哈希绑定，但当前只接受与原生抽取逐字符完全一致的候选。复杂多栏重排的所有者绑定仍未证明，相关增强继续延期。

## Acceptance Evidence

- 最终全量：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover`，`1035` 项通过、`0` 失败，耗时 `435.652s`；独立 regression reviewer 在 exact implementation commit 上再次得到 `1035/1035 PASS`。
- 最终修复覆盖普通数值/CJK 数词、技术运算符、章节身份配对、重复显式字段、表格重复行、混合重排+值变化、宽表 Column 标签别名与物理抽取顺序。没有以 OIF/PCIe 出版方词表作为身份依据；证明不足时保留新增/删除或“需人工复核”，不猜 replacement。
- 最终 GUI：`.venv/bin/python gui_app.py --smoke-test` 与系统入口 `python3 gui_app.py --smoke-test` 均退出 `0`；真实 Tk 根窗口、控件树、页码输入、滚动路径和字体均被创建并验证后关闭。
- 全量：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`，`933` 项通过、`0` 失败，耗时 `329.409s`。
- 目标 RED/GREEN：同一快照伪表卡、SerDes 缩写隐藏、同家族伪覆盖、通用编号叙述链均先证明旧实现失败，再由公开入口回归变绿。
- 受控解析基准：`5 PASS / 0 FAIL / 0 SKIP`。
- 混合 Corpus：最终代码重跑为 `8 PASS / 0 FAIL / 1 SKIP`；三组 OIF 真实版本、PCIe PHY 真实版本、JLT 论文、表单、幻灯片和 OIF 自比均通过。PCIe PHY 页窗为 `2` 个正文变化、`0` 个表格变化并因非对齐页窗诚实降级；唯一 skip 是未提供的可选扫描 PDF。运行后用于集合样本的 12 份临时 PDF 副本已移入废纸篓，可恢复；只保留 summary 和最终报告。Gold/受控解析/Corpus summary SHA-256 分别为 `22b15cc8d57a4a156a911126870e1b203959f05296d10c628b1296ed35912b07` / `a8fed071006168fe12f7ad42e9a5996db3be90da823bd8bad07a56b3b0fd899b` / `cf44291181202a57f3b65b96830a0435dfe7c89048b979ef1f37d73a07ae41c3`。
- Gold 示例：`1 PASS / 0 FAIL / 0 SKIP`，family coverage `required=1/executed=1/complete=true`；2/2 事件命中，recall 与 critical recall 均为 `1.0`。它仍只认证单一 OIF 家族。
- 最终 OIF 532：`35` 张原始正文审计卡、`12` 张原始表格审计卡、`7` 项公式证据、`0` 张视觉变化卡；报告明确为 `degraded / 需人工复核`，不允许无差异结论。报告 provenance.build_commit 精确绑定 `b48eb82d24fd5245498ec17ecdbf7e68f6ad9f95`；HTML/JSON SHA-256 分别为 `032cce111e7682fbe14b5e10c329d0216db8e6d11450eb1079be9cc87db63ef9` / `564bff761a8656846c094a739e316462ce6580318bc0c4fef824e0c80055a6bd`。
- 非 OIF PCIe 技术报告：旧 `62` 页、新 `65` 页，JSON 为 `26` 个原始正文变化、`58` 个表格变化/复核组、`0` 个公式、`0` 张视觉变化卡。报告因多栏、表格归属和视觉覆盖不足明确为 `degraded / 需人工复核`，没有假装全量可靠；provenance.build_commit 同样绑定 `b48eb82d24fd5245498ec17ecdbf7e68f6ad9f95`，HTML/JSON SHA-256 为 `63122c20ac7a032b3ab685bf8670354692a3114baa9e0819c806fe93614faa08` / `c54d6ac1d20dc53962dbacaf4bd64a3287e31fc55700149b1b8c1b89f4ccd37e`。
- 先前同一渲染器的 v3 HTML 已在应用内浏览器实测首屏表格前置、OIF h2 顺序为表格/公式/正文且 PCIe 人工复核提示可见。v19/v23 再核对 HTML h2、Markdown 章节顺序、三种读者文字和 JSON；OIF Markdown 顺序为第 35/152/197 行。应用内浏览器安全策略拒绝直接打开新的本地 `file://`，未绕过该限制，也不把静态检查冒充新的视觉验收。
- `.venv/bin/python gui_app.py --smoke-test` 与系统入口 `python3 gui_app.py --smoke-test` 均退出 `0`；本轮核心 Python 文件 Ruff `F/E9/I`、全源码 `compileall`、`git diff --check` 均通过。全仓 import 排序仍有既有格式债务，不作为本轮功能门禁。

## Remaining Evidence Boundary

- 当前没有第二个“非 OIF 真实 old/new + 人工完整事件标注”的 Gold case，因此本轮证明了核心规则不依赖出版方、混合 self-diff 不假报、非 OIF 真实入口可运行，但不宣称已经量化认证跨标准变化召回率。
- 扫描件、复杂多栏、表单、幻灯片和图形语义仍不属于可靠自动判等范围；工具应降级或失败关闭，而不是为了减少卡片自动隐藏。

## Independent Review

- exact implementation commit `b48eb82d24fd5245498ec17ecdbf7e68f6ad9f95` 已获两名只读 reviewer 的 commit-bound PASS，unresolved P1/P2=`0/0`。
- correctness reviewer：`019fcb67-bcc9-7270-988d-113d07697892`，review task `accuracy-phase1-correctness-20260810`。
- regression reviewer：`019fcb67-f0d1-7fc2-acdb-c2164e8e9b59`，review task `accuracy-phase1-regression-20260810`；独立全量 `1035/1035 PASS`。

## User-Facing Artifacts

- 最终验收根目录：`/Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813`
- OIF HTML：`/Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813/532/protocol_diff_20260813_073607/protocol_diff_report.html`
- 非 OIF HTML：`/Users/mac/Desktop/test/PDF对比工具_通用性增强最终验收_20260813/pcie/protocol_diff_20260813_073717/protocol_diff_report.html`
- OIF HTML：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/532-final-v19/protocol_diff_20260811_081957/protocol_diff_report.html`
- 非 OIF HTML：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/pcie-final-v23/protocol_diff_20260811_081959/protocol_diff_report.html`
- Gold summary：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/gold_accuracy_final_v13.json`
- 受控解析 summary：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/controlled_parsing_benchmark_final_v13.json`
- 混合 Corpus summary：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/corpus_summary_final_v13.json`

## Delivery Policy

- 完成前必须取得两名 reviewer 对最终 exact commit 的 PASS，且 unresolved P1/P2=`0/0`。
- 交付时先推持久项目分支 `project/pdf-protocol-diff`，再把 `main` 快进到同一最终提交并推送；远端备份标签永久保留。
- 最终远端项目分支与 `main` OID 在 GitHub 推送后由交付回复记录；本文件不写入包含自身的递归 commit OID。
