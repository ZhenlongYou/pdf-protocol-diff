# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-residual-matching-fixes-20260820`
- status: ready
- 目标：全面修复并验收 112G `/Users/mac/Desktop/oif2021.405.14.pdf` 与 224G `/Users/mac/Desktop/oif2024.522.06.pdf` 的表级配对、行级对齐、Figure 假表、表题与读者输出问题。
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 持久项目分支：`project/pdf-protocol-diff`
- 实现提交：`7a074a34629cc1680fc43ce462640c0fb3b8c70e`

## 已经完成

- 跨 Clause 表族只有在至少 3 个全局唯一、完整单调的描述标题锚点共同证明，且不存在 split/merge 竞争时才配对；编号 offset 改变区保持新增/删除，不猜配。
- 13 个旧编号表全部映射到正确新表；`Table 30-12` p24 是唯一技术表新增，未被 offset 规则吞掉。
- TP1a 中删除 `Differential Termination Resistance Mismatch` 后，SDC22、SCC22、ERL、Transition Time 与 NOTES 不再连锁错位。
- `Table 29-6` p9 ↔ `Table 30-6` p11 恰有 3 条双边变化：525→600 mV、±160 mV/15 ps→±180 mV/10 ps、42→60 GHz；无 `Parameter 1` 表头串配或伪增删。
- `Table 29-12` p23 ↔ `Table 30-13` p26 的 CTLE 行作为同一 `Min/Max 参数范围` 报告 max[2] 6→10，并独立报告新增 Note。
- Figure 30-14 内 TP0 小框不再生成 p28 假表；Figure 删除权增加同栏、标签连通、显式技术 schema、有限正框、方向、字体/字号/基线和真实空格字形证据。
- 表题恢复了 29/30-{3,6,8} 的换行续题、29-8 的 gDC/gDC2 下标，以及 058 Table 32-4 的混合字号同基线标题。
- 数据格完整性新增 `data_rows_fully_represented`，缺格、重叠/重复 bbox、跨行争抢、乱序字符和非有限坐标均 fail closed；合法 colspan Note 仍可通过。
- 读者版将原始 PUA 阻抗符号显示为 Ω；JSON/CSV 继续保留原始审计字符。
- Canonical 全仓门禁：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`，`1083/1083 PASS`，耗时 `570.501s`。
- 变更相关五模块：`738/738 PASS`（`test_protocol_diff` 480 + 其余四模块 258）。
- 三名独立只读 reviewer 在最终 WIP 哈希 `684c5a6dc8fe7f77f30a12591882663fbce4302590acdd037225fa243e69752c` 上均报告无 Critical/Required；测试审计确认 style、baseline、rotated、subpoint、重复层近邻去重、unique-cap、NaN/Inf/反向框与 size mutants 均被回归杀死。

## 真实 112G / 224G 验收事实

- 页数：30 / 30；table visuals：13 / 15；table changes：15（14 technical + 1 document metadata）。
- 13 个技术表双边映射：29-1→30-1、29-2→30-2、29-3→30-3、29-4→30-4、29-5→30-5、29-6→30-6、29-7→30-7、29-8→30-8、29-9→30-9、29-10→30-10、29-11→30-11、29-12→30-13、29-13→30-14。
- 唯一 technical added：`Table 30-12. Electrical Stressor Parameters Measured at TP1a`，p24，5 行。
- 新版 p28 table visual 数为 0。
- 识别结论必须继续保持 `degraded / 需人工复核`；通过表格强 oracle 不等于自动证明整份 PDF 没有任何漏检。

## 当前状态或阻塞

- 实现与全仓测试已完成，工作树在实现提交后只待本 handoff 提交。
- 需从本 handoff 提交取得最终 exact commit，并在该提交上重新生成 HTML/MD/TXT/CSV/JSON。
- 最终产物必须运行 `/Users/mac/Desktop/test/PDF对比工具_全面修复验收_20260820/final-table-oracle.jq`；旧 precommit 产物缺 `data_rows_fully_represented`，明确不可交付。
- 两名 reviewer 还需对最终 exact commit 自行登记 commit-bound attestation。
- GitHub 尚未推送；最终需让 `project/pdf-protocol-diff` 与 `main` 指向同一远端 OID。
- 协调 heartbeat 曾因 orchestrator 的 `repository_authority` schema 漂移拒绝更新；仓库 identity/origin 本身未变化。不得手改协调文件绕过，若最终 release/delivery gate 仍失败要如实报告。

## 最终交付步骤

1. 提交本 handoff，冻结最终 exact commit。
2. 用 exact commit 运行真实入口，生成最终报告并执行强 oracle。
3. 让两名独立 reviewer 复核同一 exact commit 并登记 attestation。
4. 快进本地 `main`，推送 `project/pdf-protocol-diff` 和 `main`，核对远端 OID。
5. 运行交付收据与 delivery gate；若协调 schema 漂移仍阻塞，只报告该外部阻塞，不修改协调状态文件。

## 不要再踩的坑

- 不要只放宽同-family 改号或用页码/完整表号硬配；`29-*→30-*` 必须由多锚点、单调性与无竞争共同证明。
- 不要让跨列 NOTES、短 NOTE 或 identityless Min/Max 表回退到位置比较。
- 不要把工程锚当作名称等价证明；方向、运算符、大小写和完整旧/新名称必须可见。
- 不要让 Figure 题名、标签或空格跨栏借证；删除权必须比保留权更严格。
- 不要用卡片数量或单个标题存在代替强 oracle；最终 JSON 必须绑定源 hash、页窗和 exact build commit。
- 不要把表格 oracle PASS 写成“整份文档完全无漏检”；watchdog/版面歧义仍须保留 degraded 边界。

## 建议技能

- `rinysproject-delivery-orchestrator`
- `test-effectiveness-gate`
- `reviewer-subagents-gate`
- `delivery-acceptance-gate`
- `github-code-handoff`
- `delivery-cleanup-hygiene`
