# PDF Protocol Diff Handoff

- task_id: `pdf-diff-citation-list-neutral-reader-20260809`
- goal: 表格补充证据前置；纯 Section、Figure、Table、Condition 等引用编号、列表及范围变化在阅读层视为一致，原始 JSON/CSV 无损保留，工程数值、限值、单位和术语严格比较。
- repository: `ZhenlongYou/pdf-protocol-diff`
- canonical_path: `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- persistent_project_branch: `project/pdf-protocol-diff`
- base_main: `3374b0f8f0ae2ff49818a40e8f850785eaf08ca0`
- recorded_commit: `fbc2b5128fa7fd45f72d10569863125be3bfb12b`
- status: ready
- real_entrypoint: `.venv/bin/python main.py --old-pdf /Users/mac/Documents/文件对比工具/oif2024.532.04.pdf --new-pdf /Users/mac/Documents/文件对比工具/oif2024.532.05.pdf --layout-backend native --output-dir /Users/mac/Desktop/PDF对比工具_引用出处中性验收_20260809/532`
- accepted_report: `/Users/mac/Desktop/PDF对比工具_引用出处中性验收_20260809/532/protocol_diff_20260809_145011/protocol_diff_report.html`

## Implemented

- HTML、Markdown 与侧栏的“表格补充证据（变化与复核）”位于公式视觉核对和技术正文之前。
- 阅读层中和纯标题、caption、显式引用和可证明完整的编号列表/范围；底层 `DiffResult` 及 JSON/CSV 不改写。
- 长引用列表被底层 diff 拆成 added/removed 卡时，仅在同角色、同位置、唯一一对一且整句中和后精确相等时从阅读副本中消除。
- 裸整数列表只在真正句末才可忽略；点分/短横线编号必须同形且共享父编号。右括号/方括号后仍有技术内容时一律 fail-visible。
- 编号中和后保持大小写精确比较；`mV/MV`、`UI/ui`、`CMIT-LT/cmit-lt` 等变化不会被隐藏。
- 公式视觉证据与 MCB 伪表格防护保持原有逻辑，本轮未放宽抽取层边界。

## Acceptance Evidence

- 全项目 `819` 项 unittest 通过；`compileall` 和 `git diff --check` 通过。
- 用户截图的 Table 列表扩展和 `Sections 31.3.4 to 31.3.18 → 31.3.19` 在最终 HTML/Markdown/TXT 中不可见，在 JSON/changes.csv 中仍有完整旧/新原文。
- 独立反例覆盖 dB、mV、UI、GBd、mVrms、mVpp、ratio、BER、lanes、taps、千位数、范围、分数、`in/by/at/with/are`、大小写以及 integer/dotted/dashed 括号语境，旧新技术事实在 HTML/MD/TXT 中全部保留。
- 真实 532.04/532.05 报告 Markdown 顺序为表格（第 33 行）、公式（第 150 行）、技术正文（第 195 行）。
- 最终 JSON 保留 `35` 张原始正文变化卡、`12` 张表格变化卡和 `7` 项公式视觉证据；MCB 表题误分类数为 `0`。
- 阅读报告仍保留 `33.5 dB`、`CMIT-LT → CMIS-LT`、`53.125 GHz` 等技术事实。HTML SHA-256 为 `fb41fc0adb241e7f33b373844cc9c91c677003cf0ed2b1890b7f90d063d1e2b0`，JSON SHA-256 为 `889b3f815758ad72f9586b1b777364d1d51ff263c0938c79258ad493314507f4`。
- 最终 `file://` 自动导航被应用内浏览器安全策略拒绝，未绕过限制；同模板早先报告已直开通过，最终文件已做可见文本、结构顺序和完整性核对。
- 两个独立 reviewer 对 `fbc2b5128fa7fd45f72d10569863125be3bfb12b` 给出 commit-bound PASS，无 P1/P2：`019fcb67-bcc9-7270-988d-113d07697892`、`019fcb67-f0d1-7fc2-acdb-c2164e8e9b59`。协调层的旧 task 丢失 GitHub identity write lane，因此 attestation 未落盘；两份独立审查结论均已精确绑定该提交。

## Delivery

- 已先推送持久项目分支 `project/pdf-protocol-diff`，再将 `main` 快进到同一提交并推送；两条分支均永久保留。
- canonical checkout 最终停留在 `main`，工作树干净。
- 本轮只保留最终验收报告 `protocol_diff_20260809_145011`；六个中间报告已移入 macOS 废纸篓，可恢复。
