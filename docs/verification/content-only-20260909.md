# 实质内容比较验收

需求来源：2026-09-09 任务 `01a086af-c710-7b50-99f2-8fe0f3110b34`。用户要求仅报告实际内容变化，排除作者、目录、排版、同内容章节改号和移动；随后明确邮箱在全文均不关注。

- HTML/Markdown/TXT/CSV 不显示作者与目录差异，也不为无具体内容差异的表格结构提示创建差异卡。
- 空白宽度、同一已配对单元格内的普通单词折行、普通文字大小写变化不产生内容变化；不把数字序列、技术单位、标识符或条件归属一并中和。
- 邮箱地址变更在正文中同样忽略；邮箱与数值/条件同时改变时，仅中和地址，其他变化仍显示。
- 内容相同的完整章节改号或同域换序不报差异；数值、单位、否定词、实际增删与适用范围变化必须保留。
- 原始 JSON 取证字段保持兼容；新增 `content_changes` 和 `content_table_changes` 与读者清单一致。识别不足仍由质量结论表达，不能把清单为空当成任意 PDF 一致证明。

实现与验证入口：[报告过滤](../../src/protocol_pdf_diff/reporting.py)、[内容等价规则](../../src/protocol_pdf_diff/content_equivalence.py)、[章节识别](../../src/protocol_pdf_diff/sectioning.py)、[用户行为回归](../../tests/test_content_only.py)。真实入口为 `.venv/bin/python gui_app.py`，CLI 为 `.venv/bin/python main.py --old-pdf ... --new-pdf ...`。

证据目录：`/Users/mac/Documents/ProtocolPdfDiffReports/content_only_20260909`。初始四个目标失败保存在 `baseline.log`；日志与最终源码绑定情况、真实 PDF/GUI 结果及交付 OID 以最终交付回执为准。最终行为源码为 `fa2eba88011dda57d27d430f8681a16edb11ce97`；之后仅更新验证路径与交接文档。

旧候选 `31040db56694d053c90b58153086964861417e39` 未合入本任务。原 owner 正式取消 claim 后，经独立 legacy 保存审计，全部已提交历史保存在本地 `archive/precise-evidence-20260909-31040db` 标签及已核验的完整 bundle；目录 `preserved/preservation.json` 记录源 OID 和 SHA256。仅复用了目录标题/条目边界识别的窄实现，并重新验证；没有恢复旧界面改造或未知未提交变更。


## 范围验证结果

- 最后行为变更后完整回归：1317 项，175.685 秒，1 项条件跳过，0 失败；`full-suite-reviewed.log`。
- 27 个独立手写场景覆盖普通大小写/折行、全局邮箱排除、目录续页和跨页、Source 版本名单、独立 Abstract、should、单元格与表题的 State 上下文，以及数值/单位/否定保留。STRICT v2 `strict-executed-final.json` 实际执行三对 RED/GREEN、独立 oracle、全场景和真实 PDF 报告入口，结果 `EXECUTED_EVIDENCE_PASS / verified_scope_only`，SHA-256 `8355819ae3667f307b8c56cf0063907b9d7a7bba541a211d28369b533cbcb025`。
- 原生 GUI：`native-reviewed/native-result.json` 记录 WKWebView 的真实 JavaScript 桥接比较、界面最终状态及生成报告。独立绘制的 PDF 保留 10→12 mV，作者/邮箱/目录/大小写无卡；正式 `gui_app.py --smoke-test` 也通过。
- 实际 OIF：输入为本机 `OIF-CEI-5.1.pdf`（636 页）与 `OIF-CEI-05.3.pdf`（685 页）。最终源码比较旧 1–15 / 新 1–18 页，作者、公司名单、邮箱、版权和目录均无内容卡；证据 `oif-front-reviewed/protocol_diff_20260910_000054`，JSON SHA-256 `3c5653819759b2b7aeb151922576ef13293577f0f236bfd6085cfb44a7fb612b`。
- 实际表格：旧 70 / 新 75 页 Table 1-9 的 `outputs ↵ of` 与 `the ↵ outputs` 仅换行，最终 `oif-table-reviewed/protocol_diff_20260910_000221` 没有表格差异卡。相邻页窗 `oif-table-context/protocol_diff_20260910_000317` 同样无表格卡。窄页窗因行号/来源覆盖不足仍有正文复核条目，不能将这两份局部报告说成整页相同证明。
- 浏览器直接打开本地 HTML 被本机浏览器 URL 策略拒绝；未绕过策略。本轮检查原生 GUI 完整工作流和生成的 HTML/Markdown/CSV/JSON 内容，不声称重新完成报告浏览器视觉审计；报告版式模板未改。
- 两位独立只读审查者已复测并关闭目录、Contacts、作者名单边界和表题上下文发现。最终 exact-OID attestation 与 main/持久分支交付由证据根 `delivery-receipt.json` 及官方协调门禁记录。

原始失败日志、中间报告和取消分支 bundle 用于复现边界缺陷及历史保存，不能作为最终源码证据。原始 JSON 继续保留被排除的出版/排版事实；使用 `content_changes` / `content_table_changes` 获取与读者报告相同的范围。本轮不认证任意语义改写、扫描件、图形/公式相同，也不包含 907 页原文件或整本 OIF 的最终源码验收。旧报告不会自动改写，重启源码工具并重新比较后生效。
