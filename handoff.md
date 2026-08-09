# PDF Protocol Diff Handoff

- task_id: `pdf-diff-accuracy-phase1-20260810`
- goal: 在保留可回退基线的前提下，提高疑难 PDF 的正文、表格、公式和视觉变化识别率，并建立不可假绿的可量化验收。
- repository: `ZhenlongYou/pdf-protocol-diff`
- canonical_path: `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- persistent_project_branch: `project/pdf-protocol-diff`
- base_main: `67891f01dbab0aebc3e8f16cec89a495f22b028e`
- implementation_commit: `69857da98b0789bb364a0c53f1b7160e65af5ab4`
- status: `accepted_ready_for_delivery`
- real_entrypoint: `.venv/bin/python main.py --old-pdf /Users/mac/Documents/文件对比工具/oif2024.532.04.pdf --new-pdf /Users/mac/Documents/文件对比工具/oif2024.532.05.pdf --layout-backend native --output-dir /Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/532`
- accepted_report: `/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/532/protocol_diff_20260810_041209/protocol_diff_report.html`

## Rollback Baseline

- 远端注释标签：`backup/pdf-protocol-diff-before-accuracy-phase1-20260810`
- 标签提交：`67891f01dbab0aebc3e8f16cec89a495f22b028e`
- 上一版报告：`/Users/mac/Desktop/PDF对比工具_引用出处中性验收_20260809/532/protocol_diff_20260809_145011/protocol_diff_report.html`
- 安全回退步骤见 `docs/回退说明.md`；禁止用 `git reset --hard` 或强制推送覆盖历史。

## Implemented

- 报告顺序保持“表格补充证据 → 公式复核 → 技术正文”；读者层继续隐藏纯 Section/Table/Figure/Condition/Equation 引用编号顺延，JSON/CSV 保留原始审计事实。
- 工程数值、限值、单位、正负号及大小写技术标识符严格比较；编号中和只作用于可证明的定位语法，不能吞掉 `mV/MV`、`UI/ui`、`CMIT-LT/CMIS-LT` 等变化。
- 视觉漏检哨兵绑定抽取快照 SHA，记录完整覆盖审计；屏蔽坐标已证明的页眉、页脚、页边行号和已有表格/公式证据；小型连续矢量符号仍会触发人工复核卡。
- 页眉/页脚只屏蔽实际 word 紧框；完整页脚簇可含相邻续行，但附近独立小图形不被整带遮住。页眉还必须由同一标题覆盖至少 80% 选定页的跨页证据证明，每页不同的技术标题保留。
- 唯一文字页可跨插页或换序配对；重复、空文字或其它未安全配对页失败关闭。多页章节只允许具体读者片段在 `page_bodies` 中的唯一精确 occurrence 覆盖所在页，禁止一张第一页正文卡替整节其它页面担保。
- 仅引用编号不同的页当前只有整行坐标，为避免吞掉同行小图形，不屏蔽整行、不认证视觉一致。若整页发生移动、换行或重排，也不生成整页红色假差异卡；两类情况均明确记录视觉覆盖未完成并阻止 all-clear。
- Gold Accuracy 按具体 C/T/F/V 卡片和 HTML/Markdown/TXT 三个独立读者面核对可见性、事件 occurrence 及技术 token 边界；默认要求视觉覆盖完整。仅语义 benchmark 可显式设置 `visual_coverage_required: false`，输出仍公开 `visual_coverage_complete=false`，且包含视觉事件时禁止豁免。
- Docling 保持显式可选和快照哈希绑定，但本期只接受与原生抽取逐字符完全一致的候选。复杂多栏重排的所有者绑定仍未证明，相关增强明确延期，不能宣称已经修复。

## Acceptance Evidence

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`：`865` 项通过，`0` 失败，耗时 `413.691s`。
- 受控解析基准：`5/5 PASS`；混合 Corpus：`3 PASS / 0 FAIL / 6` 个缺少可选 PDF 的明确 skip。
- 真实 532 语义 Gold：`2/2` 关键事件匹配，recall 与 critical recall 均为 `1.0`，false negative 为 `0`；该 case 显式声明不认证视觉覆盖，`visual_coverage_required=false`、`visual_coverage_complete=false`、`visual_recall=null`、`precision=null`。
- 最终 532 报告：`35` 张原始正文审计卡、`12` 张原始表格审计卡、`7` 项公式证据、`0` 张视觉变化卡；视觉审计为 `eligible=1, checked=0, failed=1, unmatched=18, complete=false`，旧 29 / 新 28 的版式回流明确失败关闭，没有伪造整页差异截图。
- Markdown 顺序为表格第 `35` 行、公式第 `152` 行、技术正文第 `197` 行；真实 Chrome 已成功渲染最终 HTML 并保存首屏截图，首屏从表格补充证据开始。
- 浏览器可见正文中，用户截图的 Table 列表和 Section 范围旧/新原句均不存在；`CMIS-LT` 与 `53.125 GHz` 可见，错误视觉复核区不存在。
- `git diff --check`、修改文件 Ruff `F/E9/I`、`compileall` 均通过；`.venv/bin/python main.py --gui-smoke-test` 与系统入口 `python3 gui_app.py --smoke-test` 均退出 `0`。

## Independent Review

- correctness reviewer `019fcb67-bcc9-7270-988d-113d07697892`：对 exact `69857da98b0789bb364a0c53f1b7160e65af5ab4` 给出 `PASS`，P1/P2=`0/0`，并完成 commit-bound attestation（task `accuracy-phase1-correctness-20260810`）。
- regression reviewer：对同一 exact commit 给出 `PASS`，P1/P2=`0/0`；独立复跑跨页片段、小图形、页眉页脚、真实 532、Gold/Docling/reader 矩阵，未发现重要遗留。

## User-Facing Artifacts

- 最终 HTML：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/532/protocol_diff_20260810_041209/protocol_diff_report.html`
- Gold summary：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/gold_accuracy_532_final.json`
- 受控解析 summary：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/controlled_parsing_benchmark_final.json`
- 混合 Corpus summary：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/corpus_summary_final.json`
- Chrome 渲染截图：`/Users/mac/Desktop/PDF对比工具_识别率一期最终验收_20260810/最终报告浏览器截图.png`

## Delivery Policy

- 两名 reviewer 已对 exact implementation commit 给出 `PASS` 且 P1/P2=`0/0`。
- 交付时先推持久项目分支 `project/pdf-protocol-diff`，再把 `main` 快进到同一最终提交并推送；远端备份标签永久保留。
- 最终远端项目分支与 `main` OID 在 GitHub 推送后由交付回复记录；本文件不写入包含自身的递归 commit OID。
