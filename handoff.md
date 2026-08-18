# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-accuracy-phase1-20260810`
- 目标：修复 112G `/Users/mac/Desktop/oif2021.405.14.pdf` 与 224G `/Users/mac/Desktop/oif2024.522.06.pdf` 的表格错配，尤其是 TP1a、CTLE 和中间删行后的参数行对齐。
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 持久项目分支：`project/pdf-protocol-diff`
- 实现提交：`79166bd18395981335df2c9883acdc62a5948064`

## 已经完成

- 跨 Clause 表族只有在至少 3 个全局唯一、完整单调的描述标题锚点共同证明，且不存在 split/merge 竞争时才配对；编号 offset 改变的插入区保持新增/删除，不猜配。
- TP1a 的跨列 `NOTES` 与参数行分开比较；删除 `Differential Termination Resistance Mismatch` 后，SDC22、SCC22、ERL、Transition Time 不再按位置连锁错配。
- 工程锚只用于把唯一候选送入双侧比较，完整旧/新项目名称始终可见；运算符、方向与技术标识大小写变化不会被隐藏。
- 显式严格 `Table` 表题可保留 numeric-only CTLE 网格；`Figure` 表题和无表题图轴候选仍被过滤。
- 全套：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`，`1059/1059 PASS`，耗时 `579.776s`。
- 聚焦模块：报告、页面续表与历史准确性共 `249/249 PASS`。
- 真实 corpus：`1 PASS / 0 FAIL / 0 SKIP`，状态仍为 `degraded`，技术表格变化 `15`。
- 真实结构 oracle：`/Users/mac/Desktop/test/PDF对比工具_TP1a表格修复验收_20260819/tp1a-table-oracle.jq` 已对预提交 JSON 返回 `true`，覆盖：
  - `Table 29-1` p6 ↔ `Table 30-1` p8 同卡；
  - `Table 29-12` p23 ↔ `Table 30-13` p26 同卡；
  - `Table 30-12` p24 保持单侧新增；
  - termination 旧侧非空、新侧为空；
  - SDC22、SCC22、ERL、Transition Time 各自唯一双侧。
- 浏览器渲染已检查 TP1a 卡：旧/新截图并排、行级旧/新列对齐、长 NOTES 折叠且可展开，报告明确标注“需人工复核”。
- 故障注入 `4/4` 命中：2 锚点放宽、NOTES 回退顺序配对、CTLE 重新误删、3+2 竞争表族四种错误均被对应测试拦截。
- 两名独立只读 reviewer 已复核工作区修正，无剩余 Critical/Required；最终 commit 尚需重新复核并写入 commit-bound attestation。

## 当前状态或阻塞

- 当前无技术阻塞。
- 实现已提交到持久项目分支；`handoff.md` 本次更新尚未提交。
- 尚未把最终提交快进到本地 `main`，也尚未推送 GitHub。
- 最终用户报告必须在最终 exact commit 上重新生成；预提交报告仅作验收证据，不作为最终交付物。
- 识别结果必须继续保持 `degraded / 需人工复核`，不能宣称自动判定完全可靠。

## 下一步计划

1. 提交本 handoff 更新，取得最终 exact commit。
2. 将持久项目分支快进到本地 `main`，并从 canonical `main` 运行 claim 绑定的真实入口 `python3 main.py`。
3. 用最终 commit 重新生成 112G/224G 报告，并重跑 corpus 与 `jq -e -f .../tp1a-table-oracle.jq <final-json>`。
4. 让两名独立 reviewer 复核同一最终 commit，并各自执行 `attest-review`。
5. 推送 `project/pdf-protocol-diff` 和 `main` 到同一 OID，验证远端 OID。
6. 生成交付收据并运行 `delivery_gate.py`；通过后清理预提交报告，只保留最终报告、manifest、summary 与 oracle。

## 不要再踩的坑

- 不要只放宽现有同-family 改号条件；`29-*→30-*` 必须由多个唯一单调锚点证明。
- 不要按页码、完整表号或行位置硬配；新 `Table 30-12` 会让 CTLE 的后段 offset 从 `+2` 变成 `+3`。
- 不要让跨列 `NOTES` 使整张参数表退回位置比较。
- 不要把工程锚当作名称等价证明；方向、运算符、大小写和完整旧/新名称必须可见。
- 不要取消旧 TP1a 表的 `row_alignment_reliable=false` 安全边界；行级结果应继续标“需人工复核”。
- 不要用卡片数量或单个标题存在代替强 oracle；最终 JSON 必须重跑结构化断言。
- 不要把预提交候选报告当成最终 commit 产物，也不要把本地测试文件提交进仓库。

## 建议技能

- `rinysproject-delivery-orchestrator`
- `test-effectiveness-gate`
- `reviewer-subagents-gate`
- `delivery-acceptance-gate`
- `github-code-handoff`
- `delivery-cleanup-hygiene`
