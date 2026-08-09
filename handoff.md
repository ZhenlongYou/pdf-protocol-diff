# PDF Protocol Diff Handoff

- task_id: `pdf-diff-accuracy-phase1-20260810`
- goal: 在保留可回退基线的前提下，提高疑难 PDF 的变化识别率，加入可量化 Gold 基准、视觉漏检哨兵和安全的 Docling 多解析器融合。
- repository: `ZhenlongYou/pdf-protocol-diff`
- canonical_path: `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- persistent_project_branch: `project/pdf-protocol-diff`
- base_main: `67891f01dbab0aebc3e8f16cec89a495f22b028e`
- recorded_commit: `5b6fd78d5c60dc25de87d18f342eb648e823d349`
- status: ready
- real_entrypoint: `.venv/bin/python main.py --old-pdf /Users/mac/Documents/文件对比工具/oif2024.532.04.pdf --new-pdf /Users/mac/Documents/文件对比工具/oif2024.532.05.pdf --layout-backend native --output-dir /Users/mac/Desktop/PDF对比工具_识别率一期验收_20260810/532`
- accepted_report: `/Users/mac/Desktop/PDF对比工具_识别率一期验收_20260810/532/protocol_diff_20260810_005244/protocol_diff_report.html`

## Rollback Baseline

- 远端注释标签：`backup/pdf-protocol-diff-before-accuracy-phase1-20260810`
- 标签提交：`67891f01dbab0aebc3e8f16cec89a495f22b028e`
- 上一版报告：`/Users/mac/Desktop/PDF对比工具_引用出处中性验收_20260809/532/protocol_diff_20260809_145011/protocol_diff_report.html`
- 安全回退步骤见 `docs/回退说明.md`；禁止用 `git reset --hard` 或强制推送覆盖历史。

## Implemented

- 新增文字一致页的视觉漏检哨兵：单调页面配对后以低分辨率源像素核对图片、印章、矢量图和公式绘图变化；只追加截图复核证据，不猜测技术语义。
- 视觉变化会阻止“未发现差异”结论；HTML 展示旧页、新页和差异掩膜，JSON 仅保存页码、阈值和定位元数据，避免嵌入大图。
- Docling 仍为显式可选后端；只在原生抽取已证明为版面风险页、且候选仅重排完整唯一句子行时采用。大小写、标点、数值、单位、运算符或内容变化均回退原生结果。
- 新增 Gold Accuracy 事件清单与生产入口评估器，可计算召回率、关键事实召回率、视觉召回率、漏检数；只有完整 oracle 才报告 precision。
- 表格证据仍位于报告最前，公式随后，视觉漏检证据再后，最后才是技术正文。
- 用户截图中的 Table 列表和 Section 范围引用继续从 HTML/Markdown/TXT 隐藏；新增对 `Equation ()` 抽取缺号的纯公式出处降噪，JSON/CSV 原始事实不变。
- 工程数值、限值、单位和技术标识符继续大小写敏感地严格比较；视觉哨兵和引用降噪均不改写语义差异层。

## Acceptance Evidence

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`：`828` 项通过，`0` 失败；`compileall`、`git diff --check` 通过。
- 受控解析基准：`5/5 PASS`；混合 Corpus：`3 PASS / 0 FAIL / 6` 个缺少可选 PDF 的明确 skip。
- 真实 532 Gold：`2/2` 已标注事件匹配，整体和关键事实 recall 均为 `1.0`，false negative 为 `0`；该真实清单是非完整 oracle，因此 precision 正确保持为不可用。
- 人工构造的完整 oracle 同时覆盖 `10 mV → 12 mV` 和纯图形变化，recall、critical recall、visual recall、precision 均为 `1.0`；故意写错 `13 mV` 时测试必须失败。
- 新版 532 报告保留 `35` 张正文审计卡、`12` 张表格卡和 `7` 项公式证据；Markdown 章节顺序为表格第 `34` 行、公式第 `151` 行、正文第 `196` 行。
- 浏览器实际打开新版报告，控制台 `0` 错误；纯 Table/Section/异常空公式号出处句搜索不到，`28 → 53.125 GHz` 真实技术变化仍可定位并高亮。
- `main.py --gui-smoke-test` 与普通系统入口 `python3 gui_app.py --smoke-test` 均通过真实 Tk 窗口构造检查。

## Delivery Policy

- 持久项目分支 `project/pdf-protocol-diff` 必须本地和远端永久保留；交付时先推该分支，再把 `main` 快进到同一最终提交并推送。
- 旧版协调门禁要求删除所有非 `main` 分支，与当前持久项目分支政策冲突；不得为通过旧门禁删除项目分支。
