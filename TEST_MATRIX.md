# PDF 协议差异工具测试矩阵

这份矩阵记录高频协议对比场景，以及当前测试套件覆盖的风险点。工具的目标是帮助人工审阅更快定位章节级变化，而不是替代对源 PDF 的最终确认。

## 已覆盖的核心场景

| 场景 | 覆盖重点 | 代表测试 |
|---|---|---|
| 页码漂移 | 新文档插入内容后，不按相同页码硬配对 | `test_multipage_pdfs_match_sections_not_page_numbers` |
| 独立页范围 | 旧/新 PDF 可以分别指定不同起止页，报告保留真实页码 | `test_run_diff_uses_independent_old_and_new_page_ranges` |
| 无稳定章节 | 退回按页块和正文相似度匹配，而不是强行同页比较 | `test_page_fallback_matches_content_when_headings_are_missing` |
| 页头页尾 | 重复页眉、动态页码、单页明显页脚不应制造差异 | `test_repeated_middle_body_lines_are_not_removed_as_page_furniture`、`test_dynamic_footer_is_removed_even_when_not_last_extracted_line` |
| 深层步骤 | PCIe 风格 `2.13.2` 下的 `39.` 等步骤不误判为新章节 | `test_pcie_style_numbered_steps_stay_inside_deep_section` |
| 范围从页面中间开始 | 选中的第一页如果在步骤中间，应标成范围前序内容 | `test_opening_selected_range_keeps_procedure_steps_as_body` |
| 标题变化 | 正文不变但章节编号或标题改变，仍报告为修改 | `test_heading_renumbering_is_reported_when_body_is_same` |
| 整节新增/删除 | 新增节和删除节用新/旧位置明确展示 | `test_demo_pdfs_produce_modified_and_added_sections`、`test_deleted_section_is_reported_with_old_location` |
| 表格行数值 | 表格样式文本中的限值变化不能被当成页眉或标题丢掉 | `test_table_row_value_changes_are_reported` |
| 完整句子片段 | 报告展示句子级片段，避免只露出零散词 | `test_wrapped_sentence_snippets_are_reported_as_complete_units` |
| 片段上限 | 超过上限时仍扫描全部差异，并明确提示省略数量 | `test_snippet_limit_scans_all_differences_and_reports_omissions` |
| 大小写/空格/标点噪声 | 普通大小写、句尾标点、 harmless spacing 不应制造差异 | `test_cosmetic_case_spacing_and_punctuation_diffs_are_suppressed` |
| 数值标点 | `1.0` 与 `10`、`<=` 与 `>=` 等真实数值变化必须保留 | `test_numeric_punctuation_changes_are_not_suppressed`、`test_comparison_operator_changes_are_not_suppressed` |
| 英文语义数字 | `seven`/`7`、`twenty-one`/`21` 视为普通计数等价 | `test_cardinal_number_words_and_digits_are_semantically_equal` |
| 英文标识符保护 | `Gen seven`/`Gen 7`、`report seven.pdf` 仍视为标识符变化 | `test_identifier_like_number_words_and_digits_are_not_collapsed` |
| 中文语义数字 | `七个`/`7 个`、`一百零五个`/`105 个` 这类明确计数等价 | `test_chinese_count_words_and_digits_are_semantically_equal` |
| 中文数字上下文保护 | 裸中文数字或标识符前缀不应被静默折叠 | `test_bare_chinese_number_identifier_change_is_highlighted`、`test_chinese_number_inside_identifier_is_not_collapsed` |
| 英文单位边界 | `七 samples` 可视作计数，`七sampleRate` 保留为标识符差异 | `test_chinese_count_words_before_ascii_units_are_semantically_equal`、`test_chinese_number_inside_identifier_is_not_collapsed` |
| 千分位逗号 | `1,000` 与 `1000` 等价，但 `1,000` 与 `1001` 仍报差异 | `test_numeric_thousands_separator_noise_is_suppressed`、`test_numeric_thousands_separator_does_not_hide_value_changes` |
| 语义数字加真实措辞变化 | 数字噪声不高亮，但真实新增词仍保留 | `test_pcie_capture_real_wording_change_survives_number_word_noise`、`test_chinese_count_noise_does_not_hide_real_wording_change` |
| PDF 抽取断行 | `trans-\nmitter`、`125.\n0 μs` 等抽取断行不应制造差异 | `test_hyphenated_pdf_line_wrap_is_suppressed`、`test_decimal_value_split_across_pdf_lines_stays_one_unit` |
| 视觉-only 变化 | 图片、印章、装饰图形不参与文本差异 | `test_visual_only_pdf_changes_do_not_create_text_diffs` |

## 当前已知边界

- 只比较 PDF 中可抽取的文本；扫描件或图片型 PDF 需要先做 OCR。
- 图片、印章、波形图、矢量图本身不会比较。
- 表格会按 PDF 抽取出的文本行比较，不会理解单元格合并、列对齐或图片表格。
- 中文数字归一化只在明确计数/单位/章节上下文生效，例如 `七个波形`、`第七章`；普通词里的汉字数字不会被随意改写。
- 报告仍是审阅辅助工具。对于协议签署、合规结论、法律解释，应回到源 PDF 对应页复核。

## 高频回归命令

```bash
python3 -m unittest discover -s tests
python3 main.py
python3 main.py \
  --old-pdf work/pcie/PHY_Test_Spec_Rev4.0_Ver1.2_08182021_副本.pdf \
  --new-pdf work/pcie/PCIe_6_0_PHY_Test_Spec_Rev1.0_RC2.pdf \
  --old-start-page 36 --old-end-page 40 \
  --new-start-page 78 --new-end-page 83 \
  --output-dir work/pcie_stress_20260705_more_cases
```

## 真实 PCIe Smoke 记录

最近一次手动 smoke 使用 Rev4.0 第 36-40 页对 Rev6.0 第 78-83 页：

- 输出目录: `work/pcie_stress_20260705_more_cases/protocol_diff_20260705_025136`
- 结果摘要: 2 条差异，其中 1 个修改章节、1 个新增范围前序内容。
- 页窗记录: 旧选择页 `36-40`，新选择页 `78-83`。
- 关键检查: HTML 中 `seven`/`7` 没有作为普通计数差异高亮，真实新增的 `them` 被高亮。

这条 smoke 依赖本地 PCIe PDF 文件，不作为默认单元测试的一部分；日常高频验证仍以 `python3 -m unittest discover -s tests` 为主。
