# PDF Protocol Diff Handoff

- task_id: `pdf-diff-accuracy-phase1-20260810`
- goal: 在保留可回退基线的前提下，提高疑难 PDF 识别率，并防止规则、报告降噪和准确率认证退化成 OIF 专用实现。
- repository: `ZhenlongYou/pdf-protocol-diff`
- canonical_path: `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- persistent_project_branch: `project/pdf-protocol-diff`
- base_main: `86e1a26e6cc7c20abf7905fbddd3935ce6e41165`
- implementation_commit: `pending_final_commit`
- status: `validated_pending_commit_bound_review`
- oif_entrypoint: `.venv/bin/python main.py --old-pdf /Users/mac/Documents/文件对比工具/oif2024.532.04.pdf --new-pdf /Users/mac/Documents/文件对比工具/oif2024.532.05.pdf --layout-backend native --output-dir /Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/532-final-v10`
- non_oif_entrypoint: `.venv/bin/python main.py --old-pdf '/Users/mac/Documents/New project/work/word_render_v1/PCIe_technical_report_word_v1_20260704.pdf' --new-pdf '/Users/mac/Documents/New project/work/word_render_v2/PCIe_technical_report_word_v2_20260704.pdf' --layout-backend native --output-dir /Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/pcie-final-v14`
- accepted_oif_report: `/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/532-final-v10/protocol_diff_20260811_041505/protocol_diff_report.html`
- accepted_non_oif_report: `/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/pcie-final-v14/protocol_diff_20260811_041346/protocol_diff_report.html`

## Rollback Baselines

- 本轮本地注释标签：`backup/pdf-protocol-diff-before-generality-hardening-20260810`
- 标签提交：`86e1a26e6cc7c20abf7905fbddd3935ce6e41165`
- 上一阶段远端注释标签：`backup/pdf-protocol-diff-before-accuracy-phase1-20260810`
- 上一阶段报告：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/532/protocol_diff_20260810_041209/protocol_diff_report.html`
- 安全回退步骤见 `docs/回退说明.md`；禁止用 `git reset --hard` 或强制推送覆盖历史。

## Generality Hardening

- 同一 SHA-256 字节快照且选择页窗相同，正文、表格和公式语义变化固定为空；解析器自身不稳定不能给同一文件制造变化卡。抽取与视觉覆盖风险仍由 assessment/provenance 保留，不能冒充可靠解析。
- 读者层不再凭词形把 `MCB`、`HCB`、`TP4a`、`scope`、`reference`、`Generator Calibration` 等短技术文本猜成图示噪声；在取得真实图形区域归属证据前，这类标签保持可见。
- 普通编号章节下的完整叙述句列表以“已有编号父章节、严格连续、句子形态明确、相邻后项证明”作为结构证据；前瞻按内容连续性跨页扫描到下一结构边界（最多 256 个非空片段），不再写死两页。普通正文、章内表格或下一真实章节会使歧义候选失败可见；Part/Annex/Appendix 及中英文附录边界均会结束列表。已证明列表的换行末项后紧接父级表格时，表格不能反向把末项伪造成章节。规则不使用 OIF、PCIe 或厂商关键词。
- 位于顶部窄区域且在绝大多数选定页重复的运行页眉从正文流分离，但原文按页作为独立比较单元保留；两版重叠页的精确页眉序列相同、只在末尾多/少页面时，额外页眉按页数家具处理。`ALPHA→BETA`、`ALPHA→alpha` 或相同页数内的跨页分布变化仍会进入五种报告输出。
- 带框 `Note:` 说明块只有在所有非空行稳定落在同一物理列时才退出表格路径；Figure 内小线框需要明确图题、短标签区和无正文句三项坐标证据。PCIe Note 框不再产生 11 张伪表卡，OIF 的 MCB/HCB/Reference 图示小框也不再冒充表格；Figure 与候选网格之间如有更近的明确 Table 表题，真实小表失败可见。
- Gold Accuracy 新增 `minimum_distinct_families` 和 case `family`；只计算实际产出指标且两侧源 SHA 不同的版本对，同一 old/new 字节对重复申报不同 family 只计一次，family 先规整内部空白与大小写。family 名仍是经审阅 manifest 的受信声明，不自动证明出版方身份。
- README 与 corpus 规则明确：当前随仓 Gold 示例只有 `oif-serdes` 一个文档族，不能单独认证跨标准通用性；对外通用性结论至少需要一组非 OIF 的真实 old/new 人工标注版本对。
- 源码门禁继续扫描共享行为模块中的 OIF/PCIe 出版方字面量；未知正文、单位、技术标识符和领域缩写默认保留比较。个别表格/公式恢复器仍包含工程符号和调制枚举作为正向识别信号，但不能触发读者层隐藏，也不能使未知文档得到假一致结论。

## Preserved Safety Contracts

- 报告顺序保持“表格补充证据 → 公式复核 → 技术正文”；读者层隐藏纯 Section/Table/Figure/Condition/Equation 引用编号顺延，JSON/CSV 保留原始审计事实。
- 工程数值、限值、单位、正负号及大小写技术标识符严格比较；编号中和只作用于可证明的定位语法，不能吞掉 `mV/MV`、`UI/ui`、`CMIT-LT/CMIS-LT` 等变化。
- 视觉漏检哨兵绑定抽取快照 SHA，重复、空文字或未安全配对页面失败关闭。仅引用编号页、复杂重排页和缺失逐字符坐标页不能靠整行屏蔽认证一致。
- Docling 保持显式可选和快照哈希绑定，但当前只接受与原生抽取逐字符完全一致的候选。复杂多栏重排的所有者绑定仍未证明，相关增强继续延期。

## Acceptance Evidence

- 全量：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test*.py' -v`，`906` 项通过、`0` 失败，耗时 `326.117s`。
- 目标 RED/GREEN：同一快照伪表卡、SerDes 缩写隐藏、同家族伪覆盖、通用编号叙述链均先证明旧实现失败，再由公开入口回归变绿。
- 受控解析基准：`5 PASS / 0 FAIL / 0 SKIP`。
- 混合 Corpus：最终代码重跑为 `8 PASS / 0 FAIL / 1 SKIP`；三组 OIF 真实版本、PCIe PHY 真实版本、JLT 论文、表单、幻灯片和 OIF 自比均通过。PCIe PHY 页窗为 `2` 个正文变化、`0` 个表格变化并因非对齐页窗诚实降级；唯一 skip 是未提供的可选扫描 PDF。运行后用于集合样本的临时 PDF 副本已删除，只保留 summary 和最终报告。
- Gold 示例：`1 PASS / 0 FAIL / 0 SKIP`，family coverage `required=1/executed=1/complete=true`；2/2 事件命中，recall 与 critical recall 均为 `1.0`。它仍只认证单一 OIF 家族。
- 最终 OIF 532：`35` 张原始正文审计卡、`12` 张原始表格审计卡、`7` 项公式证据、`0` 张视觉变化卡；视觉覆盖保持 incomplete 并阻止 all-clear。v10 的 HTML/Markdown/TXT 隐藏截图中的 Table/Section 纯引用原句，JSON 保留原始事实，`33.5 dB`、`53.125 GHz`、`CMIT-LT/CMIS-LT` 均可见，MCB 表题误检为 `0`；固定页眉不因 36/35 页数差制造新卡。
- 非 OIF PCIe 技术报告：旧 `62` 页、新 `65` 页，JSON 为 `30` 个原始正文变化、`58` 个表格变化/复核组、`0` 个公式、`0` 张视觉变化卡；读者首页显示 `28` 个核心技术变化。`4..9` 数据路径、`1..3` 总结句、`2. Link Equalization` 与 `202、234…` 均不成为假章节，只在所属正文卡中保留真实编号/内容变化。报告仍因多栏、表格归属和视觉覆盖不足明确为 degraded，没有假装全量可靠。
- 先前同一渲染器的 v3 HTML 已在应用内浏览器实测首屏表格前置、OIF h2 顺序为表格/公式/正文且 PCIe 人工复核提示可见。v10/v14 再核对 HTML h2、Markdown 章节顺序、三种读者文字和 JSON；OIF Markdown 顺序为第 35/152/197 行。应用内浏览器安全策略拒绝直接打开新的本地 `file://`，未绕过该限制，也不把静态检查冒充新的视觉验收。
- `.venv/bin/python main.py --gui-smoke-test` 与系统入口 `python3 gui_app.py --smoke-test` 均退出 `0`；本轮核心 Python 文件 Ruff `F/E9/I`、全源码 `compileall`、`git diff --check` 均通过。全仓 import 排序仍有既有格式债务，不作为本轮功能门禁。

## Remaining Evidence Boundary

- 当前没有第二个“非 OIF 真实 old/new + 人工完整事件标注”的 Gold case，因此本轮证明了核心规则不依赖出版方、混合 self-diff 不假报、非 OIF 真实入口可运行，但不宣称已经量化认证跨标准变化召回率。
- 扫描件、复杂多栏、表单、幻灯片和图形语义仍不属于可靠自动判等范围；工具应降级或失败关闭，而不是为了减少卡片自动隐藏。

## Independent Review

- 新实现提交尚待两名只读 reviewer 对同一 exact commit 复核和 commit-bound attestation；上一阶段对 `69857da98b0789bb364a0c53f1b7160e65af5ab4` 的 PASS 不能替代本轮审查。

## User-Facing Artifacts

- OIF HTML：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/532-final-v10/protocol_diff_20260811_041505/protocol_diff_report.html`
- 非 OIF HTML：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/pcie-final-v14/protocol_diff_20260811_041346/protocol_diff_report.html`
- Gold summary：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/gold_accuracy_final_v4.json`
- 受控解析 summary：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/controlled_parsing_benchmark_final_v4.json`
- 混合 Corpus summary：`/Users/mac/Desktop/PDF对比工具_通用性增强验收_20260811/corpus_summary_final_v4.json`

## Delivery Policy

- 完成前必须取得两名 reviewer 对最终 exact commit 的 PASS，且 unresolved P1/P2=`0/0`。
- 交付时先推持久项目分支 `project/pdf-protocol-diff`，再把 `main` 快进到同一最终提交并推送；远端备份标签永久保留。
- 最终远端项目分支与 `main` OID 在 GitHub 推送后由交付回复记录；本文件不写入包含自身的递归 commit OID。
