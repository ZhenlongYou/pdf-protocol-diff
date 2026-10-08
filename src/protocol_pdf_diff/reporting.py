"""Report writers for protocol PDF comparison results."""

from __future__ import annotations

# 兼容既有调用方的私有辅助函数；所有实际判定实现仅保存在 comparison_content。
from .comparison_content import (
    _ABSOLUTE_PATH_RE,
    _BACKSLASH_PATH_RE,
    _CHINESE_INLINE_NUMBER_RE,
    _CLI_OPTION_RE,
    _DIRECTIONAL_SYMBOL_KEYS,
    _DOTTED_IDENTIFIER_RE,
    _FIGURE_FRAGMENT_REVIEW_REASON,
    _HEX_LITERAL_RE,
    _IDENTIFIER_NUMBER_LABELS,
    _INLINE_NUMBER_RE,
    _INLINE_TOKEN_RE,
    _InlineToken,
    _NUMBERED_TABLE_CAPTION_RE,
    _NumberedTableRun,
    _READER_BARE_SEE_REFERENCE_RE,
    _READER_CITATION_PLACEHOLDER_SEQUENCE_PATTERN,
    _READER_CITATION_PROSE_LEAD_PATTERN,
    _READER_EMPTY_TYPED_LOCATOR_RES,
    _READER_EMPTY_VALUE,
    _READER_IMPERATIVE_SEE_CITATION_SOURCE_RE,
    _READER_LAYOUT_HEADER_RE,
    _READER_LAYOUT_SNIPPET_MIN_CHARS,
    _READER_LEADING_NORMATIVE_RE,
    _READER_LOCATOR_BARE_NUMBER_FORMS,
    _READER_LOCATOR_JOIN_PATTERN,
    _READER_LOCATOR_LIST_SENTENCE_END_PATTERN,
    _READER_LOCATOR_NUMBER_PATTERN,
    _READER_LOCATOR_PREFIXES,
    _READER_LOCATOR_STRUCTURED_FOLLOWING_WORD_PATTERN,
    _READER_LOCATOR_STRUCTURED_LIST_FINAL_PATTERN,
    _READER_LOCATOR_TRUE_SENTENCE_END_PATTERN,
    _READER_NORMATIVE_VERB_RE,
    _READER_PLURAL_LOCATOR_REFERENCE_RES,
    _READER_PROSE_TAIL_START_RE,
    _READER_PROSE_VERB_RE,
    _READER_PUBLICATION_METADATA_RE,
    _READER_SCOPED_CITATION_SOURCE_RE,
    _READER_STRICT_TABLE_REFERENCE_RE,
    _READER_TYPED_LOCATOR_REFERENCE_RES,
    _READER_TYPED_REFERENCE_PLACEHOLDER_RE,
    _ReaderTableTextIndex,
    _SEMANTIC_OPERATOR_KEYS,
    _SLASH_LITERAL_RE,
    _TABLE_CASE_BEARING_TOKEN_RE,
    _TABLE_FIGURE_LOCATOR_REFERENCE_RE,
    _TABLE_METADATA_TITLE_RE,
    _TABLE_PAGE_EDGE_MAX_FRACTION,
    _TABLE_PAIR_SIMILARITY_THRESHOLD,
    _TableVisualGroup,
    _UNICODE_BULLET_MARKERS,
    _VARIABLE_LITERAL_RE,
    _XML_TAG_RE,
    _additional_table_field_displays,
    _ascii_left_arrow_has_explicit_context,
    _audit_added_snippets,
    _audit_removed_snippets,
    _audit_replaced_snippets,
    _build_table_changes,
    _canonical_generic_column_row_key,
    _canonical_inline_number,
    _canonical_table_section_number_path,
    _captioned_table_adjacent_continuation_indexes,
    _case_aware_display_text_key,
    _case_bearing_token_index,
    _complete_reader_occurrences,
    _complete_table_row_display,
    _contextual_chinese_number_key,
    _critical_inline_key_is_material,
    _critical_inline_signature,
    _cross_clause_anchor_chain,
    _cross_clause_bracketed_pair_evidence,
    _cross_clause_candidate_is_mutual_unique_best,
    _cross_clause_descriptor_anchors,
    _cross_clause_descriptor_similarity,
    _cross_schema_table_value_key,
    _delete_insert_distance_within_budget,
    _displayed_similarity_one,
    _enclosing_delimiter_index,
    _exact_table_run_content_key,
    _expand_chinese_inline_token,
    _family_map_is_one_to_one,
    _first_nonempty_table_cell,
    _first_table_field,
    _first_table_order_difference_indexes,
    _full_document_selected_for_reader_cleanup,
    _generic_boundary_body_entries,
    _generic_boundary_entries_look_like_header,
    _generic_boundary_flattened_key,
    _generic_boundary_merge_pattern_evidence,
    _generic_boundary_merge_patterns_for_entries,
    _generic_data_matches_boundary_merge_pattern,
    _generic_header_matches_boundary_merge_pattern,
    _generic_header_matches_explicit_rows,
    _generic_header_value_tokens,
    _generic_table_entries_look_like_header,
    _generic_table_width_exceeds_boundary_budget,
    _identifier_span_has_expression_context,
    _identityless_schema_rows_share_stable_facts,
    _identityless_table_row_schema,
    _inline_ampersand_is_semantic,
    _inline_compact_separator_is_technical,
    _inline_context_word,
    _inline_contextual_punctuation_key,
    _inline_count_value_consumed,
    _inline_literal_quote_key,
    _inline_measurement_modifier_key,
    _inline_semantic_operator_key,
    _inline_sentence_punctuation_is_ignored,
    _inline_token_key,
    _inline_tokens,
    _inline_x_is_multiply,
    _is_arrow_operator_noise,
    _is_leading_ascii_symbol,
    _is_monotonic_alpha_sequence,
    _is_overwide_generic_table_row,
    _is_physical_page_fallback_title,
    _is_reader_page_furniture_change,
    _is_unambiguous_inline_list_marker,
    _labeled_table_row_value_display,
    _longest_common_caption_subsequence,
    _longest_common_index_pairs,
    _longest_nonsequential_single_alpha_run,
    _looks_like_english_noun_modifier,
    _looks_like_plural_count_noun,
    _looks_like_revision_table_context_title,
    _make_table_row_change,
    _merge_cjk_tokens_across_ignored_punctuation,
    _mutual_unique_non_crossing_table_pairs,
    _normalize_compact_symbol_spacing,
    _normalize_generic_table_header_wraps,
    _normalize_inline_math_token,
    _normalize_table_row_math_text,
    _normalize_unambiguous_leading_list_marker,
    _normalized_content_similarity,
    _normalized_numbered_table_caption,
    _number_word_phrase_has_positive_count_context,
    _numbered_table_runs,
    _numeric_lexical_signature,
    _ordered_narrative_table_row_changes,
    _ordered_table_changes,
    _ordered_table_row_changes,
    _page_facts_are_rendered_equal,
    _pair_anchored_unmatched_table_geometry,
    _pair_exact_duplicate_caption_runs_by_content_lcs,
    _pair_locator_renumbered_table_geometry,
    _pair_same_caption_table_groups,
    _pair_single_page_window_table_geometry,
    _pair_supported_cross_clause_table_runs,
    _pair_unique_caption_core_multipart_groups,
    _pair_unique_descriptive_caption_renumberings,
    _paired_table_order_previews,
    _paired_table_visuals,
    _partition_overwide_generic_rows,
    _partition_unproven_identityless_schema_rows,
    _proven_publication_header_section_change,
    _reader_adjacent_table_bridge_is_covered,
    _reader_audit_without_occurrences,
    _reader_change_is_coordinate_proven_table_body_duplicate,
    _reader_change_is_layout_reorder_only,
    _reader_change_with_replaced_pair_review,
    _reader_change_without_coordinate_owned_fragments,
    _reader_change_without_coordinate_table_fragments,
    _reader_change_without_covered_standalone_table_references,
    _reader_change_without_evidenced_table_body_fragments,
    _reader_change_without_evidenced_table_caption_fragments,
    _reader_change_without_figure_visual_fragments,
    _reader_change_without_locator_renumbering,
    _reader_change_without_proven_child_clause_renumber,
    _reader_change_without_proven_heading_renumber,
    _reader_change_without_publication_metadata,
    _reader_change_without_range_placeholder_heading,
    _reader_change_without_replaced_pairs,
    _reader_change_without_unreadable_singletons,
    _reader_changes_without_cross_card_locator_pairs,
    _reader_clean_single_side_table_fragments,
    _reader_cosmetic_text_key,
    _reader_evidence_physical_key,
    _reader_evidenced_fragment_flags,
    _reader_filter_evidenced_table_fragments,
    _reader_filter_evidenced_table_pairs,
    _reader_has_short_unknown_pua_formula,
    _reader_hidden_empty_table_fields,
    _reader_is_numbered_table_caption_fragment,
    _reader_is_publication_header_furniture,
    _reader_is_publication_metadata,
    _reader_nearby_table_fallback_text,
    _reader_neutralize_locator_numbers,
    _reader_neutralize_scoped_citation_source_types,
    _reader_nonspace_character_counts,
    _reader_pair_is_layout_token_reorder,
    _reader_prose_tail_candidate,
    _reader_prose_tail_candidates,
    _reader_refill_across_change_kinds,
    _reader_refill_visible_occurrences,
    _reader_reorder_has_table_schema,
    _reader_repair_duplicate_scientific_operators,
    _reader_replaced_table_reference_is_evidenced,
    _reader_section_change,
    _reader_section_has_unverified_private_glyph,
    _reader_short_formula_fragment,
    _reader_single_cross_pair_table_fragment,
    _reader_snippet_collapse_kind,
    _reader_snippet_is_evidenced_table_fragment,
    _reader_standalone_table_reference_is_covered,
    _reader_standalone_table_reference_number,
    _reader_starts_with_normative_prose,
    _reader_strip_evidenced_table_caption_prefix,
    _reader_strip_evidenced_table_suffix,
    _reader_table_body_tokens,
    _reader_table_caption_change_is_locator_renumbering,
    _reader_table_caption_number,
    _reader_table_change_proves_section_duplicate,
    _reader_table_changes,
    _reader_table_character_coverage_contributes,
    _reader_table_group_audit_text,
    _reader_table_physical_key,
    _reader_table_text_by_page,
    _reader_table_text_contributes_to_snippet,
    _reader_table_text_covers_cross_pair_fragment,
    _reader_table_text_for_snippet,
    _reader_table_text_index,
    _reader_table_title_number,
    _reader_table_token_coverage_proves_duplicate,
    _reader_tables_for_change_side,
    _reader_text_contains_table_reference,
    _reader_tiny_table_bridge_is_covered,
    _reader_token_is_explicit_table_header,
    _reader_uncovered_evidence_tokens,
    _reader_values_match_after_locator_renumbering,
    _reader_visible_prose_tail,
    _remove_cross_schema_header_rows,
    _remove_equal_table_rows,
    _remove_matching_generic_header_rows,
    _remove_one_sided_leading_schema_rows,
    _remove_proven_generic_column_boundary_reflows,
    _remove_same_position_descriptor_reflows,
    _repair_duplicate_scientific_operator,
    _resolve_duplicate_primary_table_rows,
    _resolve_unique_engineering_anchor_rows,
    _resolve_unique_identityless_schema_rows,
    _resolve_unique_numeric_qualifier_rows,
    _revision_record_groups_share_complete_baseline,
    _revision_record_row_changes,
    _revision_record_row_identity,
    _revision_record_table_key,
    _revision_table_run_identity,
    _row_change_has_revision_and_date,
    _same_page_logical_table_fragment,
    _same_page_table_indexes_in_physical_order,
    _same_page_table_ordinals_differ,
    _same_page_unique_caption_order_is_preserved,
    _same_position_candidate_row_changes,
    _section_change_has_critical_delta,
    _section_change_has_rendered_equal_sources,
    _section_change_is_window_boundary_prelude,
    _section_change_reader_identity,
    _section_content_similarity,
    _section_physical_pages,
    _selected_single_page_window_review_pairs,
    _selected_window_folio_page_pairs,
    _selected_window_locator_table_page_pairs,
    _selected_window_single_page_visual_bridges,
    _single_letter_case_is_technical,
    _single_row_exact_raw_title_pair_supported,
    _span_has_composite_identifier_context,
    _span_is_inside_paired_literal,
    _structural_number_is_ancestor,
    _structural_number_token,
    _structured_value_shape,
    _supported_cross_clause_table_family_maps,
    _supported_table_renumberings,
    _table_candidate_row_correspondence_reason,
    _table_caption_pua_review_change,
    _table_cell_value_display,
    _table_change_has_rendered_equal_sources,
    _table_change_page_sort_key,
    _table_change_reader_identity,
    _table_change_role,
    _table_change_title,
    _table_context_is_page_fallback,
    _table_context_is_weak_numeric_heading,
    _table_context_number_path,
    _table_contexts_support_adjacent_continuation,
    _table_descriptive_renumbering_key,
    _table_descriptor_wrap_fact,
    _table_descriptor_wrap_redistribution_resolution,
    _table_geometry_supports_page_boundary_continuation,
    _table_group_caption_changed,
    _table_group_has_order_independent_parameter_identity,
    _table_group_has_unreliable_multirow_alignment,
    _table_group_is_isolated_one_cell_image_fragment,
    _table_group_rows,
    _table_groups_have_relaxed_primary_identity_support,
    _table_groups_mix_generic_and_explicit_schema,
    _table_narrative_row_text,
    _table_numeric_value_changed,
    _table_order_token_state,
    _table_pua_identity_collision_resolution,
    _table_review_rows,
    _table_row_cells_for_display,
    _table_row_changes,
    _table_row_content_similarity,
    _table_row_display_key,
    _table_row_engineering_anchor,
    _table_row_field_entries,
    _table_row_fields,
    _table_row_identity_and_value_display,
    _table_row_identity_text,
    _table_row_identity_token_multiset,
    _table_row_numeric_qualifier_skeleton,
    _table_row_pairing_identity_key,
    _table_row_pairing_key,
    _table_row_pairing_primary_identity,
    _table_row_relaxed_primary_identity,
    _table_row_schemas_support_headerless_continuation,
    _table_row_single_line_text_key,
    _table_row_soft_break_identity_key,
    _table_row_text_key,
    _table_row_value_display,
    _table_rows_by_pairing_key,
    _table_rows_differ_only_by_numeric_internal_spacing,
    _table_rows_differ_only_by_one_sided_blank_unit,
    _table_rows_differ_only_by_unproven_reader_glyph_mapping,
    _table_rows_differ_only_by_whitespace,
    _table_rows_differ_only_by_whitespace_layout,
    _table_rows_equal_across_observed_schema,
    _table_rows_require_labeled_projection,
    _table_rows_support_adjacent_continuation,
    _table_rows_support_reliable_schema_boundary_continuation,
    _table_rows_support_repeated_header_continuation,
    _table_rows_support_sequential_identity_continuation,
    _table_run_primary_identity_signature,
    _table_runs_by_caption_and_context,
    _table_schema_label_key,
    _table_structure_side_description,
    _table_structured_diff_kind,
    _table_summary_item,
    _table_symbol_changed,
    _table_visual_caption_descriptor_key,
    _table_visual_caption_key,
    _table_visual_exact_caption_tail_key,
    _table_visual_group_content_similarity,
    _table_visual_group_similarity,
    _table_visual_has_valid_bbox,
    _table_visual_indexes_by_caption_key,
    _table_visual_number_identity,
    _table_visual_section_context,
    _table_visual_similarity,
    _technical_case_signatures,
    _text_backed_table_structure_review_change,
    _titlecase_token_is_technical,
    _token_shape_is_technical,
    _unique_caption_table_runs,
    _unique_exact_table_captions,
    _unique_order_sensitive_table_row_change,
    _unique_revision_rows,
    _unique_shared_revision_run_pairs,
    _unique_table_titles,
    _unverified_pua_mapping_note,
    _verified_body_page_pair_map,
)

from .comparison_policy import configured_comparison, includes_role, is_general_document

from .comparison_session import comparison_session, memoize_comparison
from .screenshot_presentation import IMAGE_VIEWER, table_context_image
from .exact_match import ratio as exact_sequence_ratio

import csv
import difflib
import html as html_lib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from .figure_filters import (
    figure_crop_owns_whole_tokens,
    figure_fragment_token_sets_regrouped,
    filter_figure_visual_snippets,
    is_figure_visual_pair,
    strip_coordinate_owned_figure_fragment,
    strip_coordinate_owned_visual_fragment,
)
from .models import (
    DiffOptions,
    DiffResult,
    FormulaVisual,
    PageExtractionAudit,
    ProseSourceVisual,
    ProseSourceVisualGroup,
    Section,
    SectionChange,
    SnippetPair,
    TableChange,
    TableRowChange,
    TableVisual,
    VisualReviewItem,
)
from .quality import (
    SUPPORTED_PROFILE,
    DiffProvenance,
    DocumentQualityMetrics,
    PairAssessment,
    ReliabilityState,
    provenance_inputs_are_identical,
)
from .table_codec import decode_table_cell, encode_table_field, split_table_cells, split_table_field
from .text_utils import (
    CHINESE_COUNT_UNIT_PATTERN,
    CHINESE_NUMBER_CHARS,
    TABLE_NUMBER_DASH_CLASS,
    canonicalize_chinese_number_token,
    canonicalize_numeric_scale_chain,
    compact_inline,
    has_observable_identifier_boundary,
    identifier_boundary_signatures,
    is_english_count_context_noun,
    is_english_count_context_verb,
    is_known_engineering_symbol_letter_suffix,
    micro_identifier_signatures,
    normalize_line,
    normalize_table_number_dashes,
    normalize_for_similarity,
    parse_number_word_phrase,
    readable_symbol_font_glyphs,
    reader_safe_glyphs,
    reader_symbol_mapping_key,
    truncate,
)
from .visual_preview import VISUAL_REVIEW_IMAGE_CSS, render_visual_mask_disclosure
from .reader_focus import FOCUS_CSS, FOCUS_SCRIPT, render_change_focus, source_button, context_source_images
from .content_equivalence import cosmetic_content_equal, neutral_email_text

_CHANGE_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "review": "需复核",
    "unchanged": "未变化",
}
_MATCH_BASIS_LABELS = {
    "similarity_exact": "编号结构与正文相似度",
    "similarity_fallback": "正文相似度回退配对",
    "evidence_suppressed_similarity_fallback": "剔除已识别表格与 Figure 证据后的正文相似度配对",
    "unique_title_body_fallback": "双侧唯一同题且正文强相似配对",
    "structural_prose_identity": "剔除结构化表格行后正文一致",
    "structural_unique_anchor": "同父同层唯一的标题技术锚点+第二正文证据",
    "unique_body_move_anchor": "双侧唯一正文身份锚点，识别已移动章节",
    "unique_body_content_anchor": "双侧唯一完整正文对应",
    "structural_leaf_body_anchor": "唯一叶章节编号与完整正文对应",
    "structural_adjacent_brackets": "前后相邻章节共同确认",
    "structural_shift_run_body": "普通兄弟章节证明一致编号偏移+多条独立正文证据",
    "structural_shift_bracketed_sentence": "前后普通兄弟夹定一致编号偏移+标题相关正文句",
    "structural_mapped_parent_body": "已配对父章节改号+子章节正文相似度",
    "structural_mapped_parent_boundary": "已配对父章节改号+边界兄弟章节+直属子章节",
    "structural_mapped_parent_unique_child": "强证据父章节配对+双侧唯一同题直属子章节",
    "document_relation_anchor": "双侧唯一相关标题+结构/引用锚点+文档顺序",
    "user_page_window_anchor": "用户指定双侧页窗强关联",
    "ambiguous_order_conflict": "章节候选顺序冲突，新增/删除结论待核实",
    "unmatched": "未配对",
}
_STRUCTURAL_MATCH_BASES = frozenset(
    {
        "structural_prose_identity",
        "structural_unique_anchor",
        "unique_body_move_anchor",
        "structural_adjacent_brackets",
        "structural_shift_run_body",
        "structural_shift_bracketed_sentence",
        "structural_mapped_parent_body",
        "structural_mapped_parent_boundary",
        "structural_mapped_parent_unique_child",
        "document_relation_anchor",
    }
)
_USER_ANCHORED_MATCH_BASES = frozenset({"user_page_window_anchor"})
_AMBIGUOUS_MATCH_BASES = frozenset({"ambiguous_order_conflict"})
_EVIDENCE_SUPPRESSED_MATCH_BASES = frozenset({"evidence_suppressed_similarity_fallback"})
_UNIQUE_TITLE_BODY_MATCH_BASES = frozenset({"unique_title_body_fallback"})
_EXPLAINED_MATCH_BASES = (
    _STRUCTURAL_MATCH_BASES
    | _USER_ANCHORED_MATCH_BASES
    | _AMBIGUOUS_MATCH_BASES
    | _EVIDENCE_SUPPRESSED_MATCH_BASES
    | _UNIQUE_TITLE_BODY_MATCH_BASES
)


def _match_basis_explanation(match_basis: str) -> str:
    """Explain the section identity evidence or ambiguity reason in a report."""

    if match_basis in _AMBIGUOUS_MATCH_BASES:
        return "双侧最强章节候选互相交叉；保留原文供核对，不自动确认新增或删除"
    if match_basis in _USER_ANCHORED_MATCH_BASES:
        return "用户页窗授权；相似度仍为全文实际值"
    if match_basis in _EVIDENCE_SUPPRESSED_MATCH_BASES:
        return "表格与 Figure 证据去重后正文授权；相似度仍为含原始版面文字的全文实际值"
    if match_basis in _UNIQUE_TITLE_BODY_MATCH_BASES:
        return "双侧唯一同题且正文强相似授权；相似度仍为含错误父层级的全文实际值"
    return "结构证据授权；相似度仍为全文实际值"

_RELIABILITY_LABELS = {
    ReliabilityState.RELIABLE: "可靠",
    ReliabilityState.DEGRADED: "需人工复核",
    ReliabilityState.INDETERMINATE: "无法判断",
}
_READER_EXTRACTION_WARNING_REASON_RE = re.compile(
    r"^(?:旧协议|新协议)有\s+\d+\s+条抽取警告。$"
)
_READER_TABLE_ITEM_MAX_CHARS = 180
_READER_TABLE_VALUE_MAX_CHARS = 240


_REPORT_COMPONENT_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_report_component(value: str, *, limit: int = 72) -> str:
    """Turn one imported filename stem into a portable report name component."""

    normalized = unicodedata.normalize("NFKC", str(value))
    cleaned = _REPORT_COMPONENT_RE.sub("_", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "协议"
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    cleaned = cleaned[:limit].rstrip(" .")
    return cleaned or "协议"


def _report_pair_label(old_pdf: str | Path, new_pdf: str | Path) -> str:
    """Return the user-facing report directory label for one PDF pair."""

    old_name = _safe_report_component(Path(old_pdf).stem)
    new_name = _safe_report_component(Path(new_pdf).stem)
    return f"{old_name}_vs_{new_name}"


def _section_change_render_suppression_reason(
    change: SectionChange,
    result: DiffResult,
    page_pairs: dict[int, int],
) -> str | None:
    if not is_general_document() and _proven_publication_header_section_change(change, result):
        return "重复页眉与各自 PDF Title 及受限版本前缀精确相符；页眉差异保留在原始审计，不计入正文变化。"
    return _render_suppression_reason(
        _section_physical_pages(change.old_section, side="old", result=result),
        _section_physical_pages(change.new_section, side="new", result=result),
        page_pairs,
    )


def _render_suppression_reason(
    old_pages: set[int],
    new_pages: set[int],
    page_pairs: dict[int, int],
) -> str | None:
    if _page_facts_are_rendered_equal(old_pages, new_pages, page_pairs):
        return "坐标已证明的页边区域之外，旧/新对应源页在高分辨率渲染下逐像素相同；抽取结构差异仅保留在原始审计字段。"
    return None


def _create_report_directory(result, output_dir, *, exclusive=False):
    base_dir = Path(output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pair_label = _report_pair_label(result.old_pdf, result.new_pdf)
    report_dir = base_dir / f"{pair_label}_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=not exclusive)
    return report_dir


@comparison_session
@configured_comparison
def write_reports(
    result: DiffResult,
    output_dir: str | Path,
    options: DiffOptions,
) -> dict[str, Path]:
    """Write Markdown, HTML, TXT, CSV, and JSON report artifacts.

    The HTML report is the most visual review surface: it groups changes by
    section, shows old/new snippets side by side, and highlights inline
    replacements. Markdown/TXT remain useful for copy-paste workflows, separate
    prose/table CSV files support Excel review, and JSON preserves the complete
    section and table-change audit model.
    """

    from .comparison_content import build_table_content, build_comparison_view, validate_result_options
    from .comparison_preparation import prepare_comparison_evidence
    validate_result_options(result, options)
    report_dir = _create_report_directory(result, output_dir)
    tables = build_table_content(result)
    evidence = prepare_comparison_evidence(result, tables, report_dir)
    view = build_comparison_view(result, options, tables=tables,
                                physical_receipts=evidence.physical_receipts,
                                formula_accepted=evidence.formula_accepted)
    return render_reports(view, report_dir, evidence)


from .comparison_policy import configured_view


@configured_view
def render_reports(view, report_dir, evidence, *, transaction_receipts=None, original_changes=()):
    """将固定结论一次输出到各格式；不参与内容配对和证据接受。"""
    result, options = view.result, view.options
    pair_label = _report_pair_label(result.old_pdf, result.new_pdf)
    table_groups = view.tables.table_groups
    table_changes = view.tables.table_changes
    identical_body_page_pairs = view.tables.identical_body_page_pairs
    reader_changes = view.reader_changes
    reader_table_changes = view.reader_table_changes
    similarity_review_changes = view.similarity_review_changes
    similarity_review_tables = view.similarity_review_tables
    reader_result = view.reader_result
    physical_html = evidence.physical_html
    physical_payload = evidence.physical_payload
    physical_receipts = evidence.physical_receipts
    physical_csv_path = report_dir / 'physical_table_records.csv'
    from .physical_table_rows import physical_text_export
    appendix_change_ids = {
        _section_change_reader_identity(c): f"A-C{i}"
        for i, c in enumerate(similarity_review_changes, 1)
    }
    appendix_table_ids = {
        _table_change_reader_identity(c): f"A-T{i}"
        for i, c in enumerate(similarity_review_tables, 1)
    }
    reader_change_card_ids = {
        _section_change_reader_identity(change): f"C{index}"
        for index, change in enumerate(
            (change for change in reader_changes if includes_role(change.role)),
            start=1,
        )
    }
    reader_changes_by_identity = {
        _section_change_reader_identity(change): change
        for change in [*reader_changes, *similarity_review_changes]
    }
    reader_table_card_ids = {
        _table_change_reader_identity(change): f"T{index}"
        for index, change in enumerate(reader_table_changes, start=1)
    }
    markdown = _render_markdown(reader_result, options, reader_table_changes)
    if similarity_review_changes or similarity_review_tables:
        appendix_lines = ["", '<details><summary>内容与配对相似度均为 1.000：按偏好不计入差异，可展开审查</summary>', "",
                          "这些条目只有在完整正文内容相同且没有数值、技术标识或语义运算符变化时才会折叠；以下保留原始比较事实。", ""]
        _append_markdown_changes(appendix_lines, similarity_review_changes, start_index=1)
        _append_markdown_table_changes(appendix_lines, similarity_review_tables)
        appendix_lines = [re.sub(r"^### (T?)(\d+)\.", lambda m: "### A-" + ("T" if m[1] else "C") + m[2] + ".", line) for line in appendix_lines]
        markdown += "\n".join([*appendix_lines, "</details>", ""])
        markdown = reader_safe_glyphs(markdown)
        markdown = markdown.replace(_empty_report_message(reader_result), "主差异清单为空；内容与配对相似度均为 1.000 的配对证据收在末尾附录。")
    html = _render_html(
        reader_result,
        options,
        reader_table_changes,
        prose_source_visuals=result.prose_source_visuals,
        similarity_review_changes=similarity_review_changes,
        similarity_review_tables=similarity_review_tables,
    )
    uncertain_tables = [group for group in table_groups
                        if any(not t.row_alignment_reliable or not t.content_fully_represented
                               for t in (*group.old_tables, *group.new_tables))]
    table_evidence_refs = {
        _table_group_identity(change.old_tables, change.new_tables): f"T{index}"
        for index, change in enumerate(reader_table_changes, start=1)
    }
    table_evidence_refs.update(
        {
            _table_group_identity(change.old_tables, change.new_tables): f"A-T{index}"
            for index, change in enumerate(similarity_review_tables, start=1)
        }
    )
    uncertainty_rows = []
    for group in uncertain_tables:
        def side_summary(tables):
            return "; ".join(f"PDF {t.page_number}: {t.title or '未识别表题'}" for t in tables) or "尚未找到对应表"
        uncertainty_rows.append({"old": side_summary(group.old_tables),
                                 "new": side_summary(group.new_tables),
                                 "old_sources": [{"page": t.page_number, "bbox": t.bbox} for t in group.old_tables],
                                 "new_sources": [{"page": t.page_number, "bbox": t.bbox} for t in group.new_tables],
                                 "table_evidence_ref": table_evidence_refs.get(
                                     _table_group_identity(group.old_tables, group.new_tables)
                                 ),
                                 "reason": "文字对应或行列归属尚未完全验证；不能据此认定新增、删除或一致。"})
    if uncertainty_rows:
        heading = f"表格对应待核实（{len(uncertainty_rows)} 项，不计为已确认差异）"
        appendix = "<details><summary>" + heading + "</summary>" + "".join(
            '<section class="table-change"><p>旧版：' + _escape(row['old']) + "；新版：" + _escape(row['new'])
            + "。" + row['reason']
            + (
                f' 截图已在 {_escape(row["table_evidence_ref"])} 表格证据中展示，本处不重复嵌入。'
                if row.get("table_evidence_ref")
                else ''
            )
            + (
                ''
                if row.get("table_evidence_ref")
                else '<div class="table-shot-grid">'
                + _render_table_shot_group("旧版", group.old_tables)
                + _render_table_shot_group("新版", group.new_tables, side="new")
                + "</div>"
            )
            + "</section>" for row, group in zip(uncertainty_rows, uncertain_tables)) + "</details>"
        html = html.replace("</main>", appendix + "</main>") if "</main>" in html else html.replace("</body>", appendix + "</body>")
        markdown += "\n\n<details><summary>" + heading + "</summary>\n\n" + "\n".join(
            f"- 旧版：{row['old']}；新版：{row['new']}。{row['reason']}" for row in uncertainty_rows) + "\n\n</details>\n"
    if physical_html:
        if '</main>' not in html and '</body>' not in html:
            raise ValueError('Physical row receipt cannot be emitted without a report insertion point')
        html = html.replace('</main>', physical_html + '</main>') if '</main>' in html else html.replace('</body>', physical_html + '</body>')
    from .formula_source_review_export import render_formula_source_reviews
    formula_records = [r for c in [*reader_changes, *similarity_review_changes] for r in c.formula_review_records]
    formula_html, formula_md, formula_txt = render_formula_source_reviews(formula_records, report_dir)
    if formula_records:
        html = html.replace('</main>', formula_html + '</main>')
        markdown += formula_md
        html = html.replace(_escape(_empty_report_message(reader_result)), '正文差异清单为空；仍有公式来源待核实，不能认定内容相同。')
        markdown = markdown.replace(_empty_report_message(reader_result), '正文差异清单为空；仍有公式来源待核实，不能认定内容相同。')
    text = _markdown_to_plain_text(markdown)
    markdown += physical_text_export(physical_payload, markdown=True)
    text += physical_text_export(physical_payload)
    text += formula_txt
    if transaction_receipts is not None:
        receipt_payload, receipt_text, receipt_html, _receipt_csv = transaction_receipts
        markdown += "\n\n原物理三格（未证明整表逻辑对应）\n" + receipt_text + "\n"
        text += "\n\n原物理三格（未证明整表逻辑对应）\n" + receipt_text + "\n"
        if "</body>" not in html:
            raise ValueError("来源凭证缺少报告插入位置")
        html = html.replace("</body>", receipt_html + "</body>")
    csv_rows = _rows_for_csv(reader_changes)
    table_csv_rows = _rows_for_table_csv(reader_table_changes)
    from .coverage_review import coverage_review_items
    sections_payload = {
        "comparison_focus": "substantive_content",
        "comparison_profile": options.comparison_profile,
        "formula_source_review_count": len(formula_records),
        "formula_source_reviews": formula_records,
        "uncertain_table_correspondences": uncertainty_rows,
        "physical_table_receipts": physical_payload,
        "physical_table_dedup_authorizations": sorted(physical_receipts),
        "visual_owned_spans": result.visual_owned_spans,
        "url_literal_source_receipts": {"old": result.old_url_literal_receipts, "new": result.new_url_literal_receipts},
        "coverage_review_items": coverage_review_items(result),
        "content_changes": [_change_to_dict(change) for change in reader_changes],
        "content_table_changes": [_table_change_to_dict(change) for change in reader_table_changes],
        "similarity_review_changes": [_change_to_dict(c) for c in similarity_review_changes],
        "similarity_review_table_changes": [_table_change_to_dict(c) for c in similarity_review_tables],
        "old_pdf": str(result.old_pdf),
        "new_pdf": str(result.new_pdf),
        "old_total_pages": _source_page_count(result, "old"),
        "new_total_pages": _source_page_count(result, "new"),
        "old_total_pages_known": result.old_total_pages_known,
        "new_total_pages_known": result.new_total_pages_known,
        "old_selected_pages": _selected_page_payload(result, "old"),
        "new_selected_pages": _selected_page_payload(result, "new"),
        "changes": [
            {
                **_change_to_dict(
                    change,
                    display_change=reader_changes_by_identity.get(
                        _section_change_reader_identity(change)
                    )
                    or replace(
                        change,
                        added_snippets=[],
                        removed_snippets=[],
                        replaced_snippets=[],
                        review_replaced_snippets=[],
                        omitted_snippet_count=0,
                    ),
                    role_override=(
                        "document_metadata"
                        if _proven_publication_header_section_change(change, result)
                        else None
                    ),
                ),
                "reader_card_id": reader_change_card_ids.get(
                    _section_change_reader_identity(change)
                ),
                "appendix_card_id": appendix_change_ids.get(_section_change_reader_identity(change)),
                "reader_suppression_reason": _section_change_render_suppression_reason(
                    change,
                    result,
                    identical_body_page_pairs,
                ),
            }
            for change in result.changes
        ],
        "table_changes": [
            _table_change_audit_to_dict(
                change,
                reader_card_id=reader_table_card_ids.get(
                    _table_change_reader_identity(change)
                ),
                appendix_card_id=appendix_table_ids.get(
                    _table_change_reader_identity(change)
                ),
                suppression_reason=_render_suppression_reason(
                    {table.page_number for table in change.old_tables},
                    {table.page_number for table in change.new_tables},
                    identical_body_page_pairs,
                ),
            )
            for change in table_changes
        ],
        # 公式自动对比已经关闭；即使旧调用方注入遗留 FormulaChange，
        # 读者报告和机器结果也不能重新发布不可靠的公式结论。
        "formula_changes": [],
        "visual_review_items": [
            {**_visual_review_item_to_dict(item), "reader_card_id": f"V{index}"}
            for index, item in enumerate(result.visual_review_items, start=1)
        ],
        "prose_source_visuals": [
            _prose_source_visual_group_to_dict(group)
            for group in result.prose_source_visuals
        ],
        "old_sections": [_section_to_dict(section) for section in result.old_sections],
        "new_sections": [_section_to_dict(section) for section in result.new_sections],
        "old_table_visuals": [_table_visual_to_dict(table) for table in result.old_table_visuals],
        "new_table_visuals": [_table_visual_to_dict(table) for table in result.new_table_visuals],
        "old_formula_visuals": [_formula_visual_to_dict(formula) for formula in result.old_formula_visuals],
        "new_formula_visuals": [_formula_visual_to_dict(formula) for formula in result.new_formula_visuals],
        "warnings": result.warnings,
        "assessment": _assessment_to_dict(_assessment_for_report(result)),
        "provenance": _provenance_to_dict(result.provenance),
        "extraction_audit": {
            "old": _extraction_audit_to_dict(result.old_extraction_audit),
            "new": _extraction_audit_to_dict(result.new_extraction_audit),
        },  # 逐页解析事实单独输出，绝不把 PageText 或 DocumentBlock 原文放入审计记录。
    }

    if transaction_receipts is not None:
        import hashlib
        sections_payload.update(
            original_changes_audit=[_change_to_dict(c) for c in original_changes],
            three_cell_records=[{
                **{k: v for k, v in row.items() if k != "image"},
                "image_sha256": hashlib.sha256(row["image"].encode()).hexdigest(),
            } for row in transaction_receipts[0]],
            table_projection_selection="candidate",
        )

    # Keep the human-facing report files discoverable from the imported PDF
    # names.  The folder already carries the pair and timestamp; using the
    # same pair label for the files prevents a generic ``protocol_diff_report``
    # from hiding which two protocol revisions were compared when a user moves
    # or shares an individual report.
    report_stem = pair_label
    md_path = report_dir / f"{report_stem}.md"
    html_path = report_dir / f"{report_stem}.html"
    txt_path = report_dir / f"{report_stem}.txt"
    csv_path = report_dir / "changes.csv"
    table_csv_path = report_dir / "table_changes.csv"
    json_path = report_dir / "protocol_diff_data.json"

    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "change_type",
                "role",
                "report_location",
                "new_location",
                "old_location",
                "new_pages",
                "old_pages",
                "similarity",
                "pair_similarity",
                "content_similarity",
                "critical_content_equal",
                "match_basis",
                "match_basis_label",
                "review_reason",
                "summary",
                "added_snippets",
                "removed_snippets",
                "replaced_snippets",
                "omitted_snippet_count",
                "context_review_records",
                "display_added_snippets",
                "display_replaced_snippets",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    with table_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "table_change_type",
                "role",
                "old_titles",
                "new_titles",
                "old_pages",
                "new_pages",
                "pair_similarity",
                "content_similarity",
                "item",
                "old_value",
                "new_value",
                "row_change_type",
                "row_role",
                "source_receipts",
            ],
        )
        writer.writeheader()
        writer.writerows(table_csv_rows)
    json_path.write_text(json.dumps(sections_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for original, name, rows in (
        (csv_path, "similarity_review_changes.csv", _rows_for_csv(similarity_review_changes)),
        (table_csv_path, "similarity_review_table_changes.csv", _rows_for_table_csv(similarity_review_tables)),
    ):
        with original.open(encoding="utf-8-sig", newline="") as handle:
            fields = next(csv.reader(handle))
        with (report_dir / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    return {
        "report_dir": report_dir,
        "markdown": md_path,
        "html": html_path,
        "text": txt_path,
        "csv": csv_path,
        "table_csv": table_csv_path,
        "physical_table_csv": physical_csv_path,
        "similarity_review_csv": report_dir / "similarity_review_changes.csv",
        "similarity_review_table_csv": report_dir / "similarity_review_table_changes.csv",
        "json": json_path,
    }


def _table_pairing_similarity(change: TableChange) -> float | None:
    """Return the identity score retained for table pairing/audit output."""

    if not change.old_tables or not change.new_tables:
        return None
    if change.pairing_similarity is not None:
        return change.pairing_similarity
    # Legacy callers may construct TableChange without the new field.  Keep
    # their serialized audit stable while all newly built changes carry both
    # independent scores explicitly.
    return _table_visual_group_similarity(change.old_tables, change.new_tables)


def _table_group_identity(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Bind a logical table group to its exact source visual objects."""

    return (
        tuple(id(table) for table in old_tables),
        tuple(id(table) for table in new_tables),
    )


def _render_markdown(
    result: DiffResult,
    options: DiffOptions,
    table_changes: list[TableChange],
) -> str:
    """Render the main review report in Markdown."""

    counts = _change_counts(result.changes)
    # write_reports 已把元信息从读者副本剔除；此处只渲染技术正文，避免空板块和零值指标占空间。
    technical_changes = [change for change in result.changes if includes_role(change.role)]
    technical_review_count = sum(
        len(change.formula_review_records) + len(change.review_replaced_snippets)
        or (1 if change.change_type == "review" else 0)
        for change in technical_changes
    )
    material_technical_count = sum(
        change.change_type != "review" for change in technical_changes
    )
    # Every table finding is page evidence, including review-only rows.
    table_changes = _ordered_table_changes(table_changes)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comparison_note = _comparison_method_note(result)
    scope_note = _report_scope_note(options)
    assessment = _assessment_for_report(result)
    material_table_changes = _material_table_changes(table_changes)
    table_row_change_count = sum(
        len(_material_table_row_changes(change))
        for change in table_changes
    )
    table_review_count = sum(
        len(_table_review_rows(change))
        for change in table_changes
    )
    lines: list[str] = [
        "# 协议 PDF 差异报告",
        "",
        f"- 旧协议: `{result.old_pdf}`",
        f"- 新协议: `{result.new_pdf}`",
        f"- 生成时间: {generated_at}",
        f"- 旧/新页数: {_source_page_count(result, 'old')} / {_source_page_count(result, 'new')}",
        f"- 旧选择页: {_selected_page_label(result, 'old')}",
        f"- 新选择页: {_selected_page_label(result, 'new')}",
        f"- 章节匹配阈值: {options.min_section_match_similarity:.2f}",
        f"- 比较方式: {comparison_note}",
        f"- 报告范围: {scope_note}",
        "",
        "## 识别可信度",
        "",
        f"- 状态: {_RELIABILITY_LABELS[assessment.state]} (`{assessment.state.value}`)",
        f"- 结论: {assessment.headline}",
        f"- 当前支持范围: {assessment.supported_profile}",
        *[f"- 原因: {reason}" for reason in _reader_assessment_reasons(assessment)],
        "",
        "## 汇总",
        "",
        "正文差异候选按章节统计，不代表已确认技术含义发生变化。待核实项尚未分类，按片段对或整节计数，可能与候选章节重叠；请结合各项原因、识别可信度及核对范围阅读。",
        "",
        "| 类型 | 数量 |",
        "|---|---:|",
        f"| 正文差异候选（章节） | {material_technical_count} |",
        f"| 正文待核实项（未分类） | {technical_review_count} |",
        f"| 公式来源待核实 | {sum(len(c.formula_review_records) for c in technical_changes)} |",
        f"| 章节修改 / 新增 / 删除 | {counts.get('modified', 0)} / {counts.get('added', 0)} / {counts.get('deleted', 0)} |",
        "| 公式自动对比项（已关闭） | 0 |",
        f"| 视觉漏检核对项 | {len(result.visual_review_items)} |",
        f"| 变化表格 | {len(material_table_changes)} |",
        f"| 表格行变化 | {table_row_change_count} |",
        f"| 表说明变化 | {sum(r.row_role == 'annotation' for c in table_changes for r in c.row_changes)} |",
        f"| 表格复核项 | {table_review_count} |",
        "",
    ]

    lines.extend(_coverage_review_markdown(result))

    # 表格截图和行级事实是读者的首要对比证据，必须先于长篇技术正文。
    if table_changes:
        _append_markdown_table_changes(lines, table_changes)

    if result.visual_review_items:
        lines.extend(
            [
                "## 视觉漏检核对",
                "",
                "说明: 已核对的整页或固定图示区域存在未解释的像素变化；各项说明列出核对范围，该证据不自动解释图形语义。",
                "",
            ]
        )
        for index, item in enumerate(result.visual_review_items, start=1):
            lines.extend(
                [
                    f"### V{index}. 旧页 {item.old_page_number or '-'} / 新页 {item.new_page_number or '-'}",
                    f"- 说明: {item.reason}",
                    f"- 像素相似度: {item.pixel_similarity:.4f}",
                    f"- 变化像素比例: {item.changed_pixel_ratio:.4%}",
                    f"- 页面配对依据: {item.alignment_method}",
                    "",
                ]
            )

    lines.extend(
        [
            "## 技术正文变化与复核",
            "",
            "说明: 页码来自 PDF 抽取顺序；如果 PDF 自身页脚页码不同，请以 PDF 阅读器显示为准。",
            "",
        ]
    )
    _append_markdown_changes(lines, technical_changes, start_index=1)
    if not technical_changes:
        message = (
            _empty_report_message(result)
            if not table_changes and not result.visual_review_items
            else "未列出技术正文变化；是否可确认一致请以顶部识别可信度为准。"
        )
        lines.extend([message, ""])

    return reader_safe_glyphs("\n".join(lines))
    # Markdown 及由它派生的 TXT 都是读者界面；JSON/CSV 仍保留原始 PUA 审计值。


def _append_markdown_table_changes(
    lines: list[str],
    table_changes: list[TableChange],
) -> None:
    """把表格截图对应的结构化事实追加到 Markdown 读者报告。"""

    # 标题与 HTML 使用相同文案，便于不同格式之间快速定位同一证据区。
    lines.extend(["## 表格补充证据（变化与复核）", ""])
    # 一个循环项对应一张旧/新逻辑表，保持 T 编号与 HTML 导航一致。
    for index, table_change in enumerate(table_changes, start=1):
        lines.append(f"### T{index}. {_table_change_title(table_change)}")
        lines.append(f"- 角色: {table_change.role}")
        lines.append(f"- 类型: {_CHANGE_LABELS.get(table_change.change_type, table_change.change_type)}")
        lines.append(f"- 旧表: {_table_side_description(table_change.old_tables)}")
        lines.append(f"- 新表: {_table_side_description(table_change.new_tables)}")
        # 双侧均存在时同时保留内容分数和配对分数；后者只说明为何判为同一逻辑表。
        if table_change.old_tables and table_change.new_tables:
            lines.append(f"- 内容相似度: {table_change.similarity:.6f}")
            pairing = _table_pairing_similarity(table_change)
            if pairing is not None:
                lines.append(f"- 配对相似度（仅用于表格身份）: {pairing:.6f}")
        # 表题变化与行变化分开说明，避免把编号变化误读成参数变化。
        if table_change.caption_changed:
            lines.append("- 表题/表号发生变化；行内容变化另列如下。")
        # 每个结构化行事实保留旧值、新值和复核类型，数值不会被标题编号过滤覆盖。
        for row_change in table_change.row_changes:
            reader_old_value = _reader_table_inline_text(row_change.old_value)
            reader_new_value = _reader_table_inline_text(row_change.new_value)
            difference_hint = _reader_pair_difference_hint(
                reader_old_value,
                reader_new_value,
            )
            item = _reader_table_cell_text(
                _reader_table_inline_text(row_change.item),
                max_chars=_READER_TABLE_ITEM_MAX_CHARS,
            )
            old_value = _reader_table_cell_text(
                reader_old_value,
                max_chars=_READER_TABLE_VALUE_MAX_CHARS,
                difference_hint=difference_hint,
            )
            new_value = _reader_table_cell_text(
                reader_new_value,
                max_chars=_READER_TABLE_VALUE_MAX_CHARS,
                difference_hint=difference_hint,
            )
            lines.append(
                f"- {row_change.change_type}: {item} | "
                f"旧 `{old_value}` | 新 `{new_value}`"
            )
            # 未验证的私用区字符映射必须紧跟该行提示，不能静默伪装成确定文本。
            if glyph_note := _unverified_pua_mapping_note(
                row_change.old_value,
                row_change.new_value,
                reader_old_text=reader_old_value,
                reader_new_text=reader_new_value,
                force_reader_equivalent=(
                    row_change.change_type == "需人工复核"
                    and _table_row_display_key(reader_old_value)
                    == _table_row_display_key(reader_new_value)
                ),
            ):
                lines.append(f"  - 说明: {glyph_note}")
        lines.append("")


def _append_markdown_changes(
    lines: list[str],
    changes: list[SectionChange],
    *,
    start_index: int,
) -> int:
    """Append one role group and return the next global card index."""

    for index, change in enumerate(changes, start=start_index):
        label = _CHANGE_LABELS.get(change.change_type, change.change_type)
        display_location = _display_change_location(change)
        lines.append(f"### {index}. {label}: {display_location}")
        if change.old_section:
            lines.append(
                f"- 旧位置: {_display_section_location(change.old_section, '旧')}（页 {change.old_section.page_range}）"
            )
        if change.new_section:
            lines.append(
                f"- 新位置: {_display_section_location(change.new_section, '新')}（页 {change.new_section.page_range}）"
            )
        if change.old_section and change.new_section:
            content_similarity = _section_content_similarity(change)
            if content_similarity is not None:
                lines.append(f"- 内容相似度（完整正文）: {content_similarity:.6f}")
            lines.append(f"- 配对相似度（仅用于章节身份）: {change.similarity:.6f}")
            if change.match_basis in _EXPLAINED_MATCH_BASES:
                lines.append(
                    "- 配对依据: "
                    f"{_MATCH_BASIS_LABELS[change.match_basis]}"
                    f"（{_match_basis_explanation(change.match_basis)}）"
                )
        summary = _change_summary(change)
        if summary:
            lines.append(f"- 差异摘要: {summary}")
        if change.review_reason:
            lines.append(f"- 待核实原因: {change.review_reason}")

        if change.replaced_snippets:
            lines.append("- 替换片段:")
            for pair in change.replaced_snippets:
                difference_hint = _reader_pair_difference_hint(pair.old, pair.new)
                lines.append(
                    f"  - 旧: {_reader_snippet_text(pair.old, difference_hint=difference_hint)}"
                )
                lines.append(
                    f"    新: {_reader_snippet_text(pair.new, difference_hint=difference_hint)}"
                )
                if glyph_note := _unverified_pua_mapping_note(pair.old, pair.new):
                    lines.append(f"    说明: {glyph_note}")
        if change.review_replaced_snippets:
            if change.context_review_records:
                lines.append("- 上下文归属待核实（不表示适用条件相同）:")
            elif _FIGURE_FRAGMENT_REVIEW_REASON in change.review_reason:
                lines.append("- 图文差异待复核（保留原文供核对）:")
            else:
                lines.append("- 结构顺延复核（不计入核心差异，保留原文供核对）:")
            for pair in change.review_replaced_snippets:
                lines.append(f"  - 旧: {_reader_snippet_text(pair.old)}")
                lines.append(f"    新: {_reader_snippet_text(pair.new)}")
        if change.context_review_records:
            lines.append("- 上下文来源与原始分段审计（未证明适用范围相同）:")
            lines.extend(["```json", json.dumps(change.context_review_records, ensure_ascii=False, indent=2), "```"])
        if change.added_snippets:
            lines.append("- 新版待核实原文:" if change.change_type == "review" else "- 新增片段:")
            for snippet in _reader_single_list_groups(change.added_snippets):
                lines.append(
                    "  - "
                    + _reader_snippet_text(
                        snippet,
                        difference_hint=_reader_single_side_evidence_hint(snippet),
                    )
                )
        if change.removed_snippets:
            lines.append("- 旧版待核实原文:" if change.change_type == "review" else "- 删除片段:")
            for snippet in _reader_single_list_groups(change.removed_snippets):
                lines.append(
                    "  - "
                    + _reader_snippet_text(
                        snippet,
                        difference_hint=_reader_single_side_evidence_hint(snippet),
                    )
                )
        if change.omitted_snippet_count:
            lines.append(f"- {_omitted_snippet_message(change.omitted_snippet_count)}")
        lines.append("")
    return start_index + len(changes)


def _markdown_to_plain_text(markdown: str) -> str:
    """Convert the Markdown report into a readable plain-text review note.

    The Markdown report contains heading markers and table separators that are
    useful in a renderer but distracting in `.txt`. This converter keeps the
    semantic content, flattens small tables into aligned-ish rows, and avoids
    copying formatting artifacts such as `##` or `|---|---:|` into the text
    report that users may paste into email or chat.
    """

    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = re.sub(r"</?(?:details|summary)(?:\s[^>]*)?>", "", raw_line.rstrip())
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("### "):
            lines.append(stripped[4:].replace("`", ""))
            continue
        if stripped.startswith("## "):
            lines.append(stripped[3:].replace("`", ""))
            continue
        if stripped.startswith("# "):
            lines.append(stripped[2:].replace("`", ""))
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            lines.append("  ".join(cells))
            continue
        lines.append(line.replace("`", ""))

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _render_html(
    result: DiffResult,
    options: DiffOptions,
    table_changes: list[TableChange],
    *,
    prose_source_visuals: Iterable[ProseSourceVisualGroup] = (),
    similarity_review_changes: Iterable[SectionChange] = (),
    similarity_review_tables: Iterable[TableChange] = (),
) -> str:
    """Render an easy-to-scan standalone HTML review report."""

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comparison_note = _comparison_method_note(result)
    scope_note = _report_scope_note(options)
    assessment_html = _render_assessment_html(_assessment_for_report(result))
    assessment_html += _render_visual_coverage_html(result)
    # HTML 与 Markdown 共用技术正文口径，元信息只留在机器审计文件。
    technical_changes = [change for change in result.changes if includes_role(change.role)]
    technical_review_count = sum(
        len(change.formula_review_records) + len(change.review_replaced_snippets)
        or (1 if change.change_type == "review" else 0)
        for change in technical_changes
    )
    material_technical_count = sum(
        change.change_type != "review" for change in technical_changes
    )
    # Keep every table finding in the page evidence area, even if a caller
    # still passes a legacy similarity-review table iterable.
    table_changes = _ordered_table_changes(
        [*table_changes, *similarity_review_tables]
    )
    indexed_technical = sorted(
        enumerate(technical_changes, start=1),
        key=lambda item: (_section_change_page_sort_key(item[1]), item[0]),
    )
    indexed_similarity_review = sorted(
        enumerate(similarity_review_changes, start=1),
        key=lambda item: (_section_change_page_sort_key(item[1]), item[0]),
    )
    materialized_source_visuals = tuple(prose_source_visuals)
    similarity_review_tables = []
    prose_visual_lookup = {
        _prose_source_visual_identity(group): group
        for group in materialized_source_visuals
        if group.old_visuals or group.new_visuals
    }
    # Reader classification can become review after source images were built.
    # Bind by stable source sections and use genuinely unannotated pixels.
    for change in [*technical_changes, *similarity_review_changes]:
        key = _section_change_visual_identity(change)
        if key in prose_visual_lookup:
            continue
        candidates = [g for g in materialized_source_visuals
                      if (g.old_section_id, g.new_section_id) == key[1:] and (g.old_visuals or g.new_visuals)]
        if len(candidates) == 1 and change.change_type == "review":
            group = candidates[0]
            def neutral(visuals):
                return tuple(replace(v, image_data_uri=v.raw_image_data_uri, highlight_region_count=0,
                                     precision="source-page-unlocalized", source_words=())
                             for v in visuals if v.raw_image_data_uri)
            prose_visual_lookup[key] = replace(group, change_type="review", old_visuals=neutral(group.old_visuals), new_visuals=neutral(group.new_visuals))
    # A section can span a page boundary, so adjacent cards may own the same
    # physical source page.  Keep one canonical occurrence for the visible
    # screenshot and let later cards resolve focus through a hidden alias.
    prose_card_groups = [
        (
            f"change-{index}",
            prose_visual_lookup.get(_section_change_visual_identity(change)),
        )
        for index, change in indexed_technical
    ] + [
        (
            f"appendix-{index}",
            prose_visual_lookup.get(_section_change_visual_identity(change)),
        )
        for index, change in indexed_similarity_review
    ]
    # Build one source-page namespace across table and prose cards. It provides
    # stable focus targets for repeated page crops while keeping one visible
    # screenshot per physical source page.
    table_card_specs = [
        (f"table-{index}", change)
        for index, change in enumerate(table_changes, start=1)
    ] + [
        (f"table-appendix-{index}", change)
        for index, change in enumerate(similarity_review_tables, start=1)
    ]
    page_source_aliases = _build_page_source_aliases(
        table_card_specs,
        prose_card_groups,
    )
    prose_source_aliases = page_source_aliases["prose"]
    table_source_aliases = page_source_aliases["table"]
    page_source_candidates = _build_page_source_candidates(
        table_changes,
        technical_changes,
        prose_visual_lookup,
    )
    figure_source_visuals = tuple(
        group
        for group in materialized_source_visuals
        if group.old_figure_visuals or group.new_figure_visuals
    )
    title = "协议 PDF 差异报告"
    technical_nav_items = "\n".join(
        _render_nav_item(index, change) for index, change in indexed_technical
    )
    table_nav_items = "\n".join(
        _render_table_nav_item(index, change)
        for index, change in enumerate(table_changes, start=1)
    )
    nav_parts: list[str] = []
    # 表格证据是审阅入口，侧栏顺序必须与正文中的首个证据区一致。
    if table_nav_items:
        nav_parts.extend(['<div class="nav-title">表格补充证据</div>', table_nav_items])
    if technical_nav_items:
        technical_nav_class = "nav-title nav-section-gap" if nav_parts else "nav-title"
        nav_parts.extend([f'<div class="{technical_nav_class}">技术正文变化与复核</div>', technical_nav_items])
    if result.visual_review_items:
        nav_parts.extend(
            [
                '<div class="nav-title nav-section-gap">视觉漏检核对</div>',
                (
                    '<a class="nav-item nav-formula" href="#visual-review-items">'
                    '<span class="nav-label">视觉</span>'
                    '<div class="nav-body"><strong>疑似未解释变化</strong>'
                    f'<div class="nav-location">{len(result.visual_review_items)} 页待核对</div>'
                    '</div></a>'
                ),
            ]
        )
    nav_items = "\n".join(nav_parts)
    if not nav_items:
        nav_items = f'<div class="empty-nav">{_escape(_empty_report_message(result))}</div>'
        if similarity_review_changes or similarity_review_tables:
            nav_items = '<div class="empty-nav">主差异清单为空；配对证据收在末尾附录。</div>'

    technical_card_entries = [
        (
            _section_change_page_sort_key(change),
            1,
            index,
            _render_change_html(
                index,
                change,
                prose_source_visual=prose_visual_lookup.get(
                    _section_change_visual_identity(change)
                ),
                source_visual_aliases=prose_source_aliases.get(f"change-{index}"),
                render_source_visual=False,
            ),
            _section_change_page_pair(change),
            _visible_prose_source_pages(
                prose_visual_lookup.get(_section_change_visual_identity(change)),
                prose_source_aliases.get(f"change-{index}"),
            ),
        )
        for index, change in indexed_technical
    ]
    table_card_entries = [
        (
            _table_change_page_sort_key(change),
            0,
            index,
            _render_table_change_html(
                index,
                change,
                source_page_aliases=table_source_aliases.get(f"table-{index}"),
                render_source_visual=False,
            ),
            _table_change_page_pair(change),
            _visible_table_source_pages(
                change,
                table_source_aliases.get(f"table-{index}"),
            ),
        )
        for index, change in enumerate(table_changes, start=1)
    ]
    page_ordered_entries = sorted(
        [*table_card_entries, *technical_card_entries],
        key=lambda item: (item[0][0], item[1], item[0][1], item[0][2], item[2]),
    )
    page_ordered_cards = _render_page_evidence_groups(
        page_ordered_entries,
        page_source_candidates=page_source_candidates,
        force_shared_source=True,
    )
    if not page_ordered_cards:
        empty_message = (
            _empty_report_message(result)
            if not table_changes and not result.visual_review_items
            else "未列出技术正文或表格变化；是否可确认一致请以顶部识别可信度为准。"
        )
        page_ordered_cards = f'<section class="empty-state">{_escape(empty_message)}</section>'
    page_ordered_html = f'''
      <section class="table-visuals page-ordered-evidence" id="page-ordered-evidence">
        <span class="section-anchor" id="table-changes"></span>
        <span class="section-anchor" id="text-changes"></span>
        <h2>按 PDF 页数顺序的差异证据</h2>
        <p class="change-summary">表格补充证据（变化与复核）和技术正文变化与复核按旧版/新版起始页排序；同一物理页的左右原页截图只展示一次，下面集中列出该页全部表格与文字差异。</p>
        {page_ordered_cards}
      </section>
    '''
    figure_visual_html = _render_figure_source_visual_groups(figure_source_visuals)
    appendix_cards = "".join(
        _render_change_html(
            f"appendix-{i}",
            c,
            prose_source_visual=prose_visual_lookup.get(_section_change_visual_identity(c)),
            source_visual_aliases=prose_source_aliases.get(f"appendix-{i}"),
        )
        for i, c in indexed_similarity_review
    ) + "".join(
        _render_table_change_html(
            f"appendix-{i}",
            c,
            source_page_aliases=table_source_aliases.get(f"table-appendix-{i}"),
        )
        for i, c in enumerate(similarity_review_tables, 1)
    )
    appendix_html = (
        '<details class="similarity-review-appendix" id="similarity-review-appendix">'
        '<summary>内容与配对相似度均为 1.000：按偏好不计入差异，可展开审查</summary>'
        '<p>这些条目未计入上方汇总；只有完整正文内容相同且没有数值、技术标识或语义运算符变化时才会折叠，原始比较事实保留如下。</p>'
        + appendix_cards + '</details>' if appendix_cards else ""
    )
    if appendix_cards and not technical_changes and not table_changes:
        page_ordered_html = page_ordered_html.replace(
            page_ordered_cards,
            '<section class="empty-state">主差异清单为空；内容与配对相似度均为 1.000 的配对证据收在末尾附录。</section>',
        )
    visual_review_html = _render_visual_review_items_html(result.visual_review_items)
    material_table_changes = _material_table_changes(table_changes)
    table_row_change_count = sum(
        len(_material_table_row_changes(change))
        for change in table_changes
    )
    table_review_count = sum(
        len(_table_review_rows(change))
        for change in table_changes
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #18202a;
      --muted: #647184;
      --line: #d8dee8;
      --add: #16794c;
      --add-bg: #e7f6ee;
      --del: #b3261e;
      --del-bg: #fdebea;
      --mod: #8a5a00;
      --mod-bg: #fff4d8;
      --blue: #255c99;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.55;
    }}
    header {{
      padding: 24px 32px;
      color: #fff;
      background: #1f3757;
    }}
    h1, h2, h3 {{ margin: 0; }}
    header p {{ margin: 8px 0 0; color: #dbe6f5; }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
      min-height: calc(100vh - 112px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: #fff;
      padding: 18px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }}
    main {{
      min-width: 0;
      width: 100%;
      max-width: none;
      padding: 22px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric strong {{ display: block; font-size: 28px; line-height: 1.1; }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .meta, .change-card, .empty-state {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      padding: 16px;
    }}
    .assessment-banner {{
      margin: 18px 22px 0;
      padding: 16px 18px;
      border: 2px solid var(--line);
      border-left-width: 8px;
      border-radius: 8px;
      background: var(--panel);
    }}
    .visual-coverage-details {{ margin: 18px 22px; padding: 16px 20px; border: 1px solid var(--border); border-radius: 8px; background: #fff; overflow-x: auto; }}
    .visual-coverage-details summary {{ font-weight: 650; cursor: pointer; }}
    .visual-coverage-details table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .visual-coverage-details th, .visual-coverage-details td {{ padding: 10px 12px; text-align: left; vertical-align: top; border-bottom: 1px solid #dce2e9; }}
    .visual-coverage-details th {{ background: #f3f6fa; }}
    .visual-coverage-details a {{ color: var(--blue); }}
    .assessment-banner h2 {{ font-size: 20px; }}
    .assessment-banner p {{ margin: 6px 0 0; }}
    .assessment-banner ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .assessment-reliable {{ border-left-color: var(--add); }}
    .assessment-degraded {{ border-left-color: var(--mod); }}
    .assessment-indeterminate {{ border-left-color: var(--del); }}
    .meta dl {{ display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 6px 12px; margin: 0; }}
    .meta dt {{ color: var(--muted); }}
    .meta dd {{ margin: 0; overflow-wrap: anywhere; }}
    .nav-title {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    .nav-section-gap {{ margin-top: 18px; }}
    .nav-item {{
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      color: var(--text);
      text-decoration: none;
      padding: 9px 8px;
      border-radius: 6px;
      margin-bottom: 4px;
      border-left: 4px solid transparent;
    }}
    .nav-item:hover {{ background: #eef3f8; }}
    .nav-item span {{
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}
    .nav-body {{ min-width: 0; }}
    .nav-label {{
      display: inline-block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 2px;
    }}
    .nav-location {{ overflow-wrap: anywhere; }}
    .nav-added {{ border-left-color: var(--add); }}
    .nav-deleted {{ border-left-color: var(--del); }}
    .nav-modified {{ border-left-color: var(--mod); }}
    .nav-unchanged {{ border-left-color: var(--blue); }}
    .nav-formula {{ border-left-color: var(--blue); }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 13px;
      margin-right: 8px;
      border: 1px solid transparent;
    }}
    .badge-added {{ color: var(--add); background: var(--add-bg); border-color: #b8e5ce; }}
    .badge-deleted {{ color: var(--del); background: var(--del-bg); border-color: #f5c4c0; }}
    .badge-modified {{ color: var(--mod); background: var(--mod-bg); border-color: #f1d489; }}
    .badge-review {{ color: var(--mod); background: var(--mod-bg); border-color: #f1d489; }}
    .badge-unchanged {{ color: var(--blue); background: #e7f0fb; border-color: #c5d8f0; }}
    .change-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 12px;
    }}
    .change-title {{ font-size: 18px; overflow-wrap: anywhere; }}
    .pages {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .compare-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin-top: 12px;
    }}
    .prose-source-visual {{ margin-top: 12px; }}
    .prose-source-visual-legend {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .prose-source-visual-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
    }}
    .figure-source-visual {{ margin-top: 14px; }}
    .figure-source-section {{
      margin-top: 18px;
      padding: 0 0 4px;
    }}
    .figure-source-visual > h4 {{ margin: 0 0 8px; color: var(--blue); font-size: 15px; }}
    .figure-source-visual-note {{ margin: 0 0 8px; color: var(--muted); font-size: 13px; }}
    .figure-source-visual-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
    }}
    .prose-source-side {{
      min-width: 0;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .prose-source-side > h4 {{
      margin: 0;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 13px;
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
    }}
    .prose-source-page {{ margin: 0; border-bottom: 1px solid var(--line); }}
    .prose-source-page:last-child {{ border-bottom: 0; }}
    .prose-source-page figcaption {{
      padding: 6px 10px;
      color: var(--muted);
      font-size: 12px;
      background: #f8fafc;
      border-bottom: 1px solid var(--line);
    }}
    .prose-source-alias-anchor {{ display: none; }}
    .prose-source-page img {{ display: block; width: auto; max-width: 100%; height: auto; background: #fff; }}
    .section-anchor {{ display: block; height: 0; overflow: hidden; }}
    .prose-source-empty {{ padding: 24px 12px; color: var(--muted); text-align: center; }}
    .prose-source-omitted {{
      padding: 8px 10px;
      color: var(--mod);
      font-size: 12px;
      background: var(--mod-bg);
      border-top: 1px solid #f1d489;
    }}
    .prose-text-details {{
      margin-top: 10px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }}
    .prose-text-details > summary {{
      cursor: pointer;
      padding: 9px 11px;
      color: var(--blue);
      font-weight: 600;
    }}
    .prose-text-details[open] > summary {{ border-bottom: 1px solid var(--line); }}
    .prose-text-details-body {{ padding: 10px; }}
    .pane {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fbfcfe;
    }}
    .pane h4 {{
      margin: 0;
      padding: 8px 10px;
      font-size: 13px;
      color: var(--muted);
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
    }}
    .snippet {{ padding: 10px; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .snippet-detail {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #f7f9fc;
      overflow: hidden;
    }}
    .snippet-detail summary {{
      cursor: pointer;
      color: var(--muted);
      padding: 9px 11px;
      font-weight: 600;
    }}
    .snippet-detail[open] summary {{ border-bottom: 1px solid var(--line); }}
    .snippet-detail-body {{ padding: 10px; overflow-wrap: anywhere; }}
    .snippet-detail-layout summary {{ color: var(--mod); }}
    .snippet-visible-tail {{
      margin-top: 8px;
      padding: 8px 10px;
      border-left: 3px solid var(--blue);
      background: #f4f8fd;
      color: var(--ink);
    }}
    mark {{ border-radius: 3px; padding: 0 2px; }}
    .ins {{ color: var(--add); background: var(--add-bg); }}
    .del {{ color: var(--del); background: var(--del-bg); text-decoration: line-through; }}
    {FOCUS_CSS}
    .change-summary {{
      color: var(--muted);
      background: #f7f9fc;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      margin: 10px 0 12px;
    }}
    .match-basis {{
      color: var(--blue);
      background: #eef5fd;
      border: 1px solid #c9dcf3;
      border-radius: 8px;
      padding: 8px 10px;
      margin: 8px 0 12px;
      font-size: 13px;
    }}
    .single-list {{ margin: 10px 0 0 0; padding-left: 18px; }}
    .single-list li {{ margin: 6px 0; }}
    .omitted-note {{
      color: var(--muted);
      background: #f2f5f9;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 10px;
      margin-top: 12px;
    }}
    .table-visuals {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      padding: 16px;
    }}
    .page-evidence-group {{
      border: 1px solid var(--line);
      border-radius: 10px;
      margin: 16px 0;
      padding: 12px;
      background: #ffffff;
    }}
    .page-evidence-title {{
      margin: 0;
      padding: 4px 2px 8px;
      color: var(--ink);
      font-size: 19px;
    }}
    .page-evidence-note {{
      color: var(--muted);
      background: #f7f9fc;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      margin: 0 0 10px;
      font-size: 13px;
    }}
    .page-evidence-source-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin: 10px 0 14px;
    }}
    .page-source-side {{
      min-width: 0;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .page-source-side > h4 {{
      margin: 0;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 13px;
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
    }}
    .page-source-figure {{ margin: 0; border-bottom: 1px solid var(--line); }}
    .page-source-figure:last-child {{ border-bottom: 0; }}
    .page-source-figure figcaption {{
      padding: 6px 10px;
      color: var(--muted);
      font-size: 12px;
      background: #f8fafc;
      border-bottom: 1px solid var(--line);
    }}
    .page-source-figure img {{ display: block; width: auto; max-width: 100%; height: auto; cursor: zoom-in; }}
    .page-source-empty {{ padding: 24px 12px; color: var(--muted); text-align: center; }}
    .table-visual-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
      background: #fbfcfe;
    }}
    .table-shot-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin-top: 10px;
    }}
    .table-shot {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      background: #fff;
    }}
    .table-shot h4 {{
      margin: 0;
      padding: 8px 10px;
      color: var(--muted);
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }}
    .table-shot img {{
      display: block;
      width: auto;
      max-width: 100%;
      height: auto;
      background: #fff;
    }}
{VISUAL_REVIEW_IMAGE_CSS}
    .visual-mask-detail {{
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fbfcfe;
      overflow: hidden;
    }}
    .visual-mask-detail > summary {{
      cursor: pointer;
      padding: 14px 16px;
      color: var(--muted);
      font-weight: 700;
      user-select: none;
    }}
    .visual-mask-detail[open] > summary {{
      border-bottom: 1px solid var(--line);
    }}
    .visual-mask-detail > .change-summary {{
      margin: 12px 16px;
    }}
    .visual-mask-detail > .table-shot {{
      margin: 0 12px 12px;
    }}
    .table-shot-page {{
      border-bottom: 1px solid var(--line);
    }}
    .table-shot-page:last-child {{
      border-bottom: 0;
    }}
    .table-shot-page-label {{
      padding: 7px 10px;
      color: var(--muted);
      font-size: 12px;
      background: #f7f9fc;
      border-bottom: 1px solid var(--line);
    }}
    .table-status {{
      color: var(--muted);
      font-size: 12px;
      margin: 6px 0 12px;
    }}
    .table-row-summary {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 13px;
    }}
    .table-row-summary th, .table-row-summary td {{
      border: 1px solid var(--line);
      padding: 7px 8px;
      vertical-align: top;
      text-align: left;
    }}
    .table-row-summary th {{ background: #edf1f6; color: var(--muted); }}
    .table-row-summary td:nth-child(2) {{ background: #fffafa; }}
    .table-row-summary td:nth-child(3) {{ background: #fbfffb; }}
    .table-cell-detail summary {{ cursor: pointer; color: var(--mod); }}
    .table-cell-detail-body {{ margin-top: 6px; overflow-wrap: anywhere; white-space: normal; }}
    .table-kind {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 12px;
      white-space: nowrap;
      border: 1px solid var(--line);
    }}
    .table-kind-change {{ color: #b42318; background: #fff1f0; border-color: #ffccc7; }}
    .table-kind-same {{ color: #146c43; background: #eaf7ef; border-color: #b7e4c7; }}
    .table-kind-add {{ color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }}
    .table-kind-del {{ color: #7a4b00; background: #fff7db; border-color: #f5d889; }}
    .table-kind-review {{ color: #7a4b00; background: #fff7db; border-color: #f5d889; }}
    .section-heading {{ margin: 22px 0 12px; font-size: 21px; }}
    @media (max-width: 860px) {{
      .layout {{ grid-template-columns: minmax(0, 1fr); }}
      aside {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      main {{ padding: 14px; }}
      .summary, .compare-grid, .table-shot-grid {{ grid-template-columns: minmax(0, 1fr); }}
      .prose-source-visual-grid {{ grid-template-columns: minmax(0, 1fr); }}
      .figure-source-visual-grid {{ grid-template-columns: minmax(0, 1fr); }}
      .table-shot-grid, .table-shot {{ min-width: 0; max-width: 100%; }}
      .table-row-summary {{ table-layout: fixed; min-width: 0; }}
      .table-row-summary th, .table-row-summary td {{ overflow-wrap: anywhere; word-break: break-word; min-width: 0; }}
      .change-head {{ display: block; }}
      .pages {{ margin-top: 4px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>生成时间: {_escape(generated_at)} · 章节匹配阈值: {options.min_section_match_similarity:.2f}</p>
  </header>
  {assessment_html}
  <div class="layout">
    <aside>
      {nav_items}
    </aside>
    <main>
      <p class="reader-guide">正文差异候选按章节统计，不代表已确认技术含义发生变化。待核实项尚未分类，按片段对或整节计数，可能与候选章节重叠；请结合各项原因、识别可信度及核对范围阅读。</p>
      <section class="summary">
        <div class="metric"><strong>{material_technical_count}</strong><span>正文差异候选（章节）</span></div>
        <div class="metric"><strong>{technical_review_count}</strong><span>正文待核实项（未分类）</span></div>
        <div class="metric"><strong>0</strong><span>公式自动对比（已关闭）</span></div>
        <div class="metric"><strong>{len(result.visual_review_items)}</strong><span>视觉漏检核对</span></div>
        <div class="metric"><strong>{len(material_table_changes)}</strong><span>变化表格</span></div>
        <div class="metric"><strong>{table_row_change_count}</strong><span>表格行变化</span></div>
        <div class="metric"><strong>{sum(r.row_role == 'annotation' for c in table_changes for r in c.row_changes)}</strong><span>表说明变化</span></div>
        <div class="metric"><strong>{table_review_count}</strong><span>表格复核项</span></div>
      </section>
      <p class="reader-guide">先看左右原页截图，点击图片可放大；浅色标出能可靠定位的变化。正文文字明细默认折叠，表格文字明细默认展开。正文卡同时显示完整正文内容相似度和章节配对相似度；数值、技术标识或语义运算符变化不会因四舍五入为 1.000 而折叠，只有两种分数都精确为 1.000 且无关键内容变化的配对才收在报告末尾。</p>
      <section class="meta">
        <dl>
          <dt>旧协议</dt><dd>{_escape(str(result.old_pdf))}</dd>
          <dt>新协议</dt><dd>{_escape(str(result.new_pdf))}</dd>
          <dt>旧/新页数</dt><dd>{_source_page_count(result, "old")} / {_source_page_count(result, "new")}</dd>
          <dt>旧选择页</dt><dd>{_escape(_selected_page_label(result, "old"))}</dd>
          <dt>新选择页</dt><dd>{_escape(_selected_page_label(result, "new"))}</dd>
          <dt>比较方式</dt><dd>{_escape(comparison_note)}</dd>
          <dt>报告范围</dt><dd>{_escape(scope_note)}</dd>
          <dt>提示</dt><dd>页码来自 PDF 抽取顺序；最终结论请回到源 PDF 复核。</dd>
        </dl>
      </section>
      {page_ordered_html}
      {visual_review_html}
      {figure_visual_html}
      {appendix_html}
    </main>
  </div>
{context_source_images(materialized_source_visuals, ''.join(entry[3] for entry in page_ordered_entries) + appendix_cards)}
{FOCUS_SCRIPT}
{IMAGE_VIEWER}
</body>
</html>
"""


def _render_change_html(
    index: int,
    change: SectionChange,
    *,
    prose_source_visual: ProseSourceVisualGroup | None = None,
    source_visual_aliases: dict[tuple[str, int], str] | None = None,
    render_source_visual: bool = True,
) -> str:
    """Render one change as a side-by-side HTML block."""

    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    old_pages = change.old_section.page_range if change.old_section else "-"
    new_pages = change.new_section.page_range if change.new_section else "-"
    if change.old_section and change.new_section:
        content_similarity = _section_content_similarity(change)
        similarity = (
            f" · 内容相似度 {content_similarity:.6f}"
            f" · 配对相似度 {change.similarity:.6f}"
            if content_similarity is not None
            else f" · 配对相似度 {change.similarity:.6f}"
        )
    else:
        similarity = ""
    summary = _change_summary(change)
    summary_html = (
        f'<div class="change-summary">{_escape(summary)}</div>'
        if summary
        else ""
    )
    match_basis_html = ""
    if change.match_basis in _EXPLAINED_MATCH_BASES:
        match_basis_html = (
            '<div class="match-basis">配对依据：'
            f'{_escape(_MATCH_BASIS_LABELS[change.match_basis])}'
            f'（{_escape(_match_basis_explanation(change.match_basis))}）</div>'
        )
    pairs = "\n".join(_render_pair_html(pair.old, pair.new) for pair in change.replaced_snippets)
    review_pairs = _render_review_pairs_html(
        change.review_replaced_snippets,
        context_scope=bool(change.context_review_records),
        figure_scope=_FIGURE_FRAGMENT_REVIEW_REASON in change.review_reason,
    )
    if change.formula_review_records:
        review_pairs += '<p class="review-note">公式来源待核实（' + str(len(change.formula_review_records)) + ' 项）；完整原文、精确来源位置与双侧原图见公式来源待核实记录，不表示公式相同。</p>'
    neutral = change.change_type == "review"
    if neutral:
        pairs = "\n".join(_render_pair_html(pair.old, pair.new, neutral=True) for pair in change.replaced_snippets)
    added = _render_single_list("新版待核实原文" if neutral else "新增片段", change.added_snippets, "span" if neutral else "ins")
    removed = _render_single_list("旧版待核实原文" if neutral else "删除片段", change.removed_snippets, "span" if neutral else "del")
    if change.review_reason:
        match_basis_html += f'<div class="match-basis">{_escape(change.review_reason)}</div>'
    omitted = _render_omitted_html(change.omitted_snippet_count)
    text_body = (pairs or "") + review_pairs + added + removed + omitted
    if (change.context_review_records or change.formula_review_records) and prose_source_visual is not None:
        # The original segmentation highlights are no longer factual deltas.
        # Show actual unannotated pixels; never relabel a colored image as raw.
        def context_raw(visuals):
            return tuple(replace(v, image_data_uri=v.raw_image_data_uri,
                                 highlight_region_count=0) for v in visuals if v.raw_image_data_uri)
        prose_source_visual = replace(prose_source_visual, change_type="review",
                                      old_visuals=context_raw(prose_source_visual.old_visuals),
                                      new_visuals=context_raw(prose_source_visual.new_visuals))
    source_prefix = f"change-{index}-source"
    source_visual_html = ""
    if prose_source_visual is not None:
        if render_source_visual:
            source_visual_html = _render_prose_source_visual_group(
                prose_source_visual,
                prefix=source_prefix,
                source_visual_aliases=source_visual_aliases,
            )
        else:
            # Main page evidence owns the visible old/new page screenshots.
            # Keep per-change source ids as invisible aliases so focus buttons
            # still resolve to that shared page image instead of losing the
            # coordinate-backed navigation target.
            source_visual_html = _render_prose_page_source_aliases(
                prose_source_visual,
                prefix=source_prefix,
            )
    focus = render_change_focus(change, prose_source_visual, f"change-{index}-source", _inline_tokens, _inline_diff_html, _unverified_pua_mapping_note, _focus_context_html, _focus_allows_deltas, _escape)
    text_detail = (
        '<details class="prose-text-details"><summary>展开文字识别明细</summary>'
        + (focus or text_body) + '</details>' if text_body or focus else ""
    )
    context_proof_html = (
        '<details><summary>查看上下文来源与原始分段审计（归属待核实）</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
        + _escape(json.dumps(change.context_review_records, ensure_ascii=False, indent=2)) + '</pre></details>'
        if change.context_review_records else ''
    )
    body = (source_visual_html or '<p class="prose-source-empty">原页截图暂不可用；可展开文字明细核对。</p>') + text_detail + context_proof_html
    if not body:
        body = f'<p class="snippet">{_escape(_empty_change_message(change))}</p>'
    return f"""
      <section class="change-card" id="change-{index}">
        <div class="change-head">
          <h3 class="change-title"><span class="badge badge-{change.change_type}">{_escape(label)}</span> {_escape(_display_change_location(change))}</h3>
          <div class="pages">旧定位页 {_escape(old_pages)} · 新定位页 {_escape(new_pages)}{_escape(similarity)}</div>
        </div>
        {match_basis_html}
        {summary_html}
        {body}
      </section>
    """


def _section_change_visual_identity(
    change: SectionChange,
) -> tuple[str, str | None, str | None]:
    return (
        change.change_type,
        change.old_section.section_id if change.old_section else None,
        change.new_section.section_id if change.new_section else None,
    )


def _prose_source_visual_identity(
    group: ProseSourceVisualGroup,
) -> tuple[str, str | None, str | None]:
    return (group.change_type, group.old_section_id, group.new_section_id)


def _build_prose_source_aliases(
    card_groups: Iterable[tuple[str, ProseSourceVisualGroup | None]],
) -> dict[str, dict[tuple[str, int], str]]:
    """Record canonical peers for repeated prose screenshots.

    The source builder intentionally gives each section every page it owns so
    context is complete.  The earliest occurrence is the visible canonical
    screenshot; later occurrences keep hidden aliases for source-focus
    resolution and do not add another visible page image.
    """

    occurrences: dict[tuple[str, int], list[tuple[str, int, int, int]]] = {}
    for order, (card_key, group) in enumerate(card_groups):
        if group is None:
            continue
        for side, visuals in (("old", group.old_visuals), ("new", group.new_visuals)):
            for visual_index, visual in enumerate(visuals):
                occurrences.setdefault((side, visual.page_number), []).append(
                    (card_key, visual_index, visual.highlight_region_count, order)
                )

    aliases: dict[str, dict[tuple[str, int], str]] = {}
    for (side, _page_number), items in occurrences.items():
        if len(items) < 2:
            continue
        canonical = min(items, key=lambda item: (item[3], item[1]))
        source_prefix = (
            f"change-{canonical[0]}-source"
            if canonical[0].startswith("appendix-")
            else f"{canonical[0]}-source"
        )
        canonical_id = f"{source_prefix}-{side}-{canonical[1]}"
        for card_key, visual_index, highlight_count, _order in items:
            if (card_key, visual_index) != (canonical[0], canonical[1]):
                aliases.setdefault(card_key, {})[(side, visual_index)] = canonical_id
    return aliases


def _prose_source_id(card_key: str, side: str, visual_index: int) -> str:
    """Return the DOM id used by a prose source occurrence."""

    prefix = (
        f"change-{card_key}-source"
        if card_key.startswith("appendix-")
        else f"{card_key}-source"
    )
    return f"{prefix}-{side}-{visual_index}"


def _table_source_id(card_key: str, side: str, visual_index: int) -> str:
    """Return the DOM id used by a table source occurrence."""

    table_key = card_key[6:] if card_key.startswith("table-") else card_key
    return f"table-change-{table_key}-source-{side}-{visual_index}"


def _build_page_source_aliases(
    table_card_groups: Iterable[tuple[str, TableChange]],
    prose_card_groups: Iterable[tuple[str, ProseSourceVisualGroup | None]],
) -> dict[str, dict[str, dict[tuple[str, int], str]]]:
    """Record source peers by side and PDF page across card types.

    A logical table and a prose section often own different crops of the same
    source page. The page is the reader-facing evidence unit, so retain one
    canonical occurrence for the visible image and point later occurrences at
    it with hidden aliases.
    """

    # kind, card key, side, visual index, highlight count, card order, DOM id
    occurrences: dict[tuple[str, int], list[tuple[str, str, str, int, int, int, str]]] = {}
    for order, (card_key, change) in enumerate(table_card_groups):
        for side, tables in (("old", change.old_tables), ("new", change.new_tables)):
            for visual_index, table in enumerate(tables):
                # A text-only fallback has no screenshot to deduplicate.
                if not (table.image_data_uri or table.context_image_data_uri):
                    continue
                occurrence = (
                    "table",
                    card_key,
                    side,
                    visual_index,
                    0,
                    order,
                    _table_source_id(card_key, side, visual_index),
                )
                occurrences.setdefault((side, table.page_number), []).append(occurrence)

    for order, (card_key, group) in enumerate(prose_card_groups):
        if group is None:
            continue
        for side, visuals in (("old", group.old_visuals), ("new", group.new_visuals)):
            for visual_index, visual in enumerate(visuals):
                if not (visual.image_data_uri or visual.raw_image_data_uri):
                    continue
                occurrence = (
                    "prose",
                    card_key,
                    side,
                    visual_index,
                    visual.highlight_region_count,
                    order,
                    _prose_source_id(card_key, side, visual_index),
                )
                occurrences.setdefault((side, visual.page_number), []).append(occurrence)

    aliases: dict[str, dict[str, dict[tuple[str, int], str]]] = {
        "table": {},
        "prose": {},
    }
    for items in occurrences.values():
        if len(items) < 2:
            continue
        table_items = [item for item in items if item[0] == "table"]
        if table_items:
            canonical = min(table_items, key=lambda item: (item[5], item[3]))
        else:
            canonical = min(items, key=lambda item: (item[5], item[3]))
        canonical_id = canonical[6]
        for kind, card_key, side, visual_index, _highlight_count, _order, source_id in items:
            if source_id == canonical_id:
                continue
            aliases[kind].setdefault(card_key, {})[(side, visual_index)] = canonical_id

    return aliases


def _render_prose_source_visual_group(
    group: ProseSourceVisualGroup,
    *,
    prefix: str = "",
    source_visual_aliases: dict[tuple[str, int], str] | None = None,
) -> str:
    if not group.old_visuals and not group.new_visuals:
        return ""
    old_aliases = {
        index: target
        for (side, index), target in (source_visual_aliases or {}).items()
        if side == "old"
    }
    new_aliases = {
        index: target
        for (side, index), target in (source_visual_aliases or {}).items()
        if side == "new"
    }
    old_fully_reused = bool(group.old_visuals) and len(old_aliases) == len(group.old_visuals)
    new_fully_reused = bool(group.new_visuals) and len(new_aliases) == len(group.new_visuals)
    if (old_fully_reused or not group.old_visuals) and (new_fully_reused or not group.new_visuals):
        # Keep source-focus targets resolvable without adding another visible
        # screenshot or a misleading jump/omission message to this card.
        alias_anchors = []
        for side, visuals, aliases in (
            ("old", group.old_visuals, old_aliases),
            ("new", group.new_visuals, new_aliases),
        ):
            for index, _visual in enumerate(visuals):
                target = aliases.get(index)
                if target:
                    source_id = f"{prefix}-{side}-{index}" if prefix else ""
                    if source_id:
                        alias_anchors.append(
                            f'<span class="prose-source-alias-anchor" id="{_escape(source_id)}" '
                            f'data-source-alias="{_escape(target)}"></span>'
                        )
        return "".join(alias_anchors)
    old_side = _render_prose_source_visual_side(
        "旧版原文区域",
        group.old_visuals,
        "旧版无对应原文区域",
        omitted_page_count=group.old_omitted_page_count,
        display_mode="raw" if group.change_type == "review" else "old-highlight",
        source_prefix=prefix + "-old" if prefix else "",
        source_aliases=old_aliases,
    )
    new_side = _render_prose_source_visual_side(
        "新版原文区域",
        group.new_visuals,
        "新版无对应原文区域",
        omitted_page_count=group.new_omitted_page_count,
        display_mode="raw" if group.change_type == "review" else "new-highlight",
        source_prefix=prefix + "-new" if prefix else "",
        source_aliases=new_aliases,
    )
    legend = ("原文出处：对应关系尚待核实，不作新增或删除标色。"
              if group.change_type == "review" else
              "原文坐标浅色标注（逐词）：旧版淡红、新版淡绿；表格、Figure 与页边行号不进入正文标色。")
    return (
        '<div class="prose-source-visual">'
        f'<div class="prose-source-visual-legend">{legend}</div>'
        f'<div class="prose-source-visual-grid">{old_side}{new_side}</div>'
        '</div>'
    )


def _render_prose_page_source_aliases(
    group: ProseSourceVisualGroup,
    *,
    prefix: str,
) -> str:
    """Keep focus targets while the page group owns visible source images."""

    anchors: list[str] = []
    for side, visuals in (("old", group.old_visuals), ("new", group.new_visuals)):
        for index, visual in enumerate(visuals):
            if not (visual.image_data_uri or visual.raw_image_data_uri):
                continue
            source_id = f"{prefix}-{side}-{index}"
            target_id = f"page-source-{side}-{visual.page_number}"
            anchors.append(
                f'<span class="prose-source-alias-anchor" id="{_escape(source_id)}" '
                f'data-source-alias="{_escape(target_id)}"></span>'
            )
    return "".join(anchors)


def _render_figure_source_visual_groups(
    groups: Iterable[ProseSourceVisualGroup],
) -> str:
    cards = "".join(_render_figure_source_visual_group(group) for group in groups)
    if not cards:
        return ""
    return (
        '<details class="figure-source-section focus-more" id="figure-source-evidence">'
        '<summary>补充 Figure 原图证据（未自动判定图内变化，可展开核对）</summary>'
        '<h2 class="section-heading">Figure 原图核对</h2>'
        '<p class="figure-source-visual-note">Figure 按图题与文档顺序配对；只并排呈现原图，不自动标色或解析图内标签。</p>'
        f'{cards}</details>'
    )


def _render_figure_source_visual_group(group: ProseSourceVisualGroup) -> str:
    """Render Figure evidence raw and explicitly outside automatic text comparison."""

    if not group.old_figure_visuals and not group.new_figure_visuals:
        return ""
    old_side = _render_prose_source_visual_side(
        "旧版 Figure 原图",
        group.old_figure_visuals,
        "旧版无对应 Figure 原图",
        display_mode="raw",
    )
    new_side = _render_prose_source_visual_side(
        "新版 Figure 原图",
        group.new_figure_visuals,
        "新版无对应 Figure 原图",
        display_mode="raw",
    )
    old_caption = group.old_figure_captions[0] if group.old_figure_captions else "旧版 Figure"
    new_caption = group.new_figure_captions[0] if group.new_figure_captions else "新版 Figure"
    pair_title = f"{old_caption} ↔ {new_caption}"
    pairing_note = (
        f"图题顺序配对 · 相似度 {group.figure_similarity:.3f}"
        if group.figure_similarity is not None
        else "仅单侧识别到 Figure，需回源 PDF 核对"
    )
    return (
        '<div class="figure-source-visual">'
        f'<h4>{_escape(pair_title)}</h4>'
        f'<p class="figure-source-visual-note">{_escape(pairing_note)}；图内文字请直接目视核对。</p>'
        f'<div class="figure-source-visual-grid">{old_side}{new_side}</div>'
        '</div>'
    )


def _render_prose_source_visual_side(
    title: str,
    visuals: Iterable[ProseSourceVisual],
    empty_message: str,
    *,
    omitted_page_count: int = 0,
    display_mode: str = "raw",
    source_prefix: str = "",
    source_aliases: dict[int, str] | None = None,
) -> str:
    materialized = tuple(visuals)
    if materialized:
        mode_label = {
            "old-highlight": "淡红差异标注",
            "new-highlight": "淡绿差异标注",
            "raw": "原始裁剪，无标色",
        }.get(display_mode, "原始裁剪")
        pages_parts: list[str] = []
        for visual_index, visual in enumerate(materialized):
            figure_id = (
                f' id="{_escape(source_prefix)}-{visual_index}"'
                if source_prefix
                else ""
            )
            alias_target = (source_aliases or {}).get(visual_index)
            if alias_target:
                pages_parts.append(
                    '<span class="prose-source-alias-anchor"'
                    + figure_id
                    + f' data-source-alias="{_escape(alias_target)}"></span>'
                )
                continue
            pages_parts.append(
                '<figure class="prose-source-page"'
                + figure_id
                + f' data-source-view="{_escape(json.dumps(visual.source_view_box))}">'
                f'<figcaption>PDF 第 {_escape(str(visual.page_number))} 页 · '
                f'{_escape(mode_label if visual.highlight_region_count else "原页上下文，未标色")} · 点击放大</figcaption>'
                f'<img src="{visual.image_data_uri or visual.raw_image_data_uri}" alt="{_escape(title)} PDF 第 '
                f'{_escape(str(visual.page_number))} 页原始裁剪截图">'
                '</figure>'
            )
        pages = "".join(pages_parts)
    else:
        pages = f'<div class="prose-source-empty">{_escape(empty_message)}</div>'
    if omitted_page_count:
        pages += (
            '<div class="prose-source-omitted">另有 '
            f'{omitted_page_count} 个变化页未嵌入；请以上方结构化文字为主并回到源 PDF 核对。</div>'
        )
    if materialized and not any("<figure" in part for part in pages_parts):
        # All occurrences in this card are aliases of the first visible page
        # evidence. Keep only invisible focus anchors; the shared page group
        # carries the one source screenshot and all cards below carry text.
        return pages
    return (
        '<section class="prose-source-side">'
        f'<h4>{_escape(title)}</h4>{pages}</section>'
    )


def _render_table_changes_html(table_changes: list[TableChange]) -> str:
    """Render only changed screenshot-backed tables from the shared fact model."""

    if not table_changes:
        return ""
    cards = "\n".join(
        _render_table_change_html(index, change)
        for index, change in enumerate(table_changes, start=1)
    )
    return f"""
      <section class="table-visuals" id="table-changes">
        <h2>表格补充证据（变化与复核）</h2>
        <p class="change-summary">展示检测到的行级变化、表题/表号变化、单侧新增/删除，以及结构或字体编码尚未验证的表格复核项；旧/新表题、页码、配对分数和完整事实同时写入 JSON 与 CSV。</p>
        {cards}
      </section>
    """


def _render_visual_review_items_html(items: list[VisualReviewItem]) -> str:
    """Render pixel evidence separately from semantic text/table/formula facts."""

    if not items:
        return ""
    cards = "\n".join(
        _render_visual_review_item_html(index, item)
        for index, item in enumerate(items, start=1)
    )
    return f"""
      <section class="table-visuals" id="visual-review-items">
        <h2>视觉漏检核对</h2>
        <p class="change-summary">已核对的整页或固定图示区域存在未解释的像素变化。各项说明列出核对范围；这些是人工复核证据，不自动解释为正文、表格或公式修改。</p>
        {cards}
      </section>
    """


def _render_visual_review_item_html(index: int, item: VisualReviewItem) -> str:
    """Render old/new source pages and the material-difference mask."""

    old_page = item.old_page_number if item.old_page_number is not None else "-"
    new_page = item.new_page_number if item.new_page_number is not None else "-"
    old_image = (
        f'<img src="{item.old_image_data_uri}" alt="旧版第 {old_page} 页视觉证据">'
        if item.old_image_data_uri
        else '<p class="change-summary">旧版无对应页面。</p>'
    )
    new_image = (
        f'<img src="{item.new_image_data_uri}" alt="新版第 {new_page} 页视觉证据">'
        if item.new_image_data_uri
        else '<p class="change-summary">新版无对应页面。</p>'
    )
    diff_image = (
        f'<img src="{item.diff_image_data_uri}" alt="V{index} 差异掩膜">'
        if item.diff_image_data_uri
        else '<p class="change-summary">没有可渲染的差异掩膜。</p>'
    )
    visual_mask_disclosure = render_visual_mask_disclosure(diff_image)
    buttons = []
    for region_index, box in enumerate(item.focus_regions, 1):
        targets = {
            "old": {"id": f"visual-{index}-old", "box": box, "page": old_page} if item.old_image_data_uri else None,
            "new": {"id": f"visual-{index}-new", "box": box, "page": new_page} if item.new_image_data_uri else None,
        }
        buttons.append(source_button(targets, f"核对区域 {region_index}"))
    focus_actions = "".join(buttons[:8])
    if len(buttons) > 8:
        focus_actions += f'<details class="focus-more"><summary>其余 {len(buttons)-8} 个定位区域（全部保留）</summary>{"".join(buttons[8:])}</details>'
    focus_note = (
        f"检测到 {len(buttons)} 个像素变化区域。点击查看双方对应位置；定位框内也可能包含未变化内容，不代表技术要求已改变。"
        if buttons else "本项未保留可靠的局部定位坐标；请结合完整原文及折叠的差异掩膜核对，不能据此判断具体技术变化。"
    )
    view = (0, 0, *item.preview_size) if item.preview_size else None
    view_attr = _escape(json.dumps(view))
    return f"""
        <article class="table-visual-card" id="visual-review-{index}">
          <h3>V{index}. 旧页 {old_page} / 新页 {new_page}</h3>
          <p class="focus-help">{focus_note}</p>
          <div class="visual-focus-actions">{focus_actions}</div>
          <div class="focus-preview" aria-live="polite"></div>
          <p class="change-summary">{_escape(item.reason)} 像素相似度 {item.pixel_similarity:.4f}，变化比例 {item.changed_pixel_ratio:.4%}，配对依据 {_escape(item.alignment_method)}。</p>
          <div class="table-shot-grid">
            <div class="table-shot visual-review-shot" id="visual-{index}-old" data-source-view="{view_attr}"><h4>旧协议 · 第 {old_page} 页</h4>{old_image}</div>
            <div class="table-shot visual-review-shot" id="visual-{index}-new" data-source-view="{view_attr}"><h4>新协议 · 第 {new_page} 页</h4>{new_image}</div>
          </div>
{visual_mask_disclosure}
        </article>
    """


def _material_table_row_changes(change: TableChange) -> tuple[TableRowChange, ...]:
    """Return confirmed table deltas separately from uncertainty findings."""

    return tuple(
        row
        for row in change.row_changes
        if row.change_type != "需人工复核" and row.row_role != "annotation"
    )


def _material_table_changes(changes: list[TableChange]) -> list[TableChange]:
    """Exclude review-only pairs from the reader's confirmed table-change count."""

    return [change for change in changes if change.change_type != "review"]


def _table_visual_identity(table: TableVisual) -> str:
    """Return a compact identity string for pairing table screenshots."""

    title = compact_inline(table.title).casefold()  # 表题通常是最强身份信号。
    row_identities = sorted(
        _table_row_pairing_key(row)
        for row in table.row_texts
        if compact_inline(row)
    )
    rows = " ".join(row_identities)  # 全部行身份都必须参与；第七行以后也可能是唯一判别证据。
    return compact_inline(f"{title} {rows}").casefold()


def _render_table_change_html(
    index: int,
    change: TableChange,
    *,
    source_page_aliases: dict[tuple[str, int], str] | None = None,
    render_source_visual: bool = True,
) -> str:
    """Render one changed logical table with auditable pairing metadata."""

    title = _table_change_title(change)
    source_prefix = f"table-change-{index}-source"
    if render_source_visual:
        old_shot = _render_table_shot_group(
            "旧版截图",
            change.old_tables,
            change=change,
            side="old",
            source_prefix=source_prefix,
            source_page_aliases=source_page_aliases,
        )
        new_shot = _render_table_shot_group(
            "新版截图",
            change.new_tables,
            change=change,
            side="new",
            source_prefix=source_prefix,
            source_page_aliases=source_page_aliases,
        )
        source_shots = f'<div class="table-shot-grid">{old_shot}{new_shot}</div>'
    else:
        # The page evidence group owns one visible original screenshot per
        # physical page. Keep stable hidden anchors for any downstream focus
        # target, while leaving the table's complete row facts in this card.
        source_aliases: list[str] = []
        source_availability: list[str] = []
        for side, tables in (("old", change.old_tables), ("new", change.new_tables)):
            for visual_index, table in enumerate(tables):
                if not (table.image_data_uri or table.context_image_data_uri):
                    continue
                source_id = f"{source_prefix}-{side}-{visual_index}"
                target_id = f"page-source-{side}-{table.page_number}"
                source_aliases.append(
                    f'<span class="prose-source-alias-anchor table-source-alias-anchor" '
                    f'id="{_escape(source_id)}" data-source-alias="{_escape(target_id)}"></span>'
                )
            if not tables:
                # A one-sided table still needs an explicit audit fact. The
                # page group carries the available original-page screenshot;
                # this card records why the opposite table side has no crop.
                source_availability.append(
                    f'<div class="table-source-empty">'
                    f'{_escape("旧版" if side == "old" else "新版")}无对应表格截图'
                    "（对应页没有该表格）</div>"
                )
        source_shots = "".join(source_aliases) + "".join(source_availability)
    rows_html = _render_table_row_summary(change)
    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    similarity = ""
    if change.old_tables and change.new_tables:
        pairing = _table_pairing_similarity(change)
        similarity = f" · 内容相似度 {change.similarity:.6f}"
        if pairing is not None:
            similarity += f" · 配对相似度 {pairing:.6f}"
    return f"""
        <div class="table-visual-card" id="table-change-{index}">
          <h3><span class="badge badge-{change.change_type}">{_escape(label)}</span> {_escape(title)}</h3>
          <div class="table-status">旧表：{_escape(_table_side_description(change.old_tables))}<br>
          新表：{_escape(_table_side_description(change.new_tables))}{_escape(similarity)}</div>
          {source_shots}
          <details open class="table-text-details"><summary>表格文字明细</summary>{rows_html}</details>
        </div>
    """


def _table_side_description(tables: tuple[TableVisual, ...]) -> str:
    """Return captions and source pages for one side of a table change."""

    if not tables:
        return "无对应表格"
    titles = _unique_table_titles(tables)
    title_text = " / ".join(titles) if titles else "无表题续段"
    pages = ", ".join(str(table.page_number) for table in tables)
    return f"{title_text}（页 {pages}）"


def _render_table_shot_group(
    label: str,
    tables: tuple[TableVisual, ...],
    *,
    change: TableChange | None = None,
    side: str = "old",
    source_prefix: str = "",
    source_page_aliases: dict[tuple[str, int], str] | None = None,
) -> str:
    """Render one side of a table screenshot group."""

    if not tables:
        return f'<div class="table-shot"><h4>{_escape(label)}</h4><div class="snippet">无对应表格截图</div></div>'
    heading = f"{label} · {len(tables)} 页" if len(tables) > 1 else f"{label} · 页 {tables[0].page_number} · 表格 {tables[0].table_number}"
    pages = "".join(
        _render_one_table_shot_page(
            table,
            change=change,
            side=side,
            source_id=(
                f"{source_prefix}-{side}-{visual_index}"
                if source_prefix
                else ""
            ),
            alias_target=(source_page_aliases or {}).get((side, visual_index)),
        )
        for visual_index, table in enumerate(tables)
    )
    if pages and '<div class="table-shot-page' not in pages:
        # Every occurrence on this side aliases the canonical page image.
        # Keep the table card's row facts, but do not leave an empty
        # "旧版截图/新版截图" frame that looks like a missing screenshot.
        return ""
    return f'<div class="table-shot"><h4>{_escape(heading)}</h4>{pages}</div>'


def _render_one_table_shot_page(
    table: TableVisual,
    *,
    change: TableChange | None = None,
    side: str = "old",
    source_id: str = "",
    alias_target: str | None = None,
) -> str:
    """Render one table screenshot page inside a screenshot group."""

    caption = f"页 {table.page_number} · 表格 {table.table_number}"
    id_html = f' id="{_escape(source_id)}"' if source_id else ""
    if alias_target:
        # The canonical page occurrence already carries the single visible
        # screenshot for this physical page. Keep an invisible source alias
        # so any coordinate focus can still resolve without a jump label.
        return (
            f'<span class="prose-source-alias-anchor table-source-alias-anchor"{id_html}'
            f' data-source-alias="{_escape(alias_target)}"></span>'
        )
    text_backed = table.ocr_status == "text_backed_exact_match"
    image_html = (
        f'<img alt="{_escape(caption)}" src="{table.image_data_uri}">'
        if table.image_data_uri
        else (
            '<div class="snippet">无截图：扁平文字精确匹配，行列边界未验证</div>'
            if text_backed
            else '<div class="snippet">无截图：仅使用结构化表格行摘要</div>'
        )
    )  # 文字表格兜底没有截图，避免渲染空图片。
    context_uri, highlighted = table_context_image(table, change, side)
    source_view_html = (
        f' data-source-view="{_escape(json.dumps(table.context_bbox))}"'
        if context_uri and table.context_bbox
        else ""
    )
    if context_uri:
        image_html = f'<img alt="{_escape(caption)} · 完整原页" src="{context_uri}">'
        caption += " · 完整原页 · " + ("浅色差异标注" if highlighted else "未标色，供上下文核对") + " · 点击放大"
    return (
        f'<div class="table-shot-page"{id_html}{source_view_html}>'
        f'<div class="table-shot-page-label">{_escape(caption)}</div>'
        f"{image_html}"
        "</div>"
    )


def _display_table_grid_summary(summary: str) -> str:
    """Convert internal grid diagnostics into user-facing table evidence text."""

    match = re.search(r"横线\s*(\d+)\s*条，竖线\s*(\d+)\s*条", summary)
    if match:
        return f"表格网格：横线 {match.group(1)} 条，竖线 {match.group(2)} 条"
    if summary:
        return "表格网格：检测未完成"
    return "表格网格：未检测到稳定网格"


def _render_table_row_summary(
    change: TableChange,
) -> str:
    """Render compact HTML from precomputed row-change facts."""

    source_rows = list(change.row_changes)
    if not source_rows:
        message = (
            "表题/表号发生变化，未检测到行级内容变化"
            if change.caption_changed
            else "未抽取到可靠行级文字，请结合截图复核"
        )
        kind = "表题/表号变化" if change.caption_changed else "需截图复核"
        source_rows = [TableRowChange(message, "", "", kind)]
    review_rows = [row for row in source_rows if row.change_type == "需人工复核"]
    material_rows = [row for row in source_rows if row.change_type != "需人工复核"]
    row_budget = 20
    material_budget = (
        row_budget - 1
        if material_rows and review_rows
        else row_budget
    )
    visible_material_rows = material_rows[:material_budget]
    review_budget = row_budget - len(visible_material_rows)
    visible_source_rows = [
        *visible_material_rows,
        *review_rows[:review_budget],
    ]  # 实质变化至少保留一席；两类同时存在时也固定保留一条不确定性证据。
    visible_rows = [_render_table_row_change(row) for row in visible_source_rows]
    remaining_rows = material_rows[len(visible_material_rows):] + review_rows[review_budget:]
    omitted_count = len(remaining_rows)
    omitted_note = (
        f'<details class="focus-more"><summary>其余 {omitted_count} 行表格变化（全部保留）</summary>'
        '<table class="table-row-summary"><thead><tr><th>项目</th><th>旧版</th><th>新版</th><th>类型</th></tr></thead><tbody>'
        + "".join(_render_table_row_change(row) for row in remaining_rows)
        + '</tbody></table></details>'
        if omitted_count else ""
    )
    return (
        f'<p class="focus-help">共 {len(source_rows)} 条表格明细，先展示 {len(visible_source_rows)} 条；需复核 {len(review_rows)} 条。</p>'
        '<table class="table-row-summary"><thead><tr>'
        '<th>项目</th><th>旧版</th><th>新版</th><th>类型</th>'
        '</tr></thead><tbody>'
        + "\n".join(visible_rows)
        + "</tbody></table>"
        + omitted_note
    )


    # 不同Column组的物理抽取顺序不是文档事实，按列号排序；同列号的
    # duplicate occurrences用原始index保持组内顺序，并交给review路径。


def _generic_row_equal_under_proven_column_boundary_drift(
    old_row: str,
    new_row: str,
    old_rows: list[str],
    new_rows: list[str],
) -> bool:
    """Ignore a generic row only under one table-wide proven column merge."""

    old_entries = _table_row_field_entries(old_row)
    new_entries = _table_row_field_entries(new_row)
    if not (
        old_entries
        and new_entries
        and len(old_entries) != len(new_entries)
        and all(re.fullmatch(r"column\s+\d+", entry[2], flags=re.I) for entry in old_entries)
        and all(re.fullmatch(r"column\s+\d+", entry[2], flags=re.I) for entry in new_entries)
    ):
        return False
    supported_patterns = _generic_boundary_merge_pattern_evidence(
        old_rows,
        new_rows,
        old_column_count=len(old_entries),
        new_column_count=len(new_entries),
    )
    old_is_header = _generic_boundary_entries_look_like_header(old_entries)
    new_is_header = _generic_boundary_entries_look_like_header(new_entries)
    if old_is_header != new_is_header:
        return False
    return any(
        len(evidence_keys) >= 2
        and (
            _generic_header_matches_boundary_merge_pattern(
                old_entries,
                new_entries,
                pattern,
            )
            if old_is_header
            else _generic_data_matches_boundary_merge_pattern(
                old_entries,
                new_entries,
                pattern,
            )
        )
        for pattern, evidence_keys in supported_patterns.items()
    )  # 两条不同数据行必须证明同一连续列合并；表头 token 也只能在该列组内重排。


    # 仅取消词序；关系运算符、技术标识大小写和其他可见 token 继续参与身份保护。


def _render_table_row_change(row_change: TableRowChange) -> str:
    """Render one user-readable table-change record."""

    class_name = _table_kind_class(row_change.change_type)
    reader_old_value = _reader_table_inline_text(row_change.old_value)
    reader_new_value = _reader_table_inline_text(row_change.new_value)
    difference_hint = _reader_pair_difference_hint(
        reader_old_value,
        reader_new_value,
    )
    if row_change.change_type == "需人工复核":
        old_value_html, new_value_html = _escape(reader_old_value), _escape(reader_new_value)
    elif (
        reader_old_value
        and reader_new_value
        and len(compact_inline(reader_old_value)) <= _READER_TABLE_VALUE_MAX_CHARS
        and len(compact_inline(reader_new_value)) <= _READER_TABLE_VALUE_MAX_CHARS
    ):
        old_value_html, new_value_html = _inline_diff_html(
            reader_old_value,
            reader_new_value,
        )
    else:
        old_value_html = _render_reader_table_cell_html(
            reader_old_value,
            max_chars=_READER_TABLE_VALUE_MAX_CHARS,
            difference_hint=difference_hint,
        )
        new_value_html = _render_reader_table_cell_html(
            reader_new_value,
            max_chars=_READER_TABLE_VALUE_MAX_CHARS,
            difference_hint=difference_hint,
        )
    item_html = _render_reader_table_cell_html(
        _reader_table_inline_text(row_change.item),
        max_chars=_READER_TABLE_ITEM_MAX_CHARS,
    )
    if glyph_note := _unverified_pua_mapping_note(
        row_change.old_value,
        row_change.new_value,
        reader_old_text=reader_old_value,
        reader_new_text=reader_new_value,
        force_reader_equivalent=(
            row_change.change_type == "需人工复核"
            and _table_row_display_key(reader_old_value)
            == _table_row_display_key(reader_new_value)
        ),
    ):
        item_html += f'<div class="change-summary">{_escape(glyph_note)}</div>'
    return (
        "<tr>"
        f"<td>{item_html}</td>"
        f"<td>{old_value_html}</td>"
        f"<td>{new_value_html}</td>"
        f'<td><span class="table-kind {class_name}">{_escape(row_change.change_type)}</span></td>'
        "</tr>"
    )


def _reader_table_inline_text(value: str) -> str:
    """Decode reader glyphs and normalize only explicitly labelled symbols."""

    decoded = _normalize_table_row_math_text(
        compact_inline(
            readable_symbol_font_glyphs(
                _normalize_generic_table_header_wraps(value)
            ).replace("↵", " ")
        )
    )
    decoded = compact_inline(decoded)
    parts: list[str] = []
    for part in decoded.split(" | "):
        symbol_field = re.fullmatch(
            r"(?P<label>Symbol(?:\[\d+\])?)=(?P<value>.*)",
            part,
            flags=re.I,
        )
        if symbol_field is None:
            parts.append(part)
            continue
        symbol_value = _normalize_compact_symbol_spacing(
            symbol_field.group("value"),
            field_label="Symbol",
        )
        parts.append(f'{symbol_field.group("label")}={symbol_value}')
    return " | ".join(parts)


def _reader_table_cell_text(
    value: str,
    *,
    max_chars: int,
    difference_hint: str = "",
) -> str:
    """Return a bounded reader summary while audit formats keep the full cell."""

    compact = compact_inline(value)
    if len(compact) <= max_chars:
        return compact
    preview = _reader_snippet_preview(compact, max_chars=min(96, max_chars // 2))
    hint = f"；{difference_hint}" if difference_hint else ""
    return (
        f"超长单元格已折叠（{len(compact)} 字）；开头“{preview}”"
        f"{hint}；完整值见 JSON/CSV 或展开 HTML。"
    )


def _render_reader_table_cell_html(
    value: str,
    *,
    max_chars: int,
    difference_hint: str = "",
) -> str:
    """Render a long table cell closed by default without discarding evidence."""

    compact = compact_inline(value)
    summary = _reader_table_cell_text(
        compact,
        max_chars=max_chars,
        difference_hint=difference_hint,
    )
    if len(compact) <= max_chars:
        return _escape(summary)
    return (
        '<details class="table-cell-detail">'
        f"<summary>{_escape(summary)}</summary>"
        f'<div class="table-cell-detail-body">{_escape(value)}</div>'
        "</details>"
    )


def _table_kind_class(kind: str) -> str:
    """Return a CSS class for a structured table summary kind."""

    if kind == "无变化":
        return "table-kind-same"
    if "删除" in kind:
        return "table-kind-del"
    if "新增" in kind:
        return "table-kind-add"
    if "复核" in kind:
        return "table-kind-review"
    return "table-kind-change"


    # 完整显示/equality key 仍保留换行；这里只防止同一参数因 PDF 软换行位置变化而拆成新增/删除。


    # 报告项目名判断必须保留原码位；否则 U+F067 会被可读映射成 γ，丢失审计证据。


def _table_row_primary_identity(row: str) -> str:
    """Return an explicit identity or the same first cell under generic Column N schema."""

    fields = _table_row_fields(row)
    identity_field_label = ""
    label = _first_table_field(
        fields,
        ("parameter", "characteristic", "description", "label", "name"),
    )
    if not label:
        label = fields.get("symbol", "")
        if label:
            identity_field_label = "symbol"
    if not label:
        label = fields.get("column 1", "")
    if compact_inline(label).casefold() in {
        "parameter",
        "characteristic",
        "description",
        "label",
        "name",
        "symbol",
    }:
        return ""  # 续页重复表头不是数据行身份。
    if not label:
        return ""
    if identity_field_label:
        return _table_row_text_key(label, field_label=identity_field_label)
    return _table_row_display_key(label)


    # 所有单行/多行值都编码为 JSON string tuple；长度和转义有边界，任意原文都不能伪造换行结构。


def _table_diff_kind(old_value: str, new_value: str) -> str:
    """Classify one visual table row diff for the summary column."""

    if old_value and new_value:
        return "替换/修改"
    if old_value:
        return "旧表删除行"
    return "新表新增行"


def _render_nav_item(index: int, change: SectionChange) -> str:
    """Render one left-navigation entry with an explicit change type label."""

    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    return (
        f'<a class="nav-item nav-{change.change_type}" href="#change-{index}">'
        f"<span>{index}</span>"
        '<div class="nav-body">'
        f'<div class="nav-label">{_escape(label)}</div>'
        f'<div class="nav-location">{_escape(_short_nav_location(_display_change_location(change)))}</div>'
        "</div></a>"
    )


def _render_table_nav_item(index: int, change: TableChange) -> str:
    """Render a table finding in the same navigation surface as prose changes."""

    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    material_count = len(_material_table_row_changes(change))
    review_count = len(_table_review_rows(change))
    detail_parts = []
    annotation_count = sum(r.row_role == "annotation" for r in change.row_changes)
    if annotation_count:
        detail_parts.append(f"{annotation_count} 条表说明")
    if material_count:
        detail_parts.append(f"{material_count} 行")
    if review_count:
        detail_parts.append(f"{review_count} 项复核")
    detail = " + ".join(detail_parts) or "无行级结果"
    if change.caption_changed:
        detail = f"{detail} + 表题" if change.row_changes else "表题/表号"
    return (
        f'<a class="nav-item nav-{change.change_type}" href="#table-change-{index}">'
        f"<span>T{index}</span>"
        '<div class="nav-body">'
        f'<div class="nav-label">表格{_escape(label)} · {_escape(detail)}</div>'
        f'<div class="nav-location">{_escape(_table_change_title(change))}</div>'
        "</div></a>"
    )


def _short_nav_location(location: str) -> str:
    """Keep the discriminating tail of a deep section path in the narrow sidebar."""

    parts = [part.strip() for part in location.split(" / ") if part.strip()]
    if len(parts) <= 2:
        return location
    return "… / " + " / ".join(parts[-2:])


def _display_change_location(change: SectionChange) -> str:
    """Return a friendlier location label for human-facing reports."""

    if change.report_location == "范围起始页前序内容":
        section = change.new_section or change.old_section
        if section:
            side = "新" if change.new_section else "旧"
            return _display_section_location(section, side)
    return change.report_location


def _display_section_location(section: Section, side: str) -> str:
    """Return a friendly section location for reports."""

    if section.location == "范围起始页前序内容":
        return f"{side}选择范围第 {section.start_page} 页的章节前内容"
    return section.location


def _empty_change_message(change: SectionChange) -> str:
    """Explain why a change card has no snippet body."""

    if change.change_type == "unchanged":
        return "该章节未发现正文或标题变化。"
    if change.old_section and change.new_section:
        return "该章节发生变化，但当前报告按固定展示上限收录正文片段；完整审计片段请查看同目录下的 CSV 或 JSON。"
    return "该章节没有可展示的正文片段，请回到源 PDF 对应页复核。"


def _change_summary(change: SectionChange) -> str:
    """Summarize visible snippet counts and likely review focus."""

    parts: list[str] = []
    if change.replaced_snippets:
        parts.append(f"{len(change.replaced_snippets)} 处替换")
    if change.formula_review_records:
        parts.append(f"{len(change.formula_review_records)} 处公式来源待核实")
    if change.review_replaced_snippets:
        review_label = (
            "上下文归属待核实" if change.context_review_records else
            "图文差异待复核" if _FIGURE_FRAGMENT_REVIEW_REASON in change.review_reason else
            "结构顺延复核"
        )
        parts.append(f"{len(change.review_replaced_snippets)} 处{review_label}")
    if change.added_snippets:
        label = "段新版待核实原文" if change.change_type == "review" else "段新增"
        parts.append(f"{len(_reader_single_list_groups(change.added_snippets))} {label}")
    if change.removed_snippets:
        label = "段旧版待核实原文" if change.change_type == "review" else "段删除"
        parts.append(f"{len(_reader_single_list_groups(change.removed_snippets))} {label}")
    if change.omitted_snippet_count:
        parts.append(f"{change.omitted_snippet_count} 处未展示")

    natures = _change_natures(change)
    if natures:
        parts.append("重点关注: " + "、".join(natures))
    return "；".join(parts)


def _change_natures(change: SectionChange) -> list[str]:
    """Infer high-level review categories from displayed snippets."""

    texts: list[str] = []
    for pair in change.replaced_snippets:
        texts.extend([pair.old, pair.new])
    texts.extend(change.added_snippets)
    texts.extend(change.removed_snippets)
    joined = "\n".join(texts)
    natures: list[str] = []
    if re.search(r"(?i)\b(?:step|steps)\s+\d+|\b\d+\s+through\s+\d+\b", joined):
        natures.append("步骤编号/步骤范围")
    if re.search(r"(?i)\b(?:appendix|section)\s+[A-Z0-9]+(?:\.\d+)*\b", joined):
        natures.append("附录/章节引用")
    if re.search(r"(?i)\b(?:<=|>=|≤|≥|<|>|=|[+-]?\d+(?:\.\d+)?\s*(?:ps|mv|db|gt/s|mhz|ghz|ui|%))\b", joined):
        natures.append("数值或限值")
    if _change_has_wording_difference(change):
        natures.append("术语/文字")
    return natures[:4]


def _change_has_wording_difference(change: SectionChange) -> bool:
    """Return True when visible word or identifier tokens differ across sides."""

    old_texts = [pair.old for pair in change.replaced_snippets] + change.removed_snippets
    new_texts = [pair.new for pair in change.replaced_snippets] + change.added_snippets
    return _wording_token_keys(old_texts) != _wording_token_keys(new_texts)


def _wording_token_keys(texts: list[str]) -> tuple[str, ...]:
    """Collect generic word-like keys while excluding numeric display variants."""

    return tuple(
        token.key
        for text in texts
        for token in _inline_tokens(text)
        if re.search(r"[a-z\u4e00-\u9fff]", token.key, flags=re.I)
    )


def _focus_allows_deltas(old_text: str, new_text: str) -> bool:
    # Linearized math/table text retains the established neutral reader hint;
    # fragmented extraction is not a trustworthy compact word change.
    return not any(_reader_snippet_collapse_kind(value) for value in (old_text, new_text))


def _focus_context_html(old_text: str, new_text: str, neutral: bool) -> tuple[str, str]:
    old_html, new_html = ((_escape(old_text), _escape(new_text))
                          if neutral or not old_text or not new_text or re.search(r"[\ue000-\uf8ff]", old_text + new_text)
                          else _inline_diff_html(old_text, new_text))
    hint = (_reader_pair_difference_hint(old_text, new_text) if old_text and new_text
            else _reader_single_side_evidence_hint(old_text or new_text))
    return tuple(_render_collapsible_snippet_html(text, rendered, difference_hint=hint)
                 for text, rendered in ((old_text, old_html), (new_text, new_html)))


def _render_pair_html(old_text: str, new_text: str, *, neutral: bool = False) -> str:
    """Render old/new replacement snippets with inline highlighting."""

    old_html, new_html = ((_escape(old_text), _escape(new_text)) if neutral
                          else _inline_diff_html(old_text, new_text))
    difference_hint = _reader_pair_difference_hint(old_text, new_text)
    old_html = _render_collapsible_snippet_html(
        old_text,
        old_html,
        difference_hint=difference_hint,
    )
    new_html = _render_collapsible_snippet_html(
        new_text,
        new_html,
        difference_hint=difference_hint,
    )
    glyph_note = _unverified_pua_mapping_note(old_text, new_text)
    glyph_note_html = (
        f'<div class="match-basis">{_escape(glyph_note)}</div>'
        if glyph_note
        else ""
    )
    return f"""
        {glyph_note_html}
        <div class="compare-grid">
          <div class="pane">
            <h4>旧协议</h4>
            <div class="snippet">{old_html}</div>
          </div>
          <div class="pane">
            <h4>新协议</h4>
            <div class="snippet">{new_html}</div>
          </div>
        </div>
    """


def _render_review_pairs_html(
    pairs: list[SnippetPair], *, context_scope: bool = False, figure_scope: bool = False,
) -> str:
    """Render ambiguous changes without red/green change emphasis."""

    if not pairs:
        return ""
    label = (
        "上下文归属待核实（不表示文字已一致；双侧原文及邻近项目保留）"
        if context_scope else
        "图文差异待复核（旧、新原文保留供核对）"
        if figure_scope else
        "结构顺延复核（不计入核心技术差异；旧、新原文保留供核对）"
    )
    rendered: list[str] = [f'<div class="match-basis">{label}</div>']
    for pair in pairs:
        old_html = _render_collapsible_snippet_html(pair.old, _escape(pair.old))
        new_html = _render_collapsible_snippet_html(pair.new, _escape(pair.new))
        rendered.append(
            f"""
        <div class="compare-grid review-evidence">
          <div class="pane">
            <h4>旧协议（复核）</h4>
            <div class="snippet">{old_html}</div>
          </div>
          <div class="pane">
            <h4>新协议（复核）</h4>
            <div class="snippet">{new_html}</div>
          </div>
        </div>
        """
        )
    return "\n".join(rendered)


def _render_single_list(title: str, snippets: list[str], css_class: str) -> str:
    """Render added-only or removed-only snippets."""

    if not snippets:
        return ""
    items: list[str] = []
    for snippet in _reader_single_list_groups(snippets):
        marked_html = (_escape(snippet) if css_class == "span"
                       else f'<mark class="{css_class}">{_escape(snippet)}</mark>')
        rendered_html = _render_collapsible_snippet_html(
            snippet,
            marked_html,
            difference_hint=_reader_single_side_evidence_hint(snippet),
        )
        items.append(f"<li>{rendered_html}</li>")
    items_html = "\n".join(items)
    return f"<h4>{title}</h4><ul class=\"single-list\">{items_html}</ul>"


def _reader_single_list_groups(snippets: list[str]) -> list[str]:
    """Return readable snippets without guessing technical-label semantics."""

    return [
        compact
        for snippet in snippets
        if (compact := compact_inline(snippet))
    ]


def _render_collapsible_snippet_html(
    value: str,
    body_html: str,
    *,
    difference_hint: str = "",
) -> str:
    """Keep raw evidence available without showing linearized layout text by default."""

    kind = _reader_snippet_collapse_kind(value)
    if kind is None:
        return body_html
    notice = _reader_snippet_notice(
        value,
        kind,
        expandable=True,
        difference_hint=difference_hint,
    )
    visible_tail = _reader_visible_prose_tail(value)
    tail_html = (
        '<div class="snippet-visible-tail"><strong>可读正文：</strong>'
        f"{_escape(visible_tail)}</div>"
        if visible_tail
        else ""
    )
    if kind == "layout":
        notice = _reader_snippet_notice(
            value,
            kind,
            expandable=False,
            difference_hint=difference_hint,
        )
        return (
            '<div class="snippet-folded-layout">'
            f"{_escape(notice)} "
            '<a href="protocol_diff_data.json">查看审计数据</a>'
            "</div>"
            f"{tail_html}"
        )  # 线性化公式/表体不再嵌入 HTML；原文只保留在 JSON/CSV 与源 PDF。
    return (
        f'<details class="snippet-detail snippet-detail-{kind}">'
        f'<summary>{_escape(notice)}</summary>'
        f'<div class="snippet-detail-body">{body_html}</div>'
        "</details>"
        f"{tail_html}"
    )


def _reader_snippet_text(value: str, *, difference_hint: str = "") -> str:
    """Return concise reader text while JSON/CSV retain the complete snippet."""

    kind = _reader_snippet_collapse_kind(value)
    if kind is None:
        return value
    notice = _reader_snippet_notice(
        value,
        kind,
        expandable=False,
        difference_hint=difference_hint,
    )
    visible_tail = _reader_visible_prose_tail(value)
    tail_text = f" 可读正文：{visible_tail}" if visible_tail else ""
    return f"[{notice}]{tail_text}"


def _reader_snippet_notice(
    value: str,
    kind: str,
    *,
    expandable: bool,
    difference_hint: str = "",
) -> str:
    """Build the visible summary for a folded reader snippet."""

    character_count = len(compact_inline(value))
    if kind == "layout":
        preview = _reader_snippet_preview(value)
        evidence_hint = (
            "可展开原始文字，优先核对表格补充证据或源 PDF。"
            if expandable
            else "完整文字见 JSON/CSV 审计文件或源 PDF。"
        )
        notice = (
            f"疑似表格或公式的版面文字已折叠（{character_count} 字）；"
            f"开头“{preview}”；{evidence_hint}"
        )
        if difference_hint:
            notice += f" {difference_hint}"
        return notice
    raise ValueError(f"未知读者片段类型: {kind}")


def _reader_snippet_preview(value: str, max_chars: int = 72) -> str:
    """Keep one short distinguishing anchor without recreating the text wall."""

    compact = compact_inline(readable_symbol_font_glyphs(value))
    if _reader_short_formula_fragment(compact):
        technical_anchor = re.search(
            r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_]*[A-Z0-9_])"
            r"[A-Za-z][A-Za-z0-9_]{2,}(?![A-Za-z0-9_])",
            compact,
        )
        return (
            f"可辨识标记 {technical_anchor.group(0)}"
            if technical_anchor
            else "短公式或下标片段"
        )
    if re.search(r"[\ue000-\uf8ff]", compact):
        printable = compact_inline(re.sub(r"[\ue000-\uf8ff]+", " ", compact))
        ascii_anchor = re.search(
            r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_.-]{2,}",
            printable,
        )
        return (
            f"可辨识标记 {ascii_anchor.group(0)}"
            if ascii_anchor
            else "无可靠连续正文锚点"
        )
    if len(compact) <= max_chars:
        return compact
    cut = compact.rfind(" ", 0, max_chars + 1)
    if cut < max_chars // 2:
        cut = max_chars
    return compact[:cut].rstrip() + "…"


def _reader_single_side_evidence_hint(value: str) -> str:
    """Expose compact limit/assignment anchors from a folded add/delete wall."""

    if _reader_snippet_collapse_kind(value) != "layout":
        return ""
    compact = compact_inline(readable_symbol_font_glyphs(value))
    anchors = [
        compact_inline(match.group(0))
        for match in re.finditer(
            r"(?i)(?<![A-Za-z0-9_])"
            r"[A-Za-z][A-Za-z0-9_.-]{2,}\s*"
            r"(?:=|<=|>=|≤|≥|<|>)\s*"
            r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
            r"(?:\s*(?:dB|UI|mV|V|ps|ns|ppm|MHz|GHz|Ω|ohm))?",
            compact,
        )
    ]
    unique_anchors = list(dict.fromkeys(anchors))[-3:]
    if not unique_anchors:
        return ""
    return "关键锚点：" + "；".join(f"“{anchor}”" for anchor in unique_anchors)


def _reader_pair_difference_hint(old_text: str, new_text: str) -> str:
    """Expose compact changed anchors even when both raw layout walls stay folded."""

    if _reader_pair_is_layout_token_reorder(old_text, new_text):
        return "两侧公式/图表 token 及出现次数相同，仅线性提取顺序不同；需核对源 PDF。"

    old_tokens = _inline_tokens(old_text)
    new_tokens = _inline_tokens(new_text)
    matcher = difflib.SequenceMatcher(
        None,
        [token.key for token in old_tokens],
        [token.key for token in new_tokens],
        autojunk=False,
    )
    candidates: list[tuple[int, int, int, str]] = []
    for order, (tag, old_start, old_end, new_start, new_end) in enumerate(
        matcher.get_opcodes()
    ):
        if tag == "equal":
            continue
        old_anchor_tokens = _reader_anchor_tokens_with_attached_prefix(
            old_text,
            old_tokens,
            old_start,
            old_end,
        )
        new_anchor_tokens = _reader_anchor_tokens_with_attached_prefix(
            new_text,
            new_tokens,
            new_start,
            new_end,
        )
        old_anchor = _reader_difference_anchor(old_text, old_anchor_tokens)
        new_anchor = _reader_difference_anchor(new_text, new_anchor_tokens)
        changed_token_count = (old_end - old_start) + (new_end - new_start)
        technical_priority = _reader_difference_technical_priority(
            old_tokens[old_start:old_end],
            new_tokens[new_start:new_end],
            old_context=old_tokens[max(0, old_start - 2) : min(len(old_tokens), old_end + 3)],
            new_context=new_tokens[max(0, new_start - 2) : min(len(new_tokens), new_end + 3)],
        )
        candidates.append(
            (
                technical_priority,
                changed_token_count,
                order,
                f"旧“{old_anchor}” → 新“{new_anchor}”",
            )
        )

    if not candidates:
        return ""
    # Numeric/engineering anchors are review-critical; short changes and document order break ties.
    selected = sorted(candidates)[:3]
    selected.sort(key=lambda candidate: candidate[2])
    count_note = (
        f"（共 {len(candidates)} 处，显示 {len(selected)} 处）"
        if len(candidates) > len(selected)
        else ""
    )
    return (
        f"差异锚点{count_note}："
        + "；".join(candidate[3] for candidate in selected)
    )


# 定位编号允许小数式 Section 层级和短横线式 Figure/Table 编号，但不会吞掉普通工程量。
# 引用列表可使用逗号、分号、and/or，引用范围可使用 to/through；句末标点不属于引用本体。
# 每类同时记录单数和复数写法；只有复数前缀才允许后续编号省略定位词。
# 句末允许任意已证明完整的裸引用列表。右括号后还必须紧跟真正的句末，
# 防止 `(Figures 5 and 1) lanes` 中的技术计数被回溯成引用编号。
# 共享父编号的点分/短横线列表还可以在非数值续句动词前结束。介词、系动词不在
# 正向证明集中，因为 `1 in`/`1 by`/`1 at`/`1 with` 都可以是技术数值。
# 点分或短横线列表除形态一致外还必须共享父编号；整数列表只能在句末成立。
# 复数定位词允许省略后续定位词，但所有裸编号必须同形且到达该形态的可证明结束点。
# 单数写法及重复显式定位词列表只吞并带前缀的项，因此后续裸工程值天然保留。
# PDF 字体映射偶尔只丢失括号内的出处编号，例如 ``Equation ()``。它仍是一个
# 已显式标注类别的定位引用；先在读者副本中补成占位编号，随后由上面的完整
# 引用列表规则原子中和。JSON/CSV 继续保存原始空括号，不修改审计事实。
# 无显式 Section/Clause 词的 “See 31.3.10” 只中和该编号，后续裸数字继续按正文严格比较。
# 不同来源类型只能在明确引用谓语紧邻支配的连续 locator span 内折叠。
# 普通谓语 ``can see Table 1 markers`` 及句中其它 Table/Figure 事实不会进入该范围。


def _reader_table_structure_status(tables: tuple[TableVisual, ...]) -> str:
    """Summarize structural certainty without exposing extractor diagnostics."""

    if not tables:
        return "无对应表格"
    flat_count = sum(table.ocr_status == "text_backed_exact_match" or not table.row_alignment_reliable
                     or not table.content_fully_represented for table in tables)
    if flat_count == len(tables):
        return "行列边界未验证"
    if flat_count:
        return "部分行列边界未验证"
    return "行列边界已识别"


def _reader_difference_technical_priority(
    old_tokens: list[_InlineToken],
    new_tokens: list[_InlineToken],
    *,
    old_context: list[_InlineToken],
    new_context: list[_InlineToken],
) -> int:
    """Rank numeric limits, units, operators, and protocol identifiers first."""

    changed_text = " ".join(token.text for token in [*old_tokens, *new_tokens])
    context_text = " ".join(
        token.text for token in [*old_context, *new_context]
    )
    if re.search(
        r"(?i)<=|>=|≤|≥|≠|\b(?:ui|db|[kmg]hz|mv|ma|ohm|ppm|table|figure|section|"
        r"limit|threshold|minimum|maximum|min|max|shall|must|requirement)\b",
        context_text,
    ):
        return 0
    if re.search(r"\d", changed_text):
        return 1
    return 2


def _reader_anchor_tokens_with_attached_prefix(
    source: str,
    tokens: list[_InlineToken],
    start: int,
    end: int,
) -> list[_InlineToken]:
    """Keep the unchanged ``31`` beside a changed ``-9`` in equation labels."""

    selected = tokens[start:end]
    if (
        selected
        and start > 0
        and selected[0].text.startswith("-")
        and source[tokens[start - 1].end : selected[0].start] == ""
    ):
        return tokens[start - 1 : end]
    return selected


def _reader_difference_anchor(
    source: str,
    tokens: list[_InlineToken],
    *,
    max_chars: int = 48,
) -> str:
    """Return one bounded, printable source span for a folded-pair difference."""

    if not tokens:
        return "（无）"
    anchor = compact_inline(source[tokens[0].start : tokens[-1].end])
    anchor = re.sub(r"[\ue000-\uf8ff]+", "[未映射符号]", anchor)
    if len(anchor) <= max_chars:
        return anchor
    return anchor[: max_chars - 1].rstrip() + "…"


def _render_omitted_html(omitted_count: int) -> str:
    """Render an explicit note when snippet limiting hides additional deltas."""

    if omitted_count <= 0:
        return ""
    return f'<div class="omitted-note">{_escape(_omitted_snippet_message(omitted_count))}</div>'


def _omitted_snippet_message(omitted_count: int) -> str:
    """Explain that more substantive differences exist than are displayed."""

    return f"另有 {omitted_count} 条差异片段未展示；完整章节已比较，完整审计片段请查看同目录下的 CSV 或 JSON。"


def _inline_diff_html(old_text: str, new_text: str) -> tuple[str, str]:
    """Highlight substantive token changes without emphasizing PDF noise."""

    old_text = readable_symbol_font_glyphs(old_text)
    new_text = readable_symbol_font_glyphs(new_text)
    old_tokens = _inline_tokens(old_text)
    new_tokens = _inline_tokens(new_text)
    matcher = difflib.SequenceMatcher(
        None,
        [token.key for token in old_tokens],
        [token.key for token in new_tokens],
        autojunk=False,
    )
    old_highlights: list[tuple[int, int, str]] = []
    new_highlights: list[tuple[int, int, str]] = []

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_changed = old_tokens[old_start:old_end]
        new_changed = new_tokens[new_start:new_end]
        old_highlights.extend((token.start, token.end, "del") for token in old_changed)
        new_highlights.extend((token.start, token.end, "ins") for token in new_changed)

    return (
        _render_text_with_highlights(old_text, old_highlights),
        _render_text_with_highlights(new_text, new_highlights),
    )


def _render_text_with_highlights(text: str, highlights: list[tuple[int, int, str]]) -> str:
    """Render text with non-overlapping token highlights."""

    parts: list[str] = []
    cursor = 0
    for start, end, css_class in sorted(highlights):
        if start < cursor:
            continue
        parts.append(_escape(text[cursor:start]))
        parts.append(f'<mark class="{css_class}">{_escape(text[start:end])}</mark>')
        cursor = end
    parts.append(_escape(text[cursor:]))
    return "".join(parts)


def _escape(value: object) -> str:
    """HTML-escape values while preserving readable line breaks."""

    return html_lib.escape(reader_safe_glyphs(str(value)), quote=True)


def _rows_for_csv(changes: list[SectionChange]) -> list[dict[str, str]]:
    """Flatten section changes for spreadsheet review."""

    rows: list[dict[str, str]] = []
    for change in changes:
        audit_added = _audit_added_snippets(change)
        audit_removed = _audit_removed_snippets(change)
        audit_replaced = _audit_replaced_snippets(change)
        replaced = [
            f"旧: {pair.old} / 新: {pair.new}" for pair in audit_replaced
        ]
        summary_parts = []
        if audit_replaced:
            summary_parts.append(f"{len(audit_replaced)} 处替换")
        if audit_added:
            summary_parts.append(f"{len(audit_added)} 处新增")
        if audit_removed:
            summary_parts.append(f"{len(audit_removed)} 处删除")
        if change.omitted_snippet_count:
            summary_parts.append(f"读者报告有 {change.omitted_snippet_count} 处未展开")
        natures = _change_natures(change)
        if natures:
            summary_parts.append("重点关注: " + "、".join(natures))
        rows.append(
            {
                "change_type": _CHANGE_LABELS.get(change.change_type, change.change_type),
                "role": change.role,
                "report_location": _display_change_location(change),
                "new_location": (
                    _display_section_location(change.new_section, "新")
                    if change.new_section
                    else ""
                ),
                "old_location": (
                    _display_section_location(change.old_section, "旧")
                    if change.old_section
                    else ""
                ),
                "new_pages": change.new_section.page_range if change.new_section else "",
                "old_pages": change.old_section.page_range if change.old_section else "",
                # ``similarity`` remains the legacy alias for pairing score;
                # explicit columns prevent a rounded pairing score from being
                # mistaken for exact content equality.
                "similarity": f"{change.similarity:.6f}" if change.old_section and change.new_section else "",
                "pair_similarity": f"{change.similarity:.6f}" if change.old_section and change.new_section else "",
                "content_similarity": (
                    f"{content_similarity:.6f}"
                    if (content_similarity := _section_content_similarity(change)) is not None
                    else ""
                ),
                "critical_content_equal": (
                    "false" if _section_change_has_critical_delta(change) else "true"
                ) if change.old_section and change.new_section else "",
                "match_basis": change.match_basis,
                "review_reason": getattr(change, "review_reason", ""),
                "match_basis_label": _MATCH_BASIS_LABELS.get(
                    change.match_basis,
                    change.match_basis,
                ),
                "summary": _change_summary(change) if change.context_review_records else "；".join(summary_parts),
                "added_snippets": "\n".join(audit_added),
                "removed_snippets": "\n".join(audit_removed),
                "replaced_snippets": "\n".join(replaced),
                "context_review_records": json.dumps(change.context_review_records, ensure_ascii=False),
                "display_added_snippets": "\n".join(change.added_snippets),
                "display_replaced_snippets": json.dumps([{"old": p.old, "new": p.new} for p in change.replaced_snippets], ensure_ascii=False),
                "omitted_snippet_count": str(change.omitted_snippet_count),
            }
        )
    return rows


def _rows_for_table_csv(changes: list[TableChange]) -> list[dict[str, str]]:
    """Flatten the shared table-change model for spreadsheet review."""

    rows: list[dict[str, str]] = []
    for change in changes:
        fallback_kind = "表题/表号变化" if change.caption_changed else "未抽取到可靠行级文字"
        row_changes = change.row_changes or (TableRowChange("", "", "", fallback_kind),)
        for row_change in row_changes:
            rows.append(
                {
                    "table_change_type": _CHANGE_LABELS.get(change.change_type, change.change_type),
                    "role": change.role,
                    "old_titles": _table_titles(change.old_tables),
                    "new_titles": _table_titles(change.new_tables),
                    "old_pages": _table_pages(change.old_tables),
                    "new_pages": _table_pages(change.new_tables),
                    "pair_similarity": f"{_table_pairing_similarity(change):.6f}" if change.old_tables and change.new_tables else "",
                    "content_similarity": f"{change.similarity:.6f}" if change.old_tables and change.new_tables else "",
                    "item": row_change.item,
                    "old_value": row_change.old_value,
                    "new_value": row_change.new_value,
                    "row_change_type": row_change.change_type,
                    "row_role": row_change.row_role,
                    "source_receipts": json.dumps(row_change.source_receipts, ensure_ascii=False),
                }
            )
    return rows


def _table_titles(tables: tuple[TableVisual, ...]) -> str:
    """Return unique table captions in source order."""

    return " / ".join(_unique_table_titles(tables))


def _table_pages(tables: tuple[TableVisual, ...]) -> str:
    """Return comma-separated source PDF pages for a logical table."""

    return ",".join(str(table.page_number) for table in tables)


def _change_counts(changes: list[SectionChange]) -> dict[str, int]:
    """Count changes by type."""

    counts: dict[str, int] = {}
    for change in changes:
        counts[change.change_type] = counts.get(change.change_type, 0) + 1
    return counts


def _section_change_page_pair(
    change: SectionChange,
) -> tuple[int | None, int | None]:
    """Return the old/new starting pages used by the page evidence group."""

    return (
        change.old_section.start_page if change.old_section else None,
        change.new_section.start_page if change.new_section else None,
    )


def _table_change_page_pair(
    change: TableChange,
) -> tuple[int | None, int | None]:
    """Return the first old/new pages represented by a logical table."""

    old_pages = [table.page_number for table in change.old_tables]
    new_pages = [table.page_number for table in change.new_tables]
    return (
        min(old_pages) if old_pages else None,
        min(new_pages) if new_pages else None,
    )


def _build_page_source_candidates(
    table_changes: Iterable[TableChange],
    prose_changes: Iterable[SectionChange],
    prose_visual_lookup: dict[tuple[str, str | None, str | None], ProseSourceVisualGroup],
) -> dict[tuple[str, int], tuple[str, tuple[float, float, float, float] | None, int]]:
    """Choose one screenshot candidate per physical source page.

    A page group is the reader's visual comparison unit.  Visible table or
    prose occurrences normally provide the image; this map supplies a page
    side only when every owning occurrence is text-only or an alias.  The
    integer priority is internal and is not exposed in the report.
    """

    candidates: dict[
        tuple[str, int], tuple[str, tuple[float, float, float, float] | None, int]
    ] = {}
    def add(
        side: str,
        page: int,
        uri: str,
        view_box: tuple[float, float, float, float] | None,
        priority: int,
    ) -> None:
        if not uri:
            return
        key = (side, page)
        previous = candidates.get(key)
        if previous is None or priority < previous[2]:
            candidates[key] = (uri, view_box, priority)

    # Prefer a table's complete-page context image when one exists. A table
    # card may own only a crop, while the page group must provide a symmetric
    # old/new page view. If no complete context exists, the table crop remains
    # a valid last-resort source candidate.
    for change in table_changes:
        for side, tables in (("old", change.old_tables), ("new", change.new_tables)):
            for table in tables:
                context_uri, _highlighted = table_context_image(table, change, side)
                if context_uri:
                    add(side, table.page_number, context_uri, table.context_bbox, 1)
                elif table.image_data_uri:
                    add(side, table.page_number, table.image_data_uri, None, 2)

    for change in prose_changes:
        group = prose_visual_lookup.get(_section_change_visual_identity(change))
        if group is None:
            continue
        for side, visuals in (("old", group.old_visuals), ("new", group.new_visuals)):
            for visual in visuals:
                add(
                    side,
                    visual.page_number,
                    # Prose crops cover the complete page for the current
                    # source builder. Keep the annotated image first so the
                    # shared screenshot still shows coordinate-backed text
                    # differences; raw remains the fallback for review-only
                    # or unlocalized visuals.
                    visual.image_data_uri or visual.raw_image_data_uri,
                    visual.source_view_box,
                    3,
                )
    return candidates


def _visible_prose_source_pages(
    group: ProseSourceVisualGroup | None,
    aliases: dict[tuple[str, int], str] | None,
) -> dict[str, dict[str, set[int]]]:
    """Return all and visible physical pages owned by a prose card."""

    all_pages = {"old": set(), "new": set()}
    visible = {"old": set(), "new": set()}
    if group is None:
        return {"all": all_pages, "visible": visible}
    for side, visuals in (("old", group.old_visuals), ("new", group.new_visuals)):
        for visual_index, visual in enumerate(visuals):
            all_pages[side].add(visual.page_number)
            if (aliases or {}).get((side, visual_index)):
                continue
            if visual.image_data_uri or visual.raw_image_data_uri:
                visible[side].add(visual.page_number)
    return {"all": all_pages, "visible": visible}


def _visible_table_source_pages(
    change: TableChange,
    aliases: dict[tuple[str, int], str] | None,
) -> dict[str, dict[str, set[int]]]:
    """Return all and visible physical pages owned by a table card."""

    all_pages = {"old": set(), "new": set()}
    visible = {"old": set(), "new": set()}
    for side, tables in (("old", change.old_tables), ("new", change.new_tables)):
        for visual_index, table in enumerate(tables):
            all_pages[side].add(table.page_number)
            if (aliases or {}).get((side, visual_index)):
                continue
            if table.image_data_uri or table.context_image_data_uri:
                visible[side].add(table.page_number)
    return {"all": all_pages, "visible": visible}


def _render_page_source_pair(
    component: list[tuple[int | None, int | None]],
    page_source_candidates: dict[
        tuple[str, int], tuple[str, tuple[float, float, float, float] | None, int]
    ],
    *,
    old_pages_override: Iterable[int] | None = None,
    new_pages_override: Iterable[int] | None = None,
) -> str:
    """Render source screenshots for the requested pages in one evidence group."""

    old_pages = (
        sorted(set(old_pages_override))
        if old_pages_override is not None
        else sorted({page for page, _ in component if page is not None})
    )
    new_pages = (
        sorted(set(new_pages_override))
        if new_pages_override is not None
        else sorted({page for _, page in component if page is not None})
    )

    def side_html(side: str, pages: list[int], label: str) -> str:
        if not pages:
            return ""
        # 只有确实拿到原页截图的物理页进入视觉对比区。页范围里没有截图证据的
        # 页仍保留在上方文字明细与 JSON 审计中；不再生成“截图暂不可用”空框，
        # 与历史整本报告（0 占位）的读者层契约保持一致。
        rendered_pages = [
            page for page in pages if (side, page) in page_source_candidates
        ]
        if not rendered_pages:
            return ""
        figures: list[str] = []
        for page in rendered_pages:
            uri, view_box, _priority = page_source_candidates[(side, page)]
            view_attr = (
                f' data-source-view="{_escape(json.dumps(view_box))}"'
                if view_box is not None
                else ""
            )
            source_id = f"page-source-{side}-{page}"
            figures.append(
                f'<figure class="page-source-figure" id="{_escape(source_id)}"{view_attr}>'
                f'<figcaption>PDF 第 {_escape(str(page))} 页 · 点击放大</figcaption>'
                f'<img src="{uri}" alt="{_escape(label)} PDF 第 {_escape(str(page))} 页原页截图">'
                "</figure>"
            )
        return (
            '<section class="page-source-side">'
            f'<h4>{_escape(label)}</h4>{"".join(figures)}</section>'
        )

    old_side = side_html("old", old_pages, "旧版原页截图")
    new_side = side_html("new", new_pages, "新版原页截图")
    if not old_side and not new_side:
        return ""
    return f'<div class="page-evidence-source-grid">{old_side}{new_side}</div>'


def _render_page_evidence_groups(
    entries: Iterable[tuple],
    *,
    page_source_candidates: dict[
        tuple[str, int], tuple[str, tuple[float, float, float, float] | None, int]
    ] | None = None,
    force_shared_source: bool = False,
) -> str:
    """Render one evidence block per old/new page pair.

    Table and prose cards are still computed independently, but the reader
    sees the physical page pair as the top-level unit.  One visible source
    screenshot is retained for each physical page; all table/text findings for
    that page remain as separate cards directly below the shared evidence.
    """

    materialized_entries = tuple(entries)
    grouped: dict[
        tuple[int | None, int | None],
        list[tuple[str, dict[str, dict[str, set[int]]] | None]],
    ] = {}
    order: list[tuple[int | None, int | None]] = []
    pair_metadata: dict[
        tuple[int | None, int | None],
        dict[str, dict[str, set[int]]],
    ] = {}

    def normalize_source_metadata(source_pages: object):
        """Normalize entry metadata while keeping five-tuple callers compatible."""

        if not isinstance(source_pages, dict):
            return None
        all_pages = source_pages.get("all")
        visible_pages = source_pages.get("visible")
        if not isinstance(all_pages, dict):
            # Legacy metadata, if supplied by an external caller, represented
            # the visible pages directly as ``old``/``new``.
            all_pages = {
                side: set(source_pages.get(side, ()))
                for side in ("old", "new")
            }
            visible_pages = all_pages
        if not isinstance(visible_pages, dict):
            visible_pages = {"old": set(), "new": set()}
        normalized = {
            "all": {
                side: set(all_pages.get(side, ()))
                for side in ("old", "new")
            },
            "visible": {
                side: set(visible_pages.get(side, ()))
                for side in ("old", "new")
            },
        }
        return normalized

    for entry in materialized_entries:
        page_pair = entry[4]
        if page_pair not in grouped:
            grouped[page_pair] = []
            order.append(page_pair)
        source_pages = normalize_source_metadata(entry[5] if len(entry) > 5 else None)
        grouped[page_pair].append((entry[3], source_pages))
        if source_pages is not None:
            aggregate = pair_metadata.setdefault(
                page_pair,
                {
                    "all": {"old": set(), "new": set()},
                    "visible": {"old": set(), "new": set()},
                },
            )
            for bucket in ("all", "visible"):
                for side in ("old", "new"):
                    aggregate[bucket][side].update(source_pages[bucket][side])

    # A matched pair and a one-sided finding can still refer to the same
    # physical page, for example ``(19, 18)`` beside ``(None, 18)``.  Merge
    # those connected page pairs so one source page is never presented as two
    # unrelated top-level evidence blocks.  The cards retain their own
    # one-sided wording inside the shared block.
    parents = list(range(len(order)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    # A section's start page is only a sorting key.  Its source visuals may
    # span later pages, so merge page pairs that share any physical source
    # page as well.  This prevents a later card from reintroducing the same
    # page image through a fallback path.
    def source_pages_overlap(left_pair, right_pair) -> bool:
        left_metadata = pair_metadata.get(left_pair)
        right_metadata = pair_metadata.get(right_pair)
        if left_metadata is None or right_metadata is None:
            return False
        return any(
            left_metadata["all"][side] & right_metadata["all"][side]
            for side in ("old", "new")
        )

    for left, left_pair in enumerate(order):
        for right in range(left + 1, len(order)):
            right_pair = order[right]
            same_old = (
                left_pair[0] is not None
                and left_pair[0] == right_pair[0]
            )
            same_new = (
                left_pair[1] is not None
                and left_pair[1] == right_pair[1]
            )
            if same_old or same_new or source_pages_overlap(left_pair, right_pair):
                union(left, right)

    components: dict[int, list[tuple[int | None, int | None]]] = {}
    for index, page_pair in enumerate(order):
        components.setdefault(find(index), []).append(page_pair)

    # Standalone callers may already render source images inside their cards.
    # Preserve their incremental fallback behavior; the main report opts into
    # the shared-source mode below and therefore does not use this ledger.
    global_visible_pages = {"old": set(), "new": set()}
    for metadata in pair_metadata.values():
        for side in ("old", "new"):
            global_visible_pages[side].update(metadata["visible"][side])
    fallback_rendered_pages: set[tuple[str, int]] = set()

    rendered: list[str] = []
    for component in sorted(components.values(), key=lambda pairs: order.index(pairs[0])):
        old_pages = sorted({page for page, _ in component if page is not None})
        new_pages = sorted({page for _, page in component if page is not None})
        component_metadata = [pair_metadata[pair] for pair in component if pair in pair_metadata]
        for metadata in component_metadata:
            old_pages.extend(metadata["all"]["old"])
            new_pages.extend(metadata["all"]["new"])
        old_pages = sorted(set(old_pages))
        new_pages = sorted(set(new_pages))

        def side_label(prefix: str, pages: list[int]) -> str:
            if not pages:
                return f"{prefix}无对应页"
            joined = "、".join(str(page) for page in pages)
            return f"{prefix}第 {joined} 页"

        old_label = side_label("旧版", old_pages)
        new_label = side_label("新版", new_pages)
        if len(old_pages) == len(new_pages) == 1 and old_pages[0] == new_pages[0]:
            title = f"第 {old_pages[0]} 页对比证据"
        else:
            title = f"{old_label} / {new_label} 对比证据"
        old_token = "-".join(str(page) for page in old_pages) if old_pages else "none"
        new_token = "-".join(str(page) for page in new_pages) if new_pages else "none"
        component_items = [
            item
            for page_pair in component
            for item in grouped[page_pair]
        ]
        component_cards = [card for card, _source_pages in component_items]
        card_count = len(component_cards)
        has_source_page_metadata = False
        visible_pages = {"old": set(), "new": set()}
        for _card, source_pages in component_items:
            if source_pages is None:
                continue
            has_source_page_metadata = True
            for side in ("old", "new"):
                visible_pages[side].update(source_pages["visible"].get(side, ()))
        if page_source_candidates and has_source_page_metadata and force_shared_source:
            # Every page group is the visual comparison unit. Render all
            # pages owned by the component once, before its complete list of
            # table/text cards. This prevents a matched card from becoming a
            # misleading one-sided screenshot merely because another card
            # already claimed the same page.
            source_html = _render_page_source_pair(
                component,
                page_source_candidates,
                old_pages_override=old_pages,
                new_pages_override=new_pages,
            )
        elif page_source_candidates and has_source_page_metadata:
            missing_old_pages = [
                page for page in old_pages
                if page not in visible_pages["old"]
                and page not in global_visible_pages["old"]
                and ("old", page) not in fallback_rendered_pages
            ]
            missing_new_pages = [
                page for page in new_pages
                if page not in visible_pages["new"]
                and page not in global_visible_pages["new"]
                and ("new", page) not in fallback_rendered_pages
            ]
            fallback_rendered_pages.update(("old", page) for page in missing_old_pages)
            fallback_rendered_pages.update(("new", page) for page in missing_new_pages)
            source_html = (
                _render_page_source_pair(
                    component,
                    page_source_candidates,
                    old_pages_override=missing_old_pages,
                    new_pages_override=missing_new_pages,
                )
                if missing_old_pages or missing_new_pages
                else ""
            )
        elif page_source_candidates:
            # Preserve the standalone helper's legacy behavior for callers
            # that provide only rendered cards and no source metadata.
            source_html = (
                _render_page_source_pair(component, page_source_candidates)
                if not any("<img" in card for card in component_cards)
                else ""
            )
        else:
            source_html = ""
        rendered.append(
            f'''<section class="page-evidence-group" id="page-evidence-{old_token}-{new_token}"
                data-old-page="{_escape(old_token)}" data-new-page="{_escape(new_token)}">
              <h3 class="page-evidence-title">{_escape(title)}</h3>
              <p class="page-evidence-note">本页共 {card_count} 项表格/正文证据；左右原页截图直接展示，原文坐标浅色标注，下面列出全部差异明细。</p>
              {source_html}
              {"".join(component_cards)}
            </section>'''
        )
    return "\n".join(rendered)


def _section_change_page_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Return a stable page-order key for a matched or one-sided section."""

    pages = [
        section.start_page
        for section in (change.old_section, change.new_section)
        if section is not None
    ]
    first_page = min(pages) if pages else 0
    role_order = 1 if change.role == "document_metadata" else 0
    return (first_page, role_order, change.report_location)


def _comparison_method_note(result: DiffResult) -> str:
    """Describe the matching strategy used for this report."""

    if any(
        change.match_basis == "user_page_window_anchor"
        for change in result.changes
    ):
        return (
            "两侧页窗由用户声明为强关联范围；常规章节匹配后，已将页窗内最相关正文锚定比较，"
            "页码不作逐页硬对齐。"
        )
    fallback_count = _page_fallback_section_count(result)
    total_sections = len(result.old_sections) + len(result.new_sections)
    if fallback_count and fallback_count == total_sections:
        return "未识别到稳定章节，已退回按页块和正文相似度比较；页码用于定位，不作为唯一匹配依据。"
    if fallback_count:
        return "至少一份 PDF 未识别到稳定章节，已混合使用章节、页块和正文相似度比较；页码用于定位，不作为唯一匹配依据。"
    return "按章节编号、标题和正文相似度匹配；页码只用于定位，不用于直接对齐。"


def _report_scope_note(options: DiffOptions) -> str:
    """Explain output boundaries that matter during protocol review."""

    if options.comparison_profile == "general":
        return ("通用文档模式：比较正文、表格、作者、邮箱和出版修订记录；"
                "重复页眉页脚按内容比较，不按重复页数报差异。已证明的纯排版和引用改号仍被过滤。"
                "OCR 及复杂版面需复核，公式不自动比较；未检查区域在覆盖清单中列出。")
    return (
        "只报告正文和表格的实质内容差异；作者、联系方式、目录及出版记录不计入差异，"
        "已确认的纯排版、自动折行和章节改号不计入差异；"
        "正文以源 PDF 截图作为第一视觉层，"
        "表格会额外提供截图辅助复核；"
        "公式自动对比已关闭：分式、根号、上下标和式号只用于版面隔离，不生成公式增删、修改、相似度或颜色差分结论；"
        "视觉核对覆盖文字一致页，以及正文变化页中图题唯一、位置固定且边界完整的图示区域；未覆盖范围单独列出，不自动解释图形语义；"
        "重复页眉页脚和动态页码会尽量过滤；"
        "章节、Figure、表格和条件的引用编号、列表及范围变化不计入读者差异，数值、限值和单位仍严格比较；"
        "完整章节参与比较，报告保留全部已识别差异；摘要长度不改变比较结论。"
    )


def _empty_report_message(result: DiffResult) -> str:
    """Return an empty-state message that matches the active comparison mode."""

    assessment = _assessment_for_report(result)
    if assessment.state is ReliabilityState.INDETERMINATE:
        return "无法判断是否存在差异。"
    if assessment.state is ReliabilityState.DEGRADED:
        return "未检出差异，但不能据此确认一致。"
    if _page_fallback_section_count(result):
        return "未检出受支持的可抽取正文或结构化表格差异；公式未自动比较。"
    return "未检出受支持的可抽取正文或结构化表格差异；公式未自动比较。"


def _assessment_for_report(result: DiffResult) -> PairAssessment:
    """Return the attached assessment, conservatively handling legacy results."""

    if result.assessment is not None:
        return result.assessment
    empty_metrics = DocumentQualityMetrics(0, 0, 0, 0, 0, (), (), 1.0)
    return PairAssessment(
        state=ReliabilityState.INDETERMINATE,
        headline="无法判断：比较结果缺少可靠性评估数据",
        reasons=("该比较结果由旧版调用方构造，未包含抽取质量指标。",),
        supported_profile=SUPPORTED_PROFILE,
        allows_no_difference_conclusion=False,
        old_document=empty_metrics,
        new_document=empty_metrics,
    )


def _coverage_source_label(result, side, page, *, html=False):
    if page is None:
        return "—"
    uri = Path(getattr(result, side + "_pdf")).resolve().as_uri() + f"#page={page}"
    return f'<a href="{_escape(uri)}">{page}</a>' if html else f"[{page}]({uri})"


def _render_visual_coverage_html(result: DiffResult) -> str:
    """同一份覆盖清单供读者和机器输出使用；计数按事项，不按页数。"""
    from .coverage_review import coverage_review_items
    items = coverage_review_items(result)
    if not items:
        return ""
    rows = "".join(
        "<tr><td>" + _coverage_source_label(result, "old", item["old_page"], html=True)
        + "</td><td>" + _coverage_source_label(result, "new", item["new_page"], html=True)
        + "</td><td>" + _escape(item["scope"]) + "</td><td>" + _escape(item["reason"])
        + "</td><td>" + _escape(item["action"]) + "</td></tr>" for item in items)
    return ('<details class="visual-coverage-details"><summary>未检查或需要复核的内容'
            f'（{len(items)} 项）</summary><p>点击 PDF 物理页码返回源文件；— 表示此侧不适用或没有安全对应页。'
            '以下事项不等于已确认差异。</p><table><thead><tr><th>旧页</th><th>新页</th>'
            '<th>范围</th><th>原因</th><th>下一步</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></details>')


def _coverage_review_markdown(result):
    from .coverage_review import coverage_review_items
    items = coverage_review_items(result)
    if not items:
        return []
    lines = ["", f"## 未检查或需要复核的内容（{len(items)} 项）", "",
             "页码为源 PDF 物理页。事项不等于已确认差异；— 表示此侧不适用或没有安全对应页。", "",
             "| 旧页 | 新页 | 范围 | 原因 | 下一步 |", "|---|---|---|---|---|"]
    for item in items:
        values = [_coverage_source_label(result, side, item[side + "_page"]) for side in ("old", "new")]
        values += [str(item[key]).replace("|", " / ").replace("\n", " ") for key in ("scope", "reason", "action")]
        lines.append("| " + " | ".join(values) + " |")
    return lines + [""]


def _render_assessment_html(assessment: PairAssessment) -> str:
    """Render the prominent reliability state shown above the report body."""

    reasons = "".join(
        f"<li>{_escape(reason)}</li>"
        for reason in _reader_assessment_reasons(assessment)
    )
    reasons_html = f"<ul>{reasons}</ul>" if reasons else ""
    label = _RELIABILITY_LABELS[assessment.state]
    return (
        f'<section class="assessment-banner assessment-{assessment.state.value}">'
        f"<h2>识别可信度：{_escape(label)}</h2>"
        f"<p>{_escape(assessment.headline)}</p>"
        f"<p>当前支持范围：{_escape(assessment.supported_profile)}</p>"
        f"{reasons_html}"
        "</section>"
    )


def _reader_assessment_reasons(assessment: PairAssessment) -> tuple[str, ...]:
    """Hide parser diagnostics from reader reports while JSON keeps the audit."""

    return tuple(
        reason
        for reason in assessment.reasons
        if _READER_EXTRACTION_WARNING_REASON_RE.fullmatch(reason) is None
    )


def _assessment_to_dict(assessment: PairAssessment) -> dict[str, object]:
    """Serialize reliability facts for downstream audit tooling."""

    return {
        "state": assessment.state.value,
        "headline": assessment.headline,
        "reasons": list(assessment.reasons),
        "supported_profile": assessment.supported_profile,
        "allows_no_difference_conclusion": assessment.allows_no_difference_conclusion,
        "old_document": _document_metrics_to_dict(assessment.old_document),
        "new_document": _document_metrics_to_dict(assessment.new_document),
    }


def _document_metrics_to_dict(metrics: DocumentQualityMetrics) -> dict[str, object]:
    """Serialize one side's measured quality facts without interpretation loss."""

    return {
        "selected_page_count": metrics.selected_page_count,
        "non_empty_page_count": metrics.non_empty_page_count,
        "character_count": metrics.character_count,
        "stable_section_count": metrics.stable_section_count,
        "fallback_section_count": metrics.fallback_section_count,
        "extraction_warnings": list(metrics.extraction_warnings),
        "layout_risk_pages": list(metrics.layout_risk_pages),
        "empty_page_ratio": metrics.empty_page_ratio,
        "average_characters_per_non_empty_page": (
            metrics.average_characters_per_non_empty_page
        ),
        "technical_layout_risk_pages": list(metrics.technical_layout_risk_pages),
        "fragmented_text_pages": list(metrics.fragmented_text_pages),
        "ocr_pages": list(metrics.ocr_pages),
        "image_dominant_pages": list(metrics.image_dominant_pages),  # 独立输出图像页，避免消费者误把 OCR/版面风险当作完整来源信息。
        "parser_route_pages": [
            [route, list(page_numbers)]
            for route, page_numbers in metrics.parser_route_pages
        ],  # JSON 没有元组类型；保留路由名到页号列表的稳定、可机读映射。
        "technical_character_count": metrics.technical_character_count,
        "technical_page_count": metrics.technical_page_count,
        "average_technical_characters_per_page": (
            metrics.average_technical_characters_per_page
        ),
        "duplicate_number_path_count": metrics.duplicate_number_path_count,
        "unstructured_technical_section_count": (
            metrics.unstructured_technical_section_count
        ),
        "uncomparable_table_visual_count": metrics.uncomparable_table_visual_count,
        "ambiguous_table_context_page_count": metrics.ambiguous_table_context_page_count,
        "page_window_consistent": metrics.page_window_consistent,
        "explicit_partial_window": metrics.explicit_partial_window,
        "limited_partial_window": metrics.limited_partial_window,
    }


def _extraction_audit_to_dict(
    audit_pages: tuple[PageExtractionAudit, ...],
) -> list[dict[str, object]]:
    """Serialize the compact route snapshot without page or block source text."""

    # 快照只含报告合同字段；DiffResult 不会因写 JSON 而再次接触完整 ExtractionResult。
    return [
        {
            "page_number": page_audit.page_number,
            "parser_route": page_audit.parser_route.value,
            "image_dominant": page_audit.image_dominant,
            "ocr_used": page_audit.ocr_used,
            "local_ocr_region_count": page_audit.local_ocr_region_count,
            "layout_risk": page_audit.layout_risk,
            "block_count": page_audit.block_count,
            "comparison_text_source": page_audit.comparison_text_source,
            "layout_backend_version": page_audit.layout_backend_version,
            "visual_noise_bbox_count": page_audit.visual_noise_bbox_count,
            **({"running_footer_texts": page_audit.running_footer_texts}
               if getattr(page_audit, "running_footer_texts", ()) else {}),
            **({"page_identity_header_texts": page_audit.page_identity_header_texts}
               if getattr(page_audit, "page_identity_header_texts", ()) else {}),
            **({"blank_glyph_evidence": page_audit.blank_glyph_evidence}
               if getattr(page_audit, "blank_glyph_evidence", ()) else {}),
        }
        for page_audit in audit_pages
    ]


def _provenance_to_dict(provenance: DiffProvenance | None) -> dict[str, object] | None:
    """Serialize reproducibility facts; legacy manually-built results remain explicit."""

    if provenance is None:
        return None
    thresholds = provenance.effective_thresholds
    visual_audit = provenance.visual_watchdog_audit
    return {
        "package_version": provenance.package_version,
        "build_commit": provenance.build_commit,
        "supported_profile": provenance.supported_profile,
        "inputs": {
            "old": _input_provenance_to_dict(provenance.old_input),
            "new": _input_provenance_to_dict(provenance.new_input),
        },
        "visual_watchdog_run": (
            {
                "enabled": visual_audit.enabled,
                "attempted": visual_audit.attempted,
                "backend_available": visual_audit.backend_available,
                "eligible_page_pair_count": visual_audit.eligible_page_pair_count,
                "checked_page_pair_count": visual_audit.checked_page_pair_count,
                "failed_page_pair_count": visual_audit.failed_page_pair_count,
                "ambiguous_page_count": visual_audit.ambiguous_page_count,
                "excluded_region_count": visual_audit.excluded_region_count,
                "complete": visual_audit.complete,
                "source_hashes_match": visual_audit.source_hashes_match,
                "identical_body_page_pairs": [
                    [old_page, new_page]
                    for old_page, new_page in visual_audit.identical_body_page_pairs
                ],
                "identity_render_dpi": visual_audit.identity_render_dpi,
                "old_visual_source_sha256": visual_audit.old_visual_source_sha256,
                "new_visual_source_sha256": visual_audit.new_visual_source_sha256,
                "coverage_issues": [
                    {"old_page_number": issue.old_page_number,
                     "new_page_number": issue.new_page_number, "reason": issue.reason,
                     "category": issue.category}
                    for issue in visual_audit.coverage_issues
                ],
                "checked_graphic_regions": list(visual_audit.checked_graphic_regions),
                "semantic_change_page_count": visual_audit.semantic_change_page_count,
                "semantic_change_pages": [
                    [old_page, new_page]
                    for old_page, new_page in visual_audit.semantic_change_pages
                ],
            }
            if visual_audit is not None
            else None
        ),
        "effective_thresholds": {
            "min_section_match_similarity": thresholds.min_section_match_similarity,
            "max_snippets_per_section": thresholds.max_snippets_per_section,
            "ocr_language": thresholds.ocr_language,
            "ocr_time_budget_seconds": thresholds.ocr_time_budget_seconds,
            "comparison_profile": thresholds.comparison_profile,
            "layout_backend": thresholds.layout_backend,
            "ocr_native_character_limit": thresholds.ocr_native_character_limit,
            "ocr_minimum_image_coverage": thresholds.ocr_minimum_image_coverage,
            "ocr_native_text_vertical_band_count": (
                thresholds.ocr_native_text_vertical_band_count
            ),
            "ocr_minimum_native_text_vertical_band_coverage": (
                thresholds.ocr_minimum_native_text_vertical_band_coverage
            ),
            "ocr_minimum_result_characters": thresholds.ocr_minimum_result_characters,
            "ocr_render_resolution": thresholds.ocr_render_resolution,
            "ocr_page_timeout_seconds": thresholds.ocr_page_timeout_seconds,
            "ocr_maximum_render_pixels": thresholds.ocr_maximum_render_pixels,
            "visual_watchdog": thresholds.visual_watchdog,
            "visual_render_dpi": thresholds.visual_render_dpi,
            "visual_pixel_delta_threshold": thresholds.visual_pixel_delta_threshold,
            "visual_min_changed_pixel_ratio": thresholds.visual_min_changed_pixel_ratio,
            "visual_min_component_area": thresholds.visual_min_component_area,
            "quality_min_reliable_characters": thresholds.min_reliable_characters,
            "quality_max_reliable_empty_page_ratio": thresholds.max_reliable_empty_page_ratio,
            "quality_min_pages_for_density_check": thresholds.min_pages_for_density_check,
            "quality_min_average_technical_characters_per_page": (
                thresholds.min_average_technical_characters_per_page
            ),
            "quality_min_pages_for_structure_coverage_check": (
                thresholds.min_pages_for_structure_coverage_check
            ),
            "quality_min_stable_sections_for_full_document": (
                thresholds.min_stable_sections_for_full_document
            ),
            "quality_max_structure_exempt_partial_window_ratio": (
                thresholds.max_structure_exempt_partial_window_ratio
            ),
            "quality_min_lines_for_fragmentation_check": (
                thresholds.min_lines_for_fragmentation_check
            ),
            "quality_max_average_characters_per_fragmented_line": (
                thresholds.max_average_characters_per_fragmented_line
            ),
            "quality_min_single_character_line_ratio": (
                thresholds.min_single_character_line_ratio
            ),
        },
    }


def _input_provenance_to_dict(provenance: object) -> dict[str, object]:
    """Serialize one typed input provenance record."""

    return {
        "path": str(provenance.path),
        "sha256": provenance.sha256,
        "selected_page_window": {
            "start_page": provenance.selected_start_page,
            "end_page": provenance.selected_end_page,
        },
    }


def _page_fallback_section_count(result: DiffResult) -> int:
    """Count synthetic no-heading page chunks across both documents."""

    return sum(
        1
        for section in result.old_sections + result.new_sections
        if section.section_id.startswith("P")
    )


def _page_count(sections: list[Section]) -> int:
    """Estimate total document pages from extracted section metadata."""

    if not sections:
        return 0
    return max(section.end_page for section in sections)


def _source_page_count(result: DiffResult, side: str) -> int:
    """Return the source PDF page count for one side of the comparison."""

    if side == "old":
        return result.old_total_pages or _page_count(result.old_sections)
    return result.new_total_pages or _page_count(result.new_sections)


def _selected_pages(result: DiffResult, side: str) -> tuple[int | None, int | None]:
    """Return the one-based inclusive selected source page range."""

    if side == "old":
        start = result.old_selected_start_page
        end = result.old_selected_end_page
        sections = result.old_sections
    else:
        start = result.new_selected_start_page
        end = result.new_selected_end_page
        sections = result.new_sections
    if start is not None and end is not None:
        return start, end
    if not sections:
        return None, None
    return min(section.start_page for section in sections), max(section.end_page for section in sections)


def _selected_page_label(result: DiffResult, side: str) -> str:
    """Format the selected page range for human-facing reports."""

    start, end = _selected_pages(result, side)
    total_pages = _source_page_count(result, side)
    if start is None or end is None:
        return "无可比较页"
    total_known = (
        result.old_total_pages_known if side == "old" else result.new_total_pages_known
    )
    if not total_known:
        extracted = str(start) if start == end else f"{start}-{end}"
        return f"已抽取 {extracted}（源文档总页数未知）"
    if total_pages and start == 1 and end == total_pages:
        return f"全部 (1-{total_pages})" if total_pages > 1 else "全部 (1)"
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _selected_page_payload(result: DiffResult, side: str) -> dict[str, object]:
    """Serialize selected-page metadata for the JSON audit file."""

    start, end = _selected_pages(result, side)
    total_pages = _source_page_count(result, side)
    total_known = (
        result.old_total_pages_known if side == "old" else result.new_total_pages_known
    )
    return {
        "start_page": start,
        "end_page": end,
        "label": _selected_page_label(result, side),
        "total_pages_known": total_known,
        "is_full_document": bool(
            total_known and total_pages and start == 1 and end == total_pages
        ),
    }


def _change_to_dict(
    change: SectionChange,
    *,
    display_change: SectionChange | None = None,
    role_override: str | None = None,
) -> dict[str, object]:
    """Serialize raw audit facts plus one reader-safe display projection."""

    audit_added = _audit_added_snippets(change)
    audit_removed = _audit_removed_snippets(change)
    audit_replaced = _audit_replaced_snippets(change)
    visible = display_change or change
    return {
        "change_type": change.change_type,
        "change_label": _CHANGE_LABELS.get(change.change_type, change.change_type),
        "role": role_override or change.role,
        "report_location": change.report_location,
        "display_report_location": _display_change_location(change),
        "old_location": change.old_section.location if change.old_section else None,
        "new_location": change.new_section.location if change.new_section else None,
        "display_old_location": (
            _display_section_location(change.old_section, "旧")
            if change.old_section
            else None
        ),
        "display_new_location": (
            _display_section_location(change.new_section, "新")
            if change.new_section
            else None
        ),
        "old_pages": change.old_section.page_range if change.old_section else None,
        "new_pages": change.new_section.page_range if change.new_section else None,
        # Keep the historical field as the pairing score and expose the
        # complete-content score separately.  Consumers must not infer exact
        # equality from a rounded pairing score.
        "similarity": round(change.similarity, 6),
        "pair_similarity": round(change.similarity, 6),
        "content_similarity": (
            round(content_similarity, 6)
            if (content_similarity := _section_content_similarity(change)) is not None
            else None
        ),
        "critical_content_equal": (
            not _section_change_has_critical_delta(change)
            if change.old_section and change.new_section
            else None
        ),
        "match_basis": change.match_basis,
        "review_reason": getattr(change, "review_reason", ""),
        "match_basis_label": _MATCH_BASIS_LABELS.get(
            change.match_basis,
            change.match_basis,
        ),
        "summary": _change_summary(visible),
        "change_nature": _change_natures(visible),
        "added_snippets": list(audit_added),
        "removed_snippets": list(audit_removed),
        "replaced_snippets": [
            {"old": pair.old, "new": pair.new} for pair in audit_replaced
        ],
        "context_review_records": list(visible.context_review_records),
        "formula_review_records": list(visible.formula_review_records),
        "review_replaced_snippets": [{"old": p.old, "new": p.new} for p in visible.review_replaced_snippets],
        "display_added_snippets": list(visible.added_snippets),
        "display_removed_snippets": list(visible.removed_snippets),
        "display_replaced_snippets": [
            {"old": pair.old, "new": pair.new} for pair in visible.replaced_snippets
        ],
        "omitted_snippet_count": change.omitted_snippet_count,
        "display_omitted_snippet_count": visible.omitted_snippet_count,
        "snippet_audit_complete": True,
    }


def _table_change_to_dict(change: TableChange) -> dict[str, object]:
    """Serialize a paired table and every row finding for audit/reuse."""

    return {
        "change_type": change.change_type,
        "change_label": _CHANGE_LABELS.get(change.change_type, change.change_type),
        "role": change.role,
        "old_titles": _unique_table_titles(change.old_tables),
        "new_titles": _unique_table_titles(change.new_tables),
        "old_pages": [table.page_number for table in change.old_tables],
        "new_pages": [table.page_number for table in change.new_tables],
        "pair_similarity": (
            round(_table_pairing_similarity(change), 6)
            if change.old_tables and change.new_tables
            else None
        ),
        "content_similarity": (
            round(change.similarity, 6)
            if change.old_tables and change.new_tables
            else None
        ),
        "caption_changed": change.caption_changed,
        "row_change_count": len(_material_table_row_changes(change)),
        "annotation_change_count": sum(r.row_role == "annotation" for r in change.row_changes),
        "review_count": len(_table_review_rows(change)),
        "row_changes": [
            {
                "item": row.item,
                "old_value": row.old_value,
                "new_value": row.new_value,
                "change_type": row.change_type,
                "row_role": row.row_role,
                "source_receipts": list(row.source_receipts),
            }
            for row in change.row_changes
        ],
    }


def _table_change_audit_to_dict(
    change: TableChange,
    *,
    reader_card_id: str | None,
    appendix_card_id: str | None,
    suppression_reason: str | None,
) -> dict[str, object]:
    """Serialize raw table findings without presenting suppressed candidates as facts."""

    payload = _table_change_to_dict(change)
    payload.update(
        {
            "reader_card_id": reader_card_id,
            "appendix_card_id": appendix_card_id,
            "reader_suppression_reason": suppression_reason,
        }
    )
    if not suppression_reason or reader_card_id or appendix_card_id:
        return payload

    row_changes = payload["row_changes"]
    if not isinstance(row_changes, list) or any(
        not isinstance(row, dict) for row in row_changes
    ):
        return payload  # 未知行结构保持原样；安全分类不能以丢弃审计内容为代价。

    payload["raw_candidate_change_type"] = payload["change_type"]
    payload["raw_candidate_change_label"] = payload["change_label"]
    payload["raw_candidate_row_change_count"] = payload["row_change_count"]
    payload["raw_candidate_review_count"] = payload["review_count"]

    safe_rows: list[dict[str, object]] = []
    for row in row_changes:
        safe_row = dict(row)
        safe_row["raw_candidate_change_type"] = safe_row["change_type"]
        safe_row["change_type"] = "需人工复核"
        safe_rows.append(safe_row)

    payload.update(
        {
            "change_type": "review",
            "change_label": _CHANGE_LABELS["review"],
            "row_change_count": 0,
            "review_count": len(safe_rows),
            "row_changes": safe_rows,
            "reader_disposition": "suppressed",
        }
    )
    return payload


def _section_to_dict(section: Section) -> dict[str, object]:
    """Serialize section metadata for debug/audit output."""

    return {
        "section_id": section.section_id,
        "heading": section.heading,
        "title": section.title,
        "level": section.level,
        "location": section.location,
        "number_path": list(section.number_path),
        "page_range": section.page_range,
        "body_preview": truncate(compact_inline(section.body), 500),
        "body_text": section.body,  # 完整比较视图正文，仅供 JSON 来源审计；保留所有换行。
        "role": section.role,
    }


def _table_visual_to_dict(table: TableVisual) -> dict[str, object]:
    """Serialize table visual metadata without duplicating huge image payloads."""

    return {
        "page_number": table.page_number,
        "table_number": table.table_number,
        "title": table.title,
        "bbox": list(table.bbox),
        "crop_bbox": table.crop_bbox,
        "source_id": table.source_id,
        "image_status": table.image_status,
        "page_bbox": list(table.page_bbox) if table.page_bbox is not None else None,
        "row_texts": list(table.row_texts),
        "raw_source_cells": table.raw_source_cells,
        "raw_cell_bounds": table.raw_cell_bounds,
        "grid_summary": table.grid_summary,
        "ocr_status": table.ocr_status,
        "ocr_text_preview": truncate(compact_inline(table.ocr_text), 500),
        "has_embedded_image": bool(table.image_data_uri),
        "has_context_image": bool(table.context_image_data_uri),
        "context_bbox": list(table.context_bbox) if table.context_bbox else None,
        "is_continuation": table.is_continuation,
        "content_fully_represented": table.content_fully_represented,
        "row_alignment_reliable": table.row_alignment_reliable,
        "data_rows_fully_represented": table.data_rows_fully_represented,
    }


def _formula_visual_to_dict(formula: FormulaVisual) -> dict[str, object]:
    """Serialize formula evidence without repeating embedded JPEG bytes."""

    return {
        "page_number": formula.page_number,
        "formula_number": formula.formula_number,
        "bbox": list(formula.bbox),
        "source_text": formula.source_text,
        "semantic_text": formula.semantic_text,
        "script_count": formula.script_count,
        "image_dhash": formula.image_dhash,
        "has_embedded_image": bool(formula.image_data_uri),
        "source_image_authoritative_for_complex_structure": True,
    }


def _prose_source_visual_group_to_dict(
    group: ProseSourceVisualGroup,
) -> dict[str, object]:
    """Serialize crop provenance without duplicating embedded JPEG bytes."""

    old_visuals = tuple(group.old_visuals)
    new_visuals = tuple(group.new_visuals)
    old_figure_visuals = tuple(group.old_figure_visuals)
    new_figure_visuals = tuple(group.new_figure_visuals)
    return {
        "change_type": group.change_type,
        "old_section_id": group.old_section_id,
        "new_section_id": group.new_section_id,
        "old_pages": [visual.page_number for visual in old_visuals],
        "new_pages": [visual.page_number for visual in new_visuals],
        "old_figure_pages": [
            visual.page_number for visual in old_figure_visuals
        ],
        "new_figure_pages": [
            visual.page_number for visual in new_figure_visuals
        ],
        "old_figure_captions": list(group.old_figure_captions),
        "new_figure_captions": list(group.new_figure_captions),
        "figure_match_basis": group.figure_match_basis,
        "figure_similarity": group.figure_similarity,
        "old_highlight_region_count": sum(
            visual.highlight_region_count for visual in old_visuals
        ),
        "new_highlight_region_count": sum(
            visual.highlight_region_count for visual in new_visuals
        ),
        "old_matched_snippet_count": sum(
            visual.matched_snippet_count for visual in old_visuals
        ),
        "new_matched_snippet_count": sum(
            visual.matched_snippet_count for visual in new_visuals
        ),
        "old_omitted_page_count": group.old_omitted_page_count,
        "new_omitted_page_count": group.new_omitted_page_count,
        "source_contexts": {
            side: [{"page": v.page_number, "bbox": list(v.context_bbox), "precision": v.context_precision}
                   for v in visuals if v.context_bbox is not None]
            for side, visuals in (("old", old_visuals), ("new", new_visuals))
        },
        "precision": "source-coordinate-word-translucent-highlight",
        "figure_precision": "source-figure-uncompared",
        "has_embedded_images": bool(
            old_visuals
            or new_visuals
            or old_figure_visuals
            or new_figure_visuals
        ),
    }


def _visual_review_item_to_dict(item: VisualReviewItem) -> dict[str, object]:
    """Serialize visual watchdog facts without embedding full-page image payloads."""

    return {
        "old_page_number": item.old_page_number,
        "new_page_number": item.new_page_number,
        "change_type": item.change_type,
        "pixel_similarity": item.pixel_similarity,
        "changed_pixel_ratio": item.changed_pixel_ratio,
        "reason": item.reason,
        "alignment_method": item.alignment_method,
        "diff_bbox": list(item.diff_bbox) if item.diff_bbox is not None else None,
        "focus_regions": [list(box) for box in item.focus_regions],
        "preview_size": list(item.preview_size) if item.preview_size is not None else None,
    }
