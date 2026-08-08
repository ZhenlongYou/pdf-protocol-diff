# PDF Protocol Diff Handoff

- task_id: `pdf-diff-numbering-neutral-reader-20260809`
- goal: 表格补充证据前置；纯章节、Figure、Table、Condition 等定位编号变化在阅读层视为一致，同时保持工程数值、单位、限值和术语严格比较。
- repository: `ZhenlongYou/pdf-protocol-diff`
- canonical_path: `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- persistent_project_branch: `project/pdf-protocol-diff`
- base_main: `4a4dd5f779e27e50beb952b0c064ee632a563918`
- recorded_commit: `67f9445fb8a5ba7fe6b25e7c77f370f187c55bc8`
- status: ready
- real_entrypoint: `python3 main.py --old-pdf /Users/mac/Documents/文件对比工具/oif2024.532.04.pdf --new-pdf /Users/mac/Documents/文件对比工具/oif2024.532.05.pdf --layout-backend native --output-dir /Users/mac/Desktop/PDF对比工具_编号中性与表格前置验收_20260809/532`
- accepted_report: `/Users/mac/Desktop/PDF对比工具_编号中性与表格前置验收_20260809/532/protocol_diff_20260809_020333/protocol_diff_report.html`

## Implemented

- HTML 与 Markdown 的“表格补充证据（变化与复核）”已移动到公式索引和技术正文之前，侧栏同样以表格为第一组。
- 阅读层只在整句或整格除显式定位编号外完全一致时隐藏编号变化；JSON/CSV 继续保留原始替换事实。
- 纯标题编号、Section/Clause/See、Figure、Table、Condition、Equation 和 page 定位编号支持编号中性。
- 表格 caption-only 纯表号变化在阅读层隐藏；同一行若仍有 `0.023 → 0.025 UI` 等变化则继续显示。
- 公式编号顺延继续保留源 PDF 裁剪与上下标视觉证据，但不抬高核心技术变化数。

## Acceptance Evidence

- 全项目：`815` 项 unittest 通过。
- 受影响报告模块：`265` 项协议测试与 `152` 项公式/阅读准确性测试通过。
- `python3 main.py` 真实入口完成 532.04/532.05 全文比较；最终 HTML 中表格区位于公式索引和正文之前。
- 真实阅读版未出现纯 `Figure 31-5 → 31-6`、纯 Equation/Figure/page 联合顺延或孤立 `See 31.3.11`；`CMIT-LT → CMIS-LT`、`33.5 dB` 等技术事实仍存在。
- JSON 保留 `35` 张原始正文变化卡、`12` 张原始表格变化卡和 `7` 项公式视觉证据；MCB 表格标题误分类数为 `0`。
- Playwright 浏览器首屏、表格截图、公式放大弹窗与 `<sub>/<sup>` 渲染通过；仅 favicon 缺失产生无功能影响的 404。
- GUI 的项目环境与普通 `python3` 启动路径均通过真实窗口 smoke test。
- 故障注入把过滤器故意放宽后，UI/dB 数值保护测试均失败；恢复实现后转绿。
- 审查发现的大小写漏洞已修复：定位句、表题和章节标题均精确区分 `mV/MV`、`UI/ui` 与技术标识符大小写。
- 两个独立 reviewer 对 `67f9445` 给出 PASS 并写入 commit-bound attestation：`019fcb67-bcc9-7270-988d-113d07697892`、`019fcb67-f0d1-7fc2-acdb-c2164e8e9b59`。

## Delivery

- 已先推送持久项目分支 `project/pdf-protocol-diff`，再快进并推送 `main`；两条分支永久保留。
- canonical checkout 最终停留在 `main`，工作树保持干净。
