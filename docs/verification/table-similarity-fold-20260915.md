# 表格相似度 1.000 折叠修复

## 用户缺陷

表格的符号或数值已经改变，但表格配对相似度仍显示为 `1.000`，随后整张表被移入默认折叠的相似度附录，主差异区无法直接看到。

## 修复口径

`pair_similarity` 只表示两侧表格是否是同一逻辑表。行身份匹配会忽略单元格值，以便把修改识别成同一行的修改；行级比较仍负责确认符号、数值、单位和条件变化。展示层现在只有在没有已确认行级变化、没有表题变化时，才允许按 `1.000` 折叠表格。

## 验证

- 定向测试：
  `.venv/bin/python -m unittest tests.test_coordinate_page_furniture tests.test_reader_focus tests.test_complete_report_followup tests.test_screenshot_first -q`
  ，93 项通过。
- 正式报告写入流程使用与用户截图一致的两行符号变更事实回放：`pair_similarity=1.0`、主表格变化 1 条、折叠表格变化 0 条、行级实质/符号变化 2 条。
- HTML：`/Users/mac/Documents/ProtocolPdfDiffReports/table_similarity_fold_fix_20260915/protocol_diff_20260915_224706/protocol_diff_report.html`
- JSON 验证摘要：`/Users/mac/Documents/ProtocolPdfDiffReports/table_similarity_fold_fix_20260915/verification.json`

该验证只证明表格展示过滤规则；未对整本协议重新执行完整 PDF 比较。
