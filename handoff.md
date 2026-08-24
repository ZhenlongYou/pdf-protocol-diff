# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-explicit-page-window-anchor-20260824`
- status: ready
- 目标：当用户同时给定新旧 PDF 起止页时，把双侧页窗视为强关联声明，让最相关的正文进入差异比较，而不是整节新增/删除。
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 持久项目分支：`project/pdf-protocol-diff`
- 代码提交：`03391a75b2794e1c0c93ab4b303a140b3c8a1f61`

## 已经完成

- 双侧起止页都明确时，常规/结构配对优先；若页窗内没有技术正文配对，则仅锚定步骤/段落重合最强的一对。
- 锚定只授权配对，报告仍显示实际全文相似度；其余无对应章节保留为新增/删除。
- 单侧页码、空正文和已有技术配对不会触发额外强配。
- 页边出版元数据清理只在坐标证据和重复页边证据成立时启用，不会靠纯文本规则删除正文版本号或日期。
- 已处理首轮两名独立 reviewer 的全部发现：页窗内的普通小节配对不再阻断剩余核心正文；但已有技术关系后，剩余章节必须有骨架重合才可锚定；文档元数据排除。
- 页眉大小写中和已限缩为无标识符的通用出版标题形状；`MODE_FAST/RX_CAL/ID ALPHA/GT/S` 等技术大小写变化仍可见。带 `shall/must/should/required/prohibited` 的底边正文不得被页脚规则删除。
- 全仓回归：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest` → `1093/1093 PASS`，475.702 s。
- 故障注入：隔离副本中禁用用户页窗锚定后，2 项关键回归按预期失败；恢复后 3/3 PASS。
- 真实 PHY 3.0 p16–18 ↔ PHY 4.0 p33–35 已运行：`2.8.2` ↔ `2.11.1` 是同一个 `modified` 项，`match_basis=user_page_window_anchor`，实际相似度 `0.496694`；旧版 `2.9/2.9.1` 仍保留删除。

## 当前状态或阻塞

- 无实现阻塞。
- 最终 exact commit 尚需提交本 handoff，然后在该提交上重新生成真实报告、完成两名独立 reviewer 的第二轮复核、合入/push `main` 并运行交付门禁。
- 真实报告必须保持 `degraded / 需人工复核`，因为视觉漏检哨兵存在 1 个未安全配对页；这不影响本次正文配对结论，但不得写成“全部可靠”。

## 下一步计划

1. 提交本 handoff，冻结最终 exact commit。
2. 用 exact commit 重跑真实 `main.py` 和独立 JSON oracle，只保留最终交付报告。
3. 让两名独立只读 reviewer 检查同一 exact commit 并登记 attestation。
4. 使 `project/pdf-protocol-diff`、local `main` 与 GitHub 两引用指向同一 OID，运行 delivery gate。

## 不要再踩的坑

- 不要全局降低章节相似度门槛；这会让普通文档产生错配。
- 不要把页码作为逐页硬对齐；页窗是关系授权，正文证据决定具体配对。
- 不要伪造高相似度；授权依据与实际分数必须分开展示。
- 不要把页边元数据规则写死为某个标准或厂商名称；核心规则必须保持通用。
- 不要把 JSON oracle PASS 外推成整份 PDF 无视觉漏检。

## 建议技能

- `rinysproject-delivery-orchestrator`
- `test-effectiveness-gate`
- `reviewer-subagents-gate`
- `delivery-acceptance-gate`
- `github-code-handoff`
- `delivery-cleanup-hygiene`
