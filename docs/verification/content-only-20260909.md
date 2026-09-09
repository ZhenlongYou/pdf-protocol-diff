# 实质内容比较验收

需求来源：2026-09-09 任务 `01a086af-c710-7b50-99f2-8fe0f3110b34`。用户要求仅报告实际内容变化，排除作者、目录、排版、同内容章节改号和移动；随后明确邮箱在全文均不关注。

- HTML/Markdown/TXT/CSV 不显示作者与目录差异，也不为无具体内容差异的表格结构提示创建差异卡。
- 空白宽度、同一已配对单元格内的普通单词折行、普通文字大小写变化不产生内容变化；不把数字序列、技术单位、标识符或条件归属一并中和。
- 邮箱地址变更在正文中同样忽略；邮箱与数值/条件同时改变时，仅中和地址，其他变化仍显示。
- 内容相同的完整章节改号或同域换序不报差异；数值、单位、否定词、实际增删与适用范围变化必须保留。
- 原始 JSON 取证字段保持兼容；新增 `content_changes` 和 `content_table_changes` 与读者清单一致。识别不足仍由质量结论表达，不能把清单为空当成任意 PDF 一致证明。

实现与验证入口：[报告过滤](../../src/protocol_pdf_diff/reporting.py)、[内容等价规则](../../src/protocol_pdf_diff/content_equivalence.py)、[章节识别](../../src/protocol_pdf_diff/sectioning.py)、[用户行为回归](../../tests/test_content_only.py)。真实入口为 `.venv/bin/python gui_app.py`，CLI 为 `.venv/bin/python main.py --old-pdf ... --new-pdf ...`。

证据目录：`/Users/mac/Documents/ProtocolPdfDiffReports/content_only_20260909`。初始四个目标失败保存在 `baseline.log`；日志与最终源码绑定情况、真实 PDF/GUI 结果及交付 OID 以最终交付回执为准。当前验收仍在执行，不能将中间绿测视为完成。

旧候选 `31040db56694d053c90b58153086964861417e39` 未合入本任务。原 owner 正式取消 claim 后，经独立 legacy 保存审计，全部已提交历史保存在本地 `archive/precise-evidence-20260909-31040db` 标签及已核验的完整 bundle；目录 `preserved/preservation.json` 记录源 OID 和 SHA256。仅复用了目录标题/条目边界识别的窄实现，并重新验证；没有恢复旧界面改造或未知未提交变更。
