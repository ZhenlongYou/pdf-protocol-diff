"""Section matching and difference summarization.

The comparison is designed for practical protocol review rather than formal
legal redlining. It first uses stable section numbers when possible, then falls
back to text similarity for renamed or renumbered sections. The report should be
treated as a review accelerator: important changes are surfaced with page and
section context, but final sign-off should still inspect the source PDFs.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from .models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    Section,
    SectionChange,
    SnippetPair,
)
from .pdf_extract import extract_pdf_text
from .sectioning import section_document
from .text_utils import normalize_for_similarity, normalize_line, truncate


def run_diff(old_pdf: str | Path, new_pdf: str | Path, options: DiffOptions) -> DiffResult:
    """Run the complete extraction, sectioning, and comparison pipeline."""

    old_extraction = extract_pdf_text(old_pdf)
    new_extraction = extract_pdf_text(new_pdf)
    return compare_extractions(old_extraction, new_extraction, options)


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

    old_sections = section_document(old_extraction)
    new_sections = section_document(new_extraction)
    changes = compare_sections(old_sections, new_sections, options)
    warnings = list(old_extraction.warnings) + list(new_extraction.warnings)
    if not old_sections:
        warnings.append(f"{old_extraction.pdf_path.name}: 未识别到可比较文本段落。")
    if not new_sections:
        warnings.append(f"{new_extraction.pdf_path.name}: 未识别到可比较文本段落。")
    return DiffResult(
        old_pdf=old_extraction.pdf_path,
        new_pdf=new_extraction.pdf_path,
        old_sections=old_sections,
        new_sections=new_sections,
        changes=changes,
        warnings=warnings,
    )


def compare_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
) -> list[SectionChange]:
    """Match old/new sections and classify section-level changes."""

    matches = _match_sections(old_sections, new_sections, options)
    changes: list[SectionChange] = []
    for old_index, new_index, similarity in matches:
        old_section = old_sections[old_index] if old_index is not None else None
        new_section = new_sections[new_index] if new_index is not None else None
        if old_section and new_section:
            if _sections_effectively_unchanged(old_section, new_section, options):
                if options.include_unchanged_sections:
                    changes.append(
                        SectionChange(
                            change_type="unchanged",
                            old_section=old_section,
                            new_section=new_section,
                            similarity=similarity,
                        )
                    )
                continue
            added, removed, replaced = _summarize_text_delta(
                old_section.body,
                new_section.body,
                max_snippets=options.max_snippets_per_section,
            )
            if _section_heading_changed(old_section, new_section):
                replaced.insert(
                    0,
                    SnippetPair(
                        old=f"章节标题: {old_section.heading}",
                        new=f"章节标题: {new_section.heading}",
                    ),
                )
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
        elif new_section:
            changes.append(
                SectionChange(
                    change_type="added",
                    old_section=None,
                    new_section=new_section,
                    similarity=0.0,
                    added_snippets=_first_units(new_section.body, options.max_snippets_per_section),
                )
            )
        elif old_section:
            changes.append(
                SectionChange(
                    change_type="deleted",
                    old_section=old_section,
                    new_section=None,
                    similarity=0.0,
                    removed_snippets=_first_units(old_section.body, options.max_snippets_per_section),
                )
            )

    return sorted(changes, key=_change_sort_key)


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

    exact_old_by_key: dict[str, list[int]] = {}
    for old_index, old_section in enumerate(old_sections):
        exact_old_by_key.setdefault(old_section.identity_key, []).append(old_index)

    matched_old: set[int] = set()
    matched_new: set[int] = set()
    matches: list[tuple[int | None, int | None, float]] = []

    for new_index, new_section in enumerate(new_sections):
        exact_candidates = exact_old_by_key.get(new_section.identity_key, [])
        exact_match = next((idx for idx in exact_candidates if idx not in matched_old), None)
        if exact_match is not None:
            matched_old.add(exact_match)
            similarity = _similarity(
                old_sections[exact_match].comparable_text,
                new_section.comparable_text,
            )
            matches.append((exact_match, new_index, similarity))
            matched_new.add(new_index)

    fallback_candidates: list[tuple[float, int, int]] = []
    for new_index, new_section in enumerate(new_sections):
        if new_index in matched_new:
            continue
        for old_index, old_section in enumerate(old_sections):
            if old_index in matched_old:
                continue
            score = _section_match_score(old_section, new_section)
            if score >= options.min_section_match_similarity:
                fallback_candidates.append((score, old_index, new_index))

    for score, old_index, new_index in sorted(fallback_candidates, reverse=True):
        if old_index in matched_old or new_index in matched_new:
            continue
        matched_old.add(old_index)
        matched_new.add(new_index)
        similarity = _similarity(
            old_sections[old_index].comparable_text,
            new_sections[new_index].comparable_text,
        )
        matches.append((old_index, new_index, min(score, similarity)))

    for new_index, _new_section in enumerate(new_sections):
        if new_index not in matched_new:
            matches.append((None, new_index, 0.0))

    for old_index, _old_section in enumerate(old_sections):
        if old_index not in matched_old:
            matches.append((old_index, None, 0.0))
    return matches


def _section_match_score(old_section: Section, new_section: Section) -> float:
    """Score likely identity for renamed or renumbered sections."""

    title_score = _similarity(old_section.title, new_section.title)
    location_score = _similarity(old_section.location, new_section.location)
    text_score = _similarity(
        old_section.comparable_text[:4000],
        new_section.comparable_text[:4000],
    )
    return max(title_score * 0.85 + text_score * 0.15, location_score * 0.4 + text_score * 0.6)


def _similarity(left: str, right: str) -> float:
    """Return normalized SequenceMatcher ratio for two text values."""

    left_norm = normalize_for_similarity(left)
    right_norm = normalize_for_similarity(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return difflib.SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def _sections_effectively_unchanged(
    old_section: Section,
    new_section: Section,
    options: DiffOptions,
) -> bool:
    """Treat a matched section as unchanged only when title and body are stable."""

    body_same = _similarity(old_section.body, new_section.body) >= options.unchanged_similarity
    return body_same and not _section_heading_changed(old_section, new_section)


def _section_heading_changed(old_section: Section, new_section: Section) -> bool:
    """Detect same-number section title changes that would otherwise be missed."""

    return normalize_for_similarity(old_section.heading) != normalize_for_similarity(new_section.heading)


def _summarize_text_delta(
    old_text: str,
    new_text: str,
    max_snippets: int,
) -> tuple[list[str], list[str], list[SnippetPair]]:
    """Create compact added/removed/replaced snippets for one section."""

    old_units = _split_units(old_text)
    new_units = _split_units(new_text)
    matcher = difflib.SequenceMatcher(None, old_units, new_units, autojunk=False)

    added: list[str] = []
    removed: list[str] = []
    replaced: list[SnippetPair] = []

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            added.extend(truncate(unit) for unit in new_units[new_start:new_end])
        elif tag == "delete":
            removed.extend(truncate(unit) for unit in old_units[old_start:old_end])
        elif tag == "replace":
            old_block_units = old_units[old_start:old_end]
            new_block_units = new_units[new_start:new_end]
            if len(old_block_units) == len(new_block_units):
                for old_unit, new_unit in zip(old_block_units, new_block_units, strict=True):
                    replaced.append(SnippetPair(old=truncate(old_unit), new=truncate(new_unit)))
            else:
                old_block = " ".join(old_block_units)
                new_block = " ".join(new_block_units)
                replaced.append(SnippetPair(old=truncate(old_block), new=truncate(new_block)))
        if len(added) + len(removed) + len(replaced) >= max_snippets:
            break

    return (
        _dedupe_keep_order(added)[:max_snippets],
        _dedupe_keep_order(removed)[:max_snippets],
        replaced[:max_snippets],
    )


def _split_units(text: str) -> list[str]:
    """Split text into review-sized units.

    Sentence-like chunks work better for Chinese protocols than word-level diffs
    because Chinese text has no guaranteed spaces. If extraction returns very
    long lines, the fallback chunks by length to keep report snippets readable.
    """

    raw_units: list[str] = []
    for line in text.splitlines():
        normalized_line = normalize_line(line)
        if not normalized_line:
            continue
        raw_units.extend(_split_line_preserving_numbers(normalized_line))

    units: list[str] = []
    for unit in raw_units:
        if len(unit) <= 320:
            units.append(unit)
            continue
        for start in range(0, len(unit), 260):
            chunk = unit[start : start + 260].strip()
            if chunk:
                units.append(chunk)
    return units


def _split_line_preserving_numbers(line: str) -> list[str]:
    """Split one line into sentence-ish units without breaking decimals.

    Protocols often use punctuation inside meaningful values: ``3.0 V``,
    ``1.2.3``, dates, firmware versions, and model names. A tiny state machine is
    clearer and safer here than a broad regular expression.
    """

    units: list[str] = []
    current: list[str] = []
    hard_endings = set("。！？；;!?")

    for index, char in enumerate(line):
        current.append(char)
        should_split = False
        if char in hard_endings:
            should_split = True
        elif char == ".":
            previous_char = line[index - 1] if index > 0 else ""
            next_char = line[index + 1] if index + 1 < len(line) else ""
            if previous_char.isdigit() and next_char.isdigit():
                should_split = False
            elif next_char and not next_char.isspace():
                should_split = False
            else:
                should_split = True

        if should_split:
            unit = "".join(current).strip()
            if unit:
                units.append(unit)
            current = []

    tail = "".join(current).strip()
    if tail:
        units.append(tail)
    return units


def _first_units(text: str, max_snippets: int) -> list[str]:
    """Return leading snippets for added or deleted whole sections."""

    return [truncate(unit) for unit in _split_units(text)[:max_snippets]]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    """Remove repeated snippets without changing the visible order."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _change_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Sort by new-page location first, then deleted old-page location."""

    section = change.new_section or change.old_section
    page = section.start_page if section else 0
    type_order = {"modified": 0, "added": 1, "deleted": 2, "unchanged": 3}
    return (page, type_order.get(change.change_type, 9), change.report_location)
