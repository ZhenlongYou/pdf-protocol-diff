# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-prose-source-visuals-20260825`
- status: ready
- recorded code commit: `c2994ab760c6f8f43c166f227e8885dd4aebfb14`
- 目标：大段正文变化不再先展示难读的整段删除/新增，而是把新旧 PDF 原文区域截图并排展示，并在原文坐标范围内标出差异；OCR 文字明细默认折叠。
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 持久项目分支：`project/pdf-protocol-diff`

## 已经完成

- 对技术正文中的大段修改、删除和新增生成原文截图证据；旧版与新版使用稳定的左右双栏，窄屏时自动纵向排列。
- 高亮精度明确为“原文坐标区域级”，不声称是逐字 OCR 高亮；差异行使用正确 alpha 合成的浅黄色底，文字保持清晰。已证明的左右页边打印行号会从标注候选中排除，即使与正文粘在同一坐标块里也会先裁掉页边范围；短变化继续使用紧凑文字卡片。
- 单侧新增或删除只展示存在的一侧，另一侧明确标注“无对应原文区域”，避免伪造配对。
- OCR 文本差异放入默认折叠的“查看文字识别明细”，仍保留可搜索、可复制的精确文本证据。
- 截图前校验源 PDF SHA-256；来源不一致时安全回退为文字报告并给出警告，不使用陈旧截图。
- 截图页数设有上限并显示省略页数；排除运行页眉与表格，避免重复展示已有的表格视觉证据。
- 视觉逻辑集中在独立模块，并复用现有 PDF 快照与页面渲染能力，没有复制第二套渲染管线。
- 新增 3 项针对性测试，覆盖双侧截图、短变化回退和源文件哈希失配。
- 全仓回归：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest` → `1099/1099 PASS`，490.850 s。
- 故障注入：禁用大段正文截图资格后，要求原文截图网格的测试按预期失败；当前实现恢复后通过。
- Ruff 新模块与新测试、格式检查、`git diff --check` 和字节码编译均通过。

## 当前状态或阻塞

- 无实现阻塞。
- 下一步只需冻结提交，在同一提交上重跑真实 OIF 对比、完成两名独立 reviewer 复核，并执行 GitHub/交付门禁。
- 最终 OIF 报告应继续保留其真实的 `degraded / 需人工复核` 结论；原文截图改善的是可读性，不应被表述成消除了核心配对或视觉漏检风险。

## 最终验收入口

```bash
PROTOCOL_PDF_DIFF_BUILD_COMMIT=<exact-commit> .venv/bin/python main.py \
  --old-pdf /Users/mac/Desktop/oif2021.405.14.pdf \
  --new-pdf /Users/mac/Desktop/oif2024.522.06.pdf \
  --output-dir /Users/mac/Desktop/test/pdf_protocol_diff_oif_prose_visual
```

## 不要再踩的坑

- 不要把区域级高亮描述成逐字高亮；它依赖 PDF 原文坐标块与文本片段重合。
- 不要对所有小变化生成截图，否则报告体积和阅读负担都会显著增加。
- 不要跳过源文件哈希绑定，也不要在哈希失配时复用旧截图。
- 不要把单侧新增/删除强行伪造成双侧对应关系。
- 不要用新截图替代底层文本与表格 oracle；它们是互补证据。

## 建议技能

- `rinysproject-delivery-orchestrator`
- `reviewer-subagents-gate`
- `delivery-acceptance-gate`
- `github-code-handoff`
- `delivery-cleanup-hygiene`
