"""Section matching and difference summarization.

The comparison is designed for practical protocol review rather than formal
legal redlining. It first uses stable section numbers when possible, then falls
back to text similarity for renamed or renumbered sections. The report should be
treated as a review accelerator: important changes are surfaced with page and
section context, but final sign-off should still inspect the source PDFs.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 导入 difflib，计算文本相似度和差异摘要。
import difflib
# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path

# Codex说明(自动生成)： 从 models 导入 DiffOptions, DiffResult, ExtractionResult, Section 等名称，提供本文件后续流程需要的库能力。
from .models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    Section,
    SectionChange,
    SnippetPair,
)
# Codex说明(自动生成)： 从 pdf_extract 导入 extract_pdf_text，提供本文件后续流程需要的库能力。
from .pdf_extract import extract_pdf_text
# Codex说明(自动生成)： 从 sectioning 导入 section_document，提供本文件后续流程需要的库能力。
from .sectioning import section_document
# Codex说明(自动生成)： 从 text_utils 导入 normalize_for_similarity, normalize_line, truncate，提供本文件后续流程需要的库能力。
from .text_utils import normalize_for_similarity, normalize_line, truncate


# Codex说明(自动生成)： 定义函数 run_diff，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def run_diff(old_pdf: str | Path, new_pdf: str | Path, options: DiffOptions) -> DiffResult:
    """Run the complete extraction, sectioning, and comparison pipeline."""

    # Codex说明(自动生成)： 计算并保存 old_extraction，供后续语句继续读取或更新。
    old_extraction = extract_pdf_text(old_pdf)
    # Codex说明(自动生成)： 计算并保存 new_extraction，供后续语句继续读取或更新。
    new_extraction = extract_pdf_text(new_pdf)
    # Codex说明(自动生成)： 返回 compare_extractions(old_extraction, new_extraction, opt...，让调用方取得本函数的处理结果。
    return compare_extractions(old_extraction, new_extraction, options)


# Codex说明(自动生成)： 定义函数 compare_extractions，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def compare_extractions(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
    options: DiffOptions,
) -> DiffResult:
    """Compare two already-extracted PDFs.

    This entry point is useful for tests and future OCR integrations, where text
    may come from a different extractor but should still use the same section
    matching and report generation.
    """

    # Codex说明(自动生成)： 计算并保存 old_sections，供后续语句继续读取或更新。
    old_sections = section_document(old_extraction)
    # Codex说明(自动生成)： 计算并保存 new_sections，供后续语句继续读取或更新。
    new_sections = section_document(new_extraction)
    # Codex说明(自动生成)： 计算并保存 changes，供后续语句继续读取或更新。
    changes = compare_sections(old_sections, new_sections, options)
    # Codex说明(自动生成)： 计算并保存 warnings，供后续语句继续读取或更新。
    warnings = list(old_extraction.warnings) + list(new_extraction.warnings)
    # Codex说明(自动生成)： 检查条件 not old_sections，根据结果选择后续执行路径。
    if not old_sections:
        # Codex说明(自动生成)： 调用 warnings.append 更新列表或集合，把当前步骤产生的数据加入结果。
        warnings.append(f"{old_extraction.pdf_path.name}: 未识别到可比较文本段落。")
    # Codex说明(自动生成)： 检查条件 not new_sections，根据结果选择后续执行路径。
    if not new_sections:
        # Codex说明(自动生成)： 调用 warnings.append 更新列表或集合，把当前步骤产生的数据加入结果。
        warnings.append(f"{new_extraction.pdf_path.name}: 未识别到可比较文本段落。")
    # Codex说明(自动生成)： 返回 DiffResult(old_pdf=old_extraction.pdf_path, new_pdf=new...，让调用方取得本函数的处理结果。
    return DiffResult(
        old_pdf=old_extraction.pdf_path,
        new_pdf=new_extraction.pdf_path,
        old_sections=old_sections,
        new_sections=new_sections,
        changes=changes,
        warnings=warnings,
    )


# Codex说明(自动生成)： 定义函数 compare_sections，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def compare_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
) -> list[SectionChange]:
    """Match old/new sections and classify section-level changes."""

    # Codex说明(自动生成)： 计算并保存 matches，供后续语句继续读取或更新。
    matches = _match_sections(old_sections, new_sections, options)
    # Codex说明(自动生成)： 声明并保存 changes，同时保留类型信息方便维护和静态检查。
    changes: list[SectionChange] = []
    # Codex说明(自动生成)： 遍历 matches 中的 (old_index, new_index, similarity)，逐项执行循环体逻辑。
    for old_index, new_index, similarity in matches:
        # Codex说明(自动生成)： 计算并保存 old_section，供后续语句继续读取或更新。
        old_section = old_sections[old_index] if old_index is not None else None
        # Codex说明(自动生成)： 计算并保存 new_section，供后续语句继续读取或更新。
        new_section = new_sections[new_index] if new_index is not None else None
        # Codex说明(自动生成)： 检查条件 old_section and new_section，根据结果选择后续执行路径。
        if old_section and new_section:
            # Codex说明(自动生成)： 检查条件 _sections_effectively_unchanged(old_section, new_sectio...，根据结果选择后续执行路径。
            if _sections_effectively_unchanged(old_section, new_section, options):
                # Codex说明(自动生成)： 检查条件 options.include_unchanged_sections，根据结果选择后续执行路径。
                if options.include_unchanged_sections:
                    # Codex说明(自动生成)： 调用 changes.append 更新列表或集合，把当前步骤产生的数据加入结果。
                    changes.append(
                        SectionChange(
                            change_type="unchanged",
                            old_section=old_section,
                            new_section=new_section,
                            similarity=similarity,
                        )
                    )
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue
            # Codex说明(自动生成)： 计算并保存 (added, removed, replaced)，供后续语句继续读取或更新。
            added, removed, replaced = _summarize_text_delta(
                old_section.body,
                new_section.body,
                max_snippets=options.max_snippets_per_section,
            )
            # Codex说明(自动生成)： 检查条件 _section_heading_changed(old_section, new_section)，根据结果选择后续执行路径。
            if _section_heading_changed(old_section, new_section):
                # Codex说明(自动生成)： 调用 replaced.insert 更新列表或集合，把当前步骤产生的数据加入结果。
                replaced.insert(
                    0,
                    SnippetPair(
                        old=f"章节标题: {old_section.heading}",
                        new=f"章节标题: {new_section.heading}",
                    ),
                )
            # Codex说明(自动生成)： 调用 changes.append 更新列表或集合，把当前步骤产生的数据加入结果。
            changes.append(
                SectionChange(
                    change_type="modified",
                    old_section=old_section,
                    new_section=new_section,
                    similarity=similarity,
                    added_snippets=added,
                    removed_snippets=removed,
                    replaced_snippets=replaced[: options.max_snippets_per_section],
                )
            )
        # Codex说明(自动生成)： 当前一分支未命中时，继续检查条件 new_section。
        elif new_section:
            # Codex说明(自动生成)： 调用 changes.append 更新列表或集合，把当前步骤产生的数据加入结果。
            changes.append(
                SectionChange(
                    change_type="added",
                    old_section=None,
                    new_section=new_section,
                    similarity=0.0,
                    added_snippets=_first_units(new_section.body, options.max_snippets_per_section),
                )
            )
        # Codex说明(自动生成)： 当前一分支未命中时，继续检查条件 old_section。
        elif old_section:
            # Codex说明(自动生成)： 调用 changes.append 更新列表或集合，把当前步骤产生的数据加入结果。
            changes.append(
                SectionChange(
                    change_type="deleted",
                    old_section=old_section,
                    new_section=None,
                    similarity=0.0,
                    removed_snippets=_first_units(old_section.body, options.max_snippets_per_section),
                )
            )

    # Codex说明(自动生成)： 返回 sorted(changes, key=_change_sort_key)，让调用方取得本函数的处理结果。
    return sorted(changes, key=_change_sort_key)


# Codex说明(自动生成)： 定义函数 _match_sections，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _match_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
) -> list[tuple[int | None, int | None, float]]:
    """Pair old/new sections using exact keys first, then global best scores.

    The second pass builds all viable fallback candidates and assigns the
    strongest pairs first. That is more conservative than matching each new
    section greedily in document order, especially when protocols contain many
    repeated boilerplate clauses.
    """

    # Codex说明(自动生成)： 声明并保存 exact_old_by_key，同时保留类型信息方便维护和静态检查。
    exact_old_by_key: dict[str, list[int]] = {}
    # Codex说明(自动生成)： 遍历 enumerate(old_sections) 中的 (old_index, old_section)，逐项执行循环体逻辑。
    for old_index, old_section in enumerate(old_sections):
        # Codex说明(自动生成)： 调用 exact_old_by_key.setdefault(old_section.identity_key, []).append 更新列表或集合，把当前步骤产生的数据加入结果。
        exact_old_by_key.setdefault(old_section.identity_key, []).append(old_index)

    # Codex说明(自动生成)： 声明并保存 matched_old，同时保留类型信息方便维护和静态检查。
    matched_old: set[int] = set()
    # Codex说明(自动生成)： 声明并保存 matched_new，同时保留类型信息方便维护和静态检查。
    matched_new: set[int] = set()
    # Codex说明(自动生成)： 声明并保存 matches，同时保留类型信息方便维护和静态检查。
    matches: list[tuple[int | None, int | None, float]] = []

    # Codex说明(自动生成)： 遍历 enumerate(new_sections) 中的 (new_index, new_section)，逐项执行循环体逻辑。
    for new_index, new_section in enumerate(new_sections):
        # Codex说明(自动生成)： 计算并保存 exact_candidates，供后续语句继续读取或更新。
        exact_candidates = exact_old_by_key.get(new_section.identity_key, [])
        # Codex说明(自动生成)： 计算并保存 exact_match，供后续语句继续读取或更新。
        exact_match = next((idx for idx in exact_candidates if idx not in matched_old), None)
        # Codex说明(自动生成)： 检查条件 exact_match is not None，根据结果选择后续执行路径。
        if exact_match is not None:
            # Codex说明(自动生成)： 调用 matched_old.add，执行当前流程需要的具体操作或副作用。
            matched_old.add(exact_match)
            # Codex说明(自动生成)： 计算并保存 similarity，供后续语句继续读取或更新。
            similarity = _similarity(
                old_sections[exact_match].comparable_text,
                new_section.comparable_text,
            )
            # Codex说明(自动生成)： 调用 matches.append 更新列表或集合，把当前步骤产生的数据加入结果。
            matches.append((exact_match, new_index, similarity))
            # Codex说明(自动生成)： 调用 matched_new.add，执行当前流程需要的具体操作或副作用。
            matched_new.add(new_index)

    # Codex说明(自动生成)： 声明并保存 fallback_candidates，同时保留类型信息方便维护和静态检查。
    fallback_candidates: list[tuple[float, int, int]] = []
    # Codex说明(自动生成)： 遍历 enumerate(new_sections) 中的 (new_index, new_section)，逐项执行循环体逻辑。
    for new_index, new_section in enumerate(new_sections):
        # Codex说明(自动生成)： 检查条件 new_index in matched_new，根据结果选择后续执行路径。
        if new_index in matched_new:
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 遍历 enumerate(old_sections) 中的 (old_index, old_section)，逐项执行循环体逻辑。
        for old_index, old_section in enumerate(old_sections):
            # Codex说明(自动生成)： 检查条件 old_index in matched_old，根据结果选择后续执行路径。
            if old_index in matched_old:
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue
            # Codex说明(自动生成)： 计算并保存 score，供后续语句继续读取或更新。
            score = _section_match_score(old_section, new_section)
            # Codex说明(自动生成)： 检查条件 score >= options.min_section_match_similarity，根据结果选择后续执行路径。
            if score >= options.min_section_match_similarity:
                # Codex说明(自动生成)： 调用 fallback_candidates.append 更新列表或集合，把当前步骤产生的数据加入结果。
                fallback_candidates.append((score, old_index, new_index))

    # Codex说明(自动生成)： 遍历 sorted(fallback_candidates, reverse=True) 中的 (score, old_index, new_index)，逐项执行循环体逻辑。
    for score, old_index, new_index in sorted(fallback_candidates, reverse=True):
        # Codex说明(自动生成)： 检查条件 old_index in matched_old or new_index in matched_new，根据结果选择后续执行路径。
        if old_index in matched_old or new_index in matched_new:
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 调用 matched_old.add，执行当前流程需要的具体操作或副作用。
        matched_old.add(old_index)
        # Codex说明(自动生成)： 调用 matched_new.add，执行当前流程需要的具体操作或副作用。
        matched_new.add(new_index)
        # Codex说明(自动生成)： 计算并保存 similarity，供后续语句继续读取或更新。
        similarity = _similarity(
            old_sections[old_index].comparable_text,
            new_sections[new_index].comparable_text,
        )
        # Codex说明(自动生成)： 调用 matches.append 更新列表或集合，把当前步骤产生的数据加入结果。
        matches.append((old_index, new_index, min(score, similarity)))

    # Codex说明(自动生成)： 遍历 enumerate(new_sections) 中的 (new_index, _new_section)，逐项执行循环体逻辑。
    for new_index, _new_section in enumerate(new_sections):
        # Codex说明(自动生成)： 检查条件 new_index not in matched_new，根据结果选择后续执行路径。
        if new_index not in matched_new:
            # Codex说明(自动生成)： 调用 matches.append 更新列表或集合，把当前步骤产生的数据加入结果。
            matches.append((None, new_index, 0.0))

    # Codex说明(自动生成)： 遍历 enumerate(old_sections) 中的 (old_index, _old_section)，逐项执行循环体逻辑。
    for old_index, _old_section in enumerate(old_sections):
        # Codex说明(自动生成)： 检查条件 old_index not in matched_old，根据结果选择后续执行路径。
        if old_index not in matched_old:
            # Codex说明(自动生成)： 调用 matches.append 更新列表或集合，把当前步骤产生的数据加入结果。
            matches.append((old_index, None, 0.0))
    # Codex说明(自动生成)： 返回 matches，让调用方取得本函数的处理结果。
    return matches


# Codex说明(自动生成)： 定义函数 _section_match_score，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _section_match_score(old_section: Section, new_section: Section) -> float:
    """Score likely identity for renamed or renumbered sections."""

    # Codex说明(自动生成)： 计算并保存 title_score，供后续语句继续读取或更新。
    title_score = _similarity(old_section.title, new_section.title)
    # Codex说明(自动生成)： 计算并保存 location_score，供后续语句继续读取或更新。
    location_score = _similarity(old_section.location, new_section.location)
    # Codex说明(自动生成)： 计算并保存 text_score，供后续语句继续读取或更新。
    text_score = _similarity(
        old_section.comparable_text[:4000],
        new_section.comparable_text[:4000],
    )
    # Codex说明(自动生成)： 返回 max(title_score * 0.85 + text_score * 0.15, location_sc...，让调用方取得本函数的处理结果。
    return max(title_score * 0.85 + text_score * 0.15, location_score * 0.4 + text_score * 0.6)


# Codex说明(自动生成)： 定义函数 _similarity，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _similarity(left: str, right: str) -> float:
    """Return normalized SequenceMatcher ratio for two text values."""

    # Codex说明(自动生成)： 计算并保存 left_norm，供后续语句继续读取或更新。
    left_norm = normalize_for_similarity(left)
    # Codex说明(自动生成)： 计算并保存 right_norm，供后续语句继续读取或更新。
    right_norm = normalize_for_similarity(right)
    # Codex说明(自动生成)： 检查条件 not left_norm and (not right_norm)，根据结果选择后续执行路径。
    if not left_norm and not right_norm:
        # Codex说明(自动生成)： 返回 1.0，让调用方取得本函数的处理结果。
        return 1.0
    # Codex说明(自动生成)： 检查条件 not left_norm or not right_norm，根据结果选择后续执行路径。
    if not left_norm or not right_norm:
        # Codex说明(自动生成)： 返回 0.0，让调用方取得本函数的处理结果。
        return 0.0
    # Codex说明(自动生成)： 返回 difflib.SequenceMatcher(None, left_norm, right_norm, au...，让调用方取得本函数的处理结果。
    return difflib.SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


# Codex说明(自动生成)： 定义函数 _sections_effectively_unchanged，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _sections_effectively_unchanged(
    old_section: Section,
    new_section: Section,
    options: DiffOptions,
) -> bool:
    """Treat a matched section as unchanged only when title and body are stable."""

    # Codex说明(自动生成)： 计算并保存 body_same，供后续语句继续读取或更新。
    body_same = _similarity(old_section.body, new_section.body) >= options.unchanged_similarity
    # Codex说明(自动生成)： 返回 body_same and (not _section_heading_changed(old_section...，让调用方取得本函数的处理结果。
    return body_same and not _section_heading_changed(old_section, new_section)


# Codex说明(自动生成)： 定义函数 _section_heading_changed，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _section_heading_changed(old_section: Section, new_section: Section) -> bool:
    """Detect same-number section title changes that would otherwise be missed."""

    # Codex说明(自动生成)： 返回 normalize_for_similarity(old_section.heading) != normal...，让调用方取得本函数的处理结果。
    return normalize_for_similarity(old_section.heading) != normalize_for_similarity(new_section.heading)


# Codex说明(自动生成)： 定义函数 _summarize_text_delta，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _summarize_text_delta(
    old_text: str,
    new_text: str,
    max_snippets: int,
) -> tuple[list[str], list[str], list[SnippetPair]]:
    """Create compact added/removed/replaced snippets for one section."""

    # Codex说明(自动生成)： 计算并保存 old_units，供后续语句继续读取或更新。
    old_units = _split_units(old_text)
    # Codex说明(自动生成)： 计算并保存 new_units，供后续语句继续读取或更新。
    new_units = _split_units(new_text)
    # Codex说明(自动生成)： 计算并保存 matcher，供后续语句继续读取或更新。
    matcher = difflib.SequenceMatcher(None, old_units, new_units, autojunk=False)

    # Codex说明(自动生成)： 声明并保存 added，同时保留类型信息方便维护和静态检查。
    added: list[str] = []
    # Codex说明(自动生成)： 声明并保存 removed，同时保留类型信息方便维护和静态检查。
    removed: list[str] = []
    # Codex说明(自动生成)： 声明并保存 replaced，同时保留类型信息方便维护和静态检查。
    replaced: list[SnippetPair] = []

    # Codex说明(自动生成)： 遍历 matcher.get_opcodes() 中的 (tag, old_start, old_end, new_start, new_end)，逐项执行循环体逻辑。
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        # Codex说明(自动生成)： 检查条件 tag == 'equal'，根据结果选择后续执行路径。
        if tag == "equal":
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 检查条件 tag == 'insert'，根据结果选择后续执行路径。
        if tag == "insert":
            # Codex说明(自动生成)： 调用 added.extend 更新列表或集合，把当前步骤产生的数据加入结果。
            added.extend(truncate(unit) for unit in new_units[new_start:new_end])
        # Codex说明(自动生成)： 当前一分支未命中时，继续检查条件 tag == 'delete'。
        elif tag == "delete":
            # Codex说明(自动生成)： 调用 removed.extend 更新列表或集合，把当前步骤产生的数据加入结果。
            removed.extend(truncate(unit) for unit in old_units[old_start:old_end])
        # Codex说明(自动生成)： 当前一分支未命中时，继续检查条件 tag == 'replace'。
        elif tag == "replace":
            # Codex说明(自动生成)： 计算并保存 old_block_units，供后续语句继续读取或更新。
            old_block_units = old_units[old_start:old_end]
            # Codex说明(自动生成)： 计算并保存 new_block_units，供后续语句继续读取或更新。
            new_block_units = new_units[new_start:new_end]
            # Codex说明(自动生成)： 检查条件 len(old_block_units) == len(new_block_units)，根据结果选择后续执行路径。
            if len(old_block_units) == len(new_block_units):
                # Codex说明(自动生成)： 遍历 zip(old_block_units, new_block_units, strict=True) 中的 (old_unit, new_unit)，逐项执行循环体逻辑。
                for old_unit, new_unit in zip(old_block_units, new_block_units, strict=True):
                    # Codex说明(自动生成)： 调用 replaced.append 更新列表或集合，把当前步骤产生的数据加入结果。
                    replaced.append(SnippetPair(old=truncate(old_unit), new=truncate(new_unit)))
            # Codex说明(自动生成)： 处理前面条件都未命中时的默认分支。
            else:
                # Codex说明(自动生成)： 计算并保存 old_block，供后续语句继续读取或更新。
                old_block = " ".join(old_block_units)
                # Codex说明(自动生成)： 计算并保存 new_block，供后续语句继续读取或更新。
                new_block = " ".join(new_block_units)
                # Codex说明(自动生成)： 调用 replaced.append 更新列表或集合，把当前步骤产生的数据加入结果。
                replaced.append(SnippetPair(old=truncate(old_block), new=truncate(new_block)))
        # Codex说明(自动生成)： 检查条件 len(added) + len(removed) + len(replaced) >= max_snippets，根据结果选择后续执行路径。
        if len(added) + len(removed) + len(replaced) >= max_snippets:
            # Codex说明(自动生成)： 提前结束当前循环，跳出后不再执行本轮循环后续迭代。
            break

    # Codex说明(自动生成)： 返回 (_dedupe_keep_order(added)[:max_snippets], _dedupe_keep...，让调用方取得本函数的处理结果。
    return (
        _dedupe_keep_order(added)[:max_snippets],
        _dedupe_keep_order(removed)[:max_snippets],
        replaced[:max_snippets],
    )


# Codex说明(自动生成)： 定义函数 _split_units，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _split_units(text: str) -> list[str]:
    """Split text into review-sized units.

    Sentence-like chunks work better for Chinese protocols than word-level diffs
    because Chinese text has no guaranteed spaces. If extraction returns very
    long lines, the fallback chunks by length to keep report snippets readable.
    """

    # Codex说明(自动生成)： 声明并保存 raw_units，同时保留类型信息方便维护和静态检查。
    raw_units: list[str] = []
    # Codex说明(自动生成)： 遍历 text.splitlines() 中的 line，逐项执行循环体逻辑。
    for line in text.splitlines():
        # Codex说明(自动生成)： 计算并保存 normalized_line，供后续语句继续读取或更新。
        normalized_line = normalize_line(line)
        # Codex说明(自动生成)： 检查条件 not normalized_line，根据结果选择后续执行路径。
        if not normalized_line:
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 调用 raw_units.extend 更新列表或集合，把当前步骤产生的数据加入结果。
        raw_units.extend(_split_line_preserving_numbers(normalized_line))

    # Codex说明(自动生成)： 声明并保存 units，同时保留类型信息方便维护和静态检查。
    units: list[str] = []
    # Codex说明(自动生成)： 遍历 raw_units 中的 unit，逐项执行循环体逻辑。
    for unit in raw_units:
        # Codex说明(自动生成)： 检查条件 len(unit) <= 320，根据结果选择后续执行路径。
        if len(unit) <= 320:
            # Codex说明(自动生成)： 调用 units.append 更新列表或集合，把当前步骤产生的数据加入结果。
            units.append(unit)
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 遍历 range(0, len(unit), 260) 中的 start，逐项执行循环体逻辑。
        for start in range(0, len(unit), 260):
            # Codex说明(自动生成)： 计算并保存 chunk，供后续语句继续读取或更新。
            chunk = unit[start : start + 260].strip()
            # Codex说明(自动生成)： 检查条件 chunk，根据结果选择后续执行路径。
            if chunk:
                # Codex说明(自动生成)： 调用 units.append 更新列表或集合，把当前步骤产生的数据加入结果。
                units.append(chunk)
    # Codex说明(自动生成)： 返回 units，让调用方取得本函数的处理结果。
    return units


# Codex说明(自动生成)： 定义函数 _split_line_preserving_numbers，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _split_line_preserving_numbers(line: str) -> list[str]:
    """Split one line into sentence-ish units without breaking decimals.

    Protocols often use punctuation inside meaningful values: ``3.0 V``,
    ``1.2.3``, dates, firmware versions, and model names. A tiny state machine is
    clearer and safer here than a broad regular expression.
    """

    # Codex说明(自动生成)： 声明并保存 units，同时保留类型信息方便维护和静态检查。
    units: list[str] = []
    # Codex说明(自动生成)： 声明并保存 current，同时保留类型信息方便维护和静态检查。
    current: list[str] = []
    # Codex说明(自动生成)： 计算并保存 hard_endings，供后续语句继续读取或更新。
    hard_endings = set("。！？；;!?")

    # Codex说明(自动生成)： 遍历 enumerate(line) 中的 (index, char)，逐项执行循环体逻辑。
    for index, char in enumerate(line):
        # Codex说明(自动生成)： 调用 current.append 更新列表或集合，把当前步骤产生的数据加入结果。
        current.append(char)
        # Codex说明(自动生成)： 计算并保存 should_split，供后续语句继续读取或更新。
        should_split = False
        # Codex说明(自动生成)： 检查条件 char in hard_endings，根据结果选择后续执行路径。
        if char in hard_endings:
            # Codex说明(自动生成)： 计算并保存 should_split，供后续语句继续读取或更新。
            should_split = True
        # Codex说明(自动生成)： 当前一分支未命中时，继续检查条件 char == '.'。
        elif char == ".":
            # Codex说明(自动生成)： 计算并保存 previous_char，供后续语句继续读取或更新。
            previous_char = line[index - 1] if index > 0 else ""
            # Codex说明(自动生成)： 计算并保存 next_char，供后续语句继续读取或更新。
            next_char = line[index + 1] if index + 1 < len(line) else ""
            # Codex说明(自动生成)： 检查条件 previous_char.isdigit() and next_char.isdigit()，根据结果选择后续执行路径。
            if previous_char.isdigit() and next_char.isdigit():
                # Codex说明(自动生成)： 计算并保存 should_split，供后续语句继续读取或更新。
                should_split = False
            # Codex说明(自动生成)： 当前一分支未命中时，继续检查条件 next_char and (not next_char.isspace())。
            elif next_char and not next_char.isspace():
                # Codex说明(自动生成)： 计算并保存 should_split，供后续语句继续读取或更新。
                should_split = False
            # Codex说明(自动生成)： 处理前面条件都未命中时的默认分支。
            else:
                # Codex说明(自动生成)： 计算并保存 should_split，供后续语句继续读取或更新。
                should_split = True

        # Codex说明(自动生成)： 检查条件 should_split，根据结果选择后续执行路径。
        if should_split:
            # Codex说明(自动生成)： 计算并保存 unit，供后续语句继续读取或更新。
            unit = "".join(current).strip()
            # Codex说明(自动生成)： 检查条件 unit，根据结果选择后续执行路径。
            if unit:
                # Codex说明(自动生成)： 调用 units.append 更新列表或集合，把当前步骤产生的数据加入结果。
                units.append(unit)
            # Codex说明(自动生成)： 计算并保存 current，供后续语句继续读取或更新。
            current = []

    # Codex说明(自动生成)： 计算并保存 tail，供后续语句继续读取或更新。
    tail = "".join(current).strip()
    # Codex说明(自动生成)： 检查条件 tail，根据结果选择后续执行路径。
    if tail:
        # Codex说明(自动生成)： 调用 units.append 更新列表或集合，把当前步骤产生的数据加入结果。
        units.append(tail)
    # Codex说明(自动生成)： 返回 units，让调用方取得本函数的处理结果。
    return units


# Codex说明(自动生成)： 定义函数 _first_units，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _first_units(text: str, max_snippets: int) -> list[str]:
    """Return leading snippets for added or deleted whole sections."""

    # Codex说明(自动生成)： 返回 [truncate(unit) for unit in _split_units(text)[:max_sni...，让调用方取得本函数的处理结果。
    return [truncate(unit) for unit in _split_units(text)[:max_snippets]]


# Codex说明(自动生成)： 定义函数 _dedupe_keep_order，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _dedupe_keep_order(values: list[str]) -> list[str]:
    """Remove repeated snippets without changing the visible order."""

    # Codex说明(自动生成)： 声明并保存 seen，同时保留类型信息方便维护和静态检查。
    seen: set[str] = set()
    # Codex说明(自动生成)： 声明并保存 result，同时保留类型信息方便维护和静态检查。
    result: list[str] = []
    # Codex说明(自动生成)： 遍历 values 中的 value，逐项执行循环体逻辑。
    for value in values:
        # Codex说明(自动生成)： 检查条件 value and value not in seen，根据结果选择后续执行路径。
        if value and value not in seen:
            # Codex说明(自动生成)： 调用 seen.add，执行当前流程需要的具体操作或副作用。
            seen.add(value)
            # Codex说明(自动生成)： 调用 result.append 更新列表或集合，把当前步骤产生的数据加入结果。
            result.append(value)
    # Codex说明(自动生成)： 返回 result，让调用方取得本函数的处理结果。
    return result


# Codex说明(自动生成)： 定义函数 _change_sort_key，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _change_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Sort by new-page location first, then deleted old-page location."""

    # Codex说明(自动生成)： 计算并保存 section，供后续语句继续读取或更新。
    section = change.new_section or change.old_section
    # Codex说明(自动生成)： 计算并保存 page，供后续语句继续读取或更新。
    page = section.start_page if section else 0
    # Codex说明(自动生成)： 计算并保存 type_order，供后续语句继续读取或更新。
    type_order = {"modified": 0, "added": 1, "deleted": 2, "unchanged": 3}
    # Codex说明(自动生成)： 返回 (page, type_order.get(change.change_type, 9), change.re...，让调用方取得本函数的处理结果。
    return (page, type_order.get(change.change_type, 9), change.report_location)
