# PDF 协议差异工具测试矩阵

这份矩阵记录高频协议对比场景，以及当前测试套件覆盖的风险点。工具的目标是帮助人工审阅更快定位章节级变化，而不是替代对源 PDF 的最终确认。

## 已覆盖的核心场景

| 场景 | 覆盖重点 | 代表测试 |
|---|---|---|
| 页码漂移 | 新文档插入内容后，不按相同页码硬配对 | `test_multipage_pdfs_match_sections_not_page_numbers` |
| 独立页范围 | 旧/新 PDF 可以分别指定不同起止页，报告保留真实页码 | `test_run_diff_uses_independent_old_and_new_page_ranges` |
| 无稳定章节 | 退回按页块和正文相似度匹配，而不是强行同页比较 | `test_page_fallback_matches_content_when_headings_are_missing` |
| 页头页尾 | 重复页眉、动态页码、坐标在页边但文本流插入正文的运行页脚不应制造差异；正文重复要求仍须保留 | `test_repeated_middle_body_lines_are_not_removed_as_page_furniture`、`test_dynamic_footer_is_removed_even_when_not_last_extracted_line`、`test_repeated_bottom_footer_is_removed_even_when_text_order_interleaves_it`、`test_repeated_body_requirement_is_not_removed_as_page_furniture` |
| 深层步骤 | PCIe 风格 `2.13.2` 下的 `39.` 等步骤不误判为新章节 | `test_pcie_style_numbered_steps_stay_inside_deep_section` |
| 范围从页面中间开始 | 选中的第一页如果在步骤中间，应标成范围前序内容 | `test_opening_selected_range_keeps_procedure_steps_as_body` |
| 标题变化 | 正文不变但章节编号或标题改变，仍报告为修改 | `test_heading_renumbering_is_reported_when_body_is_same` |
| 整节新增/删除 | 新增节和删除节用新/旧位置明确展示 | `test_demo_pdfs_produce_modified_and_added_sections`、`test_deleted_section_is_reported_with_old_location` |
| 表格行数值 | 表格样式文本中的限值变化不能被当成页眉或标题丢掉 | `test_table_row_value_changes_are_reported` |
| 同页插表/换序 | 前插一个唯一编号表时，共享唯一表题按 bbox 物理顺序和 LCS 单调锚点平移；局部换序不污染稳定前缀，真实换序仍须报告，单个无效 bbox 不撤销其他表的坐标证据 | `test_same_page_inserted_table_does_not_offset_existing_unique_tables`、`test_same_page_local_reorder_does_not_corrupt_stable_prefix_matches`、`test_same_page_numbered_tables_preserve_physical_order`、`test_invalid_table_bbox_does_not_hide_other_tables_physical_reorder` |
| 跨版本表格身份 | 冗余父章节编号不能拆散同一跨页表；相邻同名表仍需深层章节连续性；表号重排需至少两张唯一描述表共同证明一致偏移 | `test_same_caption_table_pairs_across_redundant_parent_section_prefix`、`test_adjacent_same_caption_pages_stay_one_table_across_section_boundary`、`test_adjacent_same_caption_tables_in_unrelated_sections_stay_separate`、`test_one_descriptive_caption_does_not_prove_cross_context_identity`、`test_renumbered_table_with_same_descriptive_caption_pairs_across_ambiguous_context` |
| 表头版式漂移 | 中性 `Column N` 与明确 Parameter/Symbol/Value/Unit 表头只在逐格可证明等价时抑制；`R / d` 不猜成 `Rd`，真实值变化仍保留 | `test_neutral_to_explicit_header_only_suppresses_proven_equal_cells`、`test_numeric_multiplication_x_and_times_glyph_are_equal` |
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
| 扫描页 OCR | 大面积栅格页在 Tesseract 可用时恢复可审阅文字，但永不自动判等 | `test_generated_raster_pdf_reaches_full_page_ocr_path`、`test_ocr_page_is_an_explicit_quality_metric_and_never_auto_equal` |
| OCR 依赖缺失 | 未安装 Tesseract 时保持空文字/无法判断并给出可操作警告 | `test_scan_without_tesseract_stays_empty_and_actionable` |
| 可选文字优先 | 原生文字占据至少一半高度分段时不走重复 OCR；稀疏页眉/页脚或缺少字符坐标不能阻断整页 OCR，所有图像主导页仍保留风险并禁止自动判等 | `test_selectable_text_layer_skips_ocr_even_over_a_full_page_image`、`test_sparse_header_and_footer_text_layer_does_not_suppress_full_page_ocr`、`test_image_dominant_page_without_native_coordinates_uses_ocr` |
| OCR 质量控制 | 扫描页按 300 DPI 渲染、每页限时 60 秒，并把中英语言配置对称传给新旧文档 | `test_requested_ocr_language_uses_document_quality_rendering_and_timeout`、`test_comparison_options_forward_ocr_language_to_both_documents` |
| OCR 配置安全 | 拒绝把任意命令式参数伪装成 Tesseract 语言代码 | `test_invalid_ocr_language_expression_is_rejected_before_opening_pdf` |
| OCR 资源边界 | 重叠 XObject 只计算一次覆盖面积，异常大页面在位图分配前停止 | `test_overlapping_images_contribute_only_their_union_coverage`、`test_oversized_page_is_not_rendered_at_unbounded_ocr_resolution` |
| 解析 benchmark | 五类临时受控 PDF 分别守住原生文字、真正重叠双栏的 column-major 顺序、公式、无框表格的关键行/值/单位、真实英文 OCR 的 `3.3 V`/`25 PS` 顺序与隐私安全 ordinal summary | `test_controlled_benchmark_exercises_layout_formula_table_and_real_ocr` |
| Benchmark manifest/隐私 | 拒绝不安全路径与未知字段；缺失 optional/required、OCR 期望和脱敏错误都有明确结果 | `test_validator_returns_all_strict_schema_errors`、`test_missing_documents_and_ocr_expectation_have_explicit_statuses`、`test_corrupt_pdf_error_is_bounded_and_redacts_corpus_root` |
| 坐标证据块 | 原生文字、表格和 OCR 分别产生不可变、连续排序的 `DocumentBlock`，不改正文比较文本 | `test_public_extraction_returns_contiguous_native_text_blocks`、`test_successful_public_ocr_adds_tesseract_page_bounds_block`、`test_public_table_visual_becomes_pdfplumber_table_block` |
| 页面解析路由 | 每页只归入一种 native/OCR/image 路由，且 OCR、图像、版面风险的优先级固定 | `test_pure_classifier_has_documented_precedence_for_all_five_routes`、`test_quality_metrics_group_each_selected_page_by_one_parser_route` |
| 解析审计 JSON 隐私 | 报告只序列化每页路由、风险标志和 block 数，不复制 page/block 原文 | `test_json_report_serializes_route_audit_without_page_or_block_text` |
| 文档元信息读者降噪 | HTML/Markdown/TXT 不显示作者、邮箱、版权和修订历史；JSON/CSV 继续无损保留 | `test_reader_reports_hide_metadata_but_audit_outputs_retain_it`、`test_generic_caption_revision_table_is_document_metadata` |
| 截图覆盖表体去重 | 同章节、同表题、同页且完整坐标覆盖的表格截图可替代重复线性化片段；按物理表去重后再按 occurrence 消费证据，并跨差异类型补回读者容量。正常技术句、规范尾句、独立公式、远处同文、不完整截图和单侧章节不得误删 | `test_mixed_section_hides_only_snippets_covered_by_visible_table_evidence`、`test_table_wall_filter_preserves_readable_normative_suffixes`、`test_table_fragment_filter_consumes_duplicate_text_occurrences`、`test_public_reader_evidence_deduplicates_change_and_visual_group`、`test_tiny_formula_between_table_rows_is_not_treated_as_a_bridge`、`test_remote_table_page_cannot_hide_same_text_in_long_section`、`test_table_filter_refills_reader_capacity_across_delta_kinds`、`test_incomplete_table_visual_cannot_hide_one_mixed_section_fragment`、`test_single_side_section_skips_paired_table_fragment_proof` |

## 当前已知边界

- 扫描件只有在页面具备大面积栅格证据且系统安装 Tesseract 时才自动 OCR；已有搜索文字层也仍按图片主导页面降级。OCR 文字只用于差异定位，不能支撑自动判等。
- 图片、印章、波形图、矢量图本身不会比较。
- 表格会按 PDF 抽取出的文本行比较，不会理解单元格合并、列对齐或图片表格。
- 当前的 `DocumentBlock` 与页面路由是审计证据层，不是 Docling/PaddleOCR 的准确率背书；重型版面后端仍需按各类 corpus 单独 A/B 验收。
- 图像主导页即使有搜索文字层也不会升为 `reliable`；`image_text_layer` 和 `unreadable_image` 会保留可审计路线，扫描 OCR 成功则为 `ocr_fallback`。
- 单页 OCR 有 60 秒超时，但整份长扫描 PDF 的时间/页数预算、进度和取消（P0b）尚未实现，应主动用页窗控制运行范围。
- 中文数字归一化只在明确计数/单位/章节上下文生效，例如 `七个波形`、`第七章`；普通词里的汉字数字不会被随意改写。
- 报告仍是审阅辅助工具。对于协议签署、合规结论、法律解释，应回到源 PDF 对应页复核。

## 高频回归命令

```bash
python3 -m unittest discover -s tests
python3 -m coverage run --branch --source=protocol_pdf_diff -m unittest discover -s tests
python3 -m coverage report -m
python3 tools/benchmark_pdf_parsing.py --controlled \
  --output-json /tmp/pdf_parsing_benchmark.json
python3 tools/benchmark_pdf_parsing.py corpus/parsing_benchmark.example.json \
  --corpus-root /path/to/private/pdf-corpus \
  --output-json /tmp/pdf_parsing_real_benchmark.json
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
