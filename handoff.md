# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-evidence-dedup-accuracy-20260825`
- status: ready
- code commit: `a087c9fa0eae3bd69763ca7dd60c3e7f04e0f5d3`
- 目标：表格差异只在前置表格卡呈现，不再被长正文截图重复着色；纯 Figure 图注不再冒充正文变化；VMA、Module output 等受表格墙或错误父层级污染的同一章节恢复正确配对。
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 持久项目分支：`project/pdf-protocol-diff`

## 已经完成

- 长正文截图从高亮框中完整扣除 `visual_noise_bboxes` 和已识别 `TableVisual.bbox`；跨越表格上下边界的文字块也不会再给表格卡拥有的像素二次着色。
- 新增保守的 Figure 读者层过滤：移除独立 Figure 编号和纯视觉尾段中的长图注/图墙；短技术标签、公式、完整句和规范动词正文保持可见，JSON 原始审计数据不变。
- 章节匹配只在原始正文分数不足时，剔除已证明的表格/Figure 证据重试；低全文分还必须满足双侧唯一同题。错误父层级场景增加强正文唯一标题兜底，并只允许已强配父章节下的唯一同题直属子章节跟随配对。
- 候选排序与消费逻辑收敛到一个小函数，没有新增第二套章节或渲染引擎；报告继续显示原始全文相似度，不伪造高分。
- 完整回归：`.venv/bin/python -m unittest discover -s tests -v` → `1110/1110 PASS`，510.610 s。
- 新模块 Ruff、字节码编译、`git diff --check` 通过；缺失 PDF 的 CLI 故障路径以状态码 2 清楚失败。
- 功能测试在实现前分别证明以下失败：表格区域仍被染色、VMA 被拆为新增/删除、Figure 图注仍成卡、错误父层级的 Module output 未配对；当前均通过。

## 最终 OIF 实测

- 输入：`/Users/mac/Desktop/oif2021.405.14.pdf` 与 `/Users/mac/Desktop/oif2024.522.06.pdf`
- HTML：`/Users/mac/Desktop/test/pdf_protocol_diff_oif_accuracy_cleanup/protocol_diff_20260826_005059/protocol_diff_report.html`
- JSON：`/Users/mac/Desktop/test/pdf_protocol_diff_oif_accuracy_cleanup/protocol_diff_20260826_005059/protocol_diff_data.json`
- provenance build commit：`a087c9fa0eae3bd69763ca7dd60c3e7f04e0f5d3`
- 结果：60 条原始章节变化、15 个表格变化、32 组长正文原文截图。
- `29.3.1 End-to-end linear channel` → `30.3.1` 恢复为一条 modified；受保护的短技术标签仍留在审计层，但不会淹没足量实质正文的身份判断。
- `29.3.6 VMA` → `30.3.7 VMA` 为一条 modified；`29.4.1.2 Module output` → `30.4.1.2` 及其直属 test method 都为一条 modified。
- HTML 中 `Figure 29-3.`、`Figure 30-2.`、`Measurement of VMA`、`表格行:` 均为 0 次。
- 可信度保持 `degraded / 需人工复核`；仍存在源 PDF 标题层级误识别和部分未配对章节，不能把本轮改善描述成全自动准确。

## 设计边界

- 不全局降低用户配置的章节相似度阈值。
- 不按 VMA、OIF、章节号或出版方硬编码；授权来自表格坐标、正文相似度、标题唯一性和父子层级关系。
- Figure 过滤只影响读者层和匹配身份文本，不删除原始证据，也不把所有含 `Figure` 的句子当噪声。
- 重复父章节路径在任一侧出现时，直属子章节结构救援直接拒绝，不依赖字典覆盖顺序猜配。
- 源截图高亮仍是坐标区域级，不宣称逐字符 OCR 高亮。

## 继续工作建议

- 下一轮若继续降低错误章节标题，应单独修复 PDF 标题层级识别，并用真实跨文档回归证明；不要继续向章节配对层叠加宽松特例。
- 每次交付继续保留真实报告的 `degraded` 状态和人工复核边界。
