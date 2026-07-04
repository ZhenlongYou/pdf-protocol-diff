"""Section matching and difference summarization.

The comparison is designed for practical protocol review rather than formal
legal redlining. It first uses stable section numbers when possible, then falls
back to text similarity for renamed or renumbered sections. The report should be
treated as a review accelerator: important changes are surfaced with page and
section context, but final sign-off should still inspect the source PDFs.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
from .text_utils import normalize_for_similarity, normalize_line


@dataclass(frozen=True)
class _DeltaCandidate:
    """One reportable snippet before max-snippet limiting is applied."""

    kind: str
    order: int
    priority: int
    text: str = ""
    pair: SnippetPair | None = None


def run_diff(old_pdf: str | Path, new_pdf: str | Path, options: DiffOptions) -> DiffResult:
    """Run the complete extraction, sectioning, and comparison pipeline."""

    old_extraction = extract_pdf_text(
        old_pdf,
        start_page=options.old_start_page,
        end_page=options.old_end_page,
    )
    new_extraction = extract_pdf_text(
        new_pdf,
        start_page=options.new_start_page,
        end_page=options.new_end_page,
    )
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
        old_total_pages=_source_page_count(old_extraction),
        new_total_pages=_source_page_count(new_extraction),
        old_selected_start_page=_selected_start_page(old_extraction),
        old_selected_end_page=_selected_end_page(old_extraction),
        new_selected_start_page=_selected_start_page(new_extraction),
        new_selected_end_page=_selected_end_page(new_extraction),
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
            heading_pair = (
                SnippetPair(
                    old=f"章节标题: {old_section.heading}",
                    new=f"章节标题: {new_section.heading}",
                )
                if _section_heading_changed(old_section, new_section)
                else None
            )
            added, removed, replaced, omitted_count = _summarize_text_delta(
                old_section.body,
                new_section.body,
                max_snippets=options.max_snippets_per_section,
                leading_replacement=heading_pair,
            )
            changes.append(
                SectionChange(
                    change_type="modified",
                    old_section=old_section,
                    new_section=new_section,
                    similarity=similarity,
                    added_snippets=added,
                    removed_snippets=removed,
                    replaced_snippets=replaced,
                    omitted_snippet_count=omitted_count,
                )
            )
        elif new_section:
            added_snippets, omitted_count = _first_units(
                new_section.body,
                options.max_snippets_per_section,
            )
            changes.append(
                SectionChange(
                    change_type="added",
                    old_section=None,
                    new_section=new_section,
                    similarity=0.0,
                    added_snippets=added_snippets,
                    omitted_snippet_count=omitted_count,
                )
            )
        elif old_section:
            removed_snippets, omitted_count = _first_units(
                old_section.body,
                options.max_snippets_per_section,
            )
            changes.append(
                SectionChange(
                    change_type="deleted",
                    old_section=old_section,
                    new_section=None,
                    similarity=0.0,
                    removed_snippets=removed_snippets,
                    omitted_snippet_count=omitted_count,
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
            similarity = max(
                _similarity(
                    old_sections[exact_match].comparable_text,
                    new_section.comparable_text,
                ),
                _review_similarity(
                    old_sections[exact_match].comparable_text,
                    new_section.comparable_text,
                ),
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
        similarity = max(
            _similarity(
                old_sections[old_index].comparable_text,
                new_sections[new_index].comparable_text,
            ),
            _review_similarity(
                old_sections[old_index].comparable_text,
                new_sections[new_index].comparable_text,
            ),
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

    if _both_page_fallback_sections(old_section, new_section):
        return _similarity(old_section.body, new_section.body)

    title_score = _review_similarity(old_section.title, new_section.title)
    location_score = _review_similarity(old_section.location, new_section.location)
    text_score = max(
        _review_similarity(
            old_section.comparable_text[:4000],
            new_section.comparable_text[:4000],
        ),
        _similarity(
            old_section.comparable_text[:4000],
            new_section.comparable_text[:4000],
        ),
    )
    return max(title_score * 0.85 + text_score * 0.15, location_score * 0.4 + text_score * 0.6)


def _review_similarity(left: str, right: str) -> float:
    """Return a similarity score after display-only punctuation is normalized."""

    left_norm = _review_unit_key(left)
    right_norm = _review_unit_key(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return difflib.SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


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

    body_same = _review_unit_key(old_section.body) == _review_unit_key(new_section.body)
    return body_same and not _section_heading_changed(old_section, new_section)


def _section_heading_changed(old_section: Section, new_section: Section) -> bool:
    """Detect same-number section title changes that would otherwise be missed."""

    if _both_page_fallback_sections(old_section, new_section):
        return False
    return _review_unit_key(old_section.heading) != _review_unit_key(new_section.heading)


def _both_page_fallback_sections(old_section: Section, new_section: Section) -> bool:
    """Return True when both sections are synthetic no-heading page chunks."""

    return old_section.section_id.startswith("P") and new_section.section_id.startswith("P")


def _summarize_text_delta(
    old_text: str,
    new_text: str,
    max_snippets: int,
    leading_replacement: SnippetPair | None = None,
) -> tuple[list[str], list[str], list[SnippetPair], int]:
    """Create compact added/removed/replaced snippets for one section."""

    old_units = _split_units(old_text)
    new_units = _split_units(new_text)
    old_keys = [_review_unit_key(unit) for unit in old_units]
    new_keys = [_review_unit_key(unit) for unit in new_units]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)

    candidates: list[_DeltaCandidate] = []
    candidate_order = 0

    def add_candidate(
        kind: str,
        *,
        text: str = "",
        pair: SnippetPair | None = None,
        priority_values: tuple[str, ...],
    ) -> None:
        nonlocal candidate_order
        candidates.append(
            _DeltaCandidate(
                kind=kind,
                order=candidate_order,
                priority=_substantive_priority(*priority_values),
                text=text,
                pair=pair,
            )
        )
        candidate_order += 1

    if leading_replacement:
        add_candidate(
            "replaced",
            pair=leading_replacement,
            priority_values=(leading_replacement.old, leading_replacement.new),
        )

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            for unit in new_units[new_start:new_end]:
                add_candidate(
                    "added",
                    text=_report_unit(unit),
                    priority_values=(unit,),
                )
        elif tag == "delete":
            for unit in old_units[old_start:old_end]:
                add_candidate(
                    "removed",
                    text=_report_unit(unit),
                    priority_values=(unit,),
                )
        elif tag == "replace":
            old_block_units = old_units[old_start:old_end]
            new_block_units = new_units[new_start:new_end]
            if len(old_block_units) == len(new_block_units):
                for old_unit, new_unit in zip(old_block_units, new_block_units, strict=True):
                    if _review_unit_key(old_unit) == _review_unit_key(new_unit):
                        continue
                    add_candidate(
                        "replaced",
                        pair=SnippetPair(old=_report_unit(old_unit), new=_report_unit(new_unit)),
                        priority_values=(old_unit, new_unit),
                    )
            else:
                old_block = _best_changed_block(old_block_units, new_block_units)
                new_block = _best_changed_block(new_block_units, old_block_units)
                if _review_unit_key(old_block) != _review_unit_key(new_block):
                    add_candidate(
                        "replaced",
                        pair=SnippetPair(old=_report_unit(old_block), new=_report_unit(new_block)),
                        priority_values=(old_block, new_block),
                    )

    return _materialize_delta_candidates(candidates, max_snippets)


def _split_units(text: str) -> list[str]:
    """Split text into review-sized units.

    Sentence-like chunks work better for Chinese protocols than word-level diffs
    because Chinese text has no guaranteed spaces. If extraction returns very
    long lines, the fallback chunks by length to keep report snippets readable.
    """

    raw_units: list[str] = []
    for block in _merge_wrapped_lines(text):
        raw_units.extend(_split_line_preserving_numbers(block))

    units: list[str] = []
    for unit in raw_units:
        if len(unit) <= 720:
            units.append(unit)
            continue
        units.extend(_split_long_unit(unit))
    return units


def _merge_wrapped_lines(text: str) -> list[str]:
    """Merge PDF line wraps into review-sized sentences or procedure steps."""

    blocks: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue
        if _starts_new_review_block(line):
            if current:
                blocks.append(current)
            current = line
            continue
        if not current:
            current = line
        elif _ends_review_sentence(current) and not _is_standalone_list_marker(current):
            blocks.append(current)
            current = line
        elif _looks_like_new_sentence_after_linebreak(current, line):
            blocks.append(current)
            current = line
        else:
            current = _join_wrapped_line(current, line)
    if current:
        blocks.append(current)
    return blocks


def _starts_new_review_block(line: str) -> bool:
    """Return True when a line starts a new list item or procedure step."""

    patterns = (
        r"^\d{1,3}[.)](?:\s+\S.*|\s*)$",
        r"^[a-zA-Z][.)](?:\s+\S.*|\s*)$",
        r"^[•●⚫-]\s+\S",
        r"^Note:\s+",
    )
    return any(re.match(pattern, line) for pattern in patterns)


def _join_wrapped_line(left: str, right: str) -> str:
    """Join one PDF-wrapped line while repairing common hyphen breaks."""

    if left.endswith("-") and right and right[0].islower():
        return left[:-1] + right
    return f"{left} {right}"


def _ends_review_sentence(value: str) -> bool:
    """Return True when a buffered line already looks like a complete sentence."""

    return value.rstrip().endswith((".", "。", ";", "；", "!", "！", "?", "？"))


def _is_standalone_list_marker(value: str) -> bool:
    """Keep markers such as ``a.`` or ``2.`` attached to their following text."""

    return bool(re.fullmatch(r"(?:\d{1,3}|[A-Za-z])[.)]", value.strip()))


def _looks_like_new_sentence_after_linebreak(left: str, right: str) -> bool:
    """Avoid gluing adjacent extracted sentences when punctuation is missing."""

    if _is_standalone_list_marker(left) or left.endswith("-"):
        return False
    first = right[:1]
    return bool(len(left) <= 60 and first and first.isascii() and first.isupper())


def _split_long_unit(unit: str, max_chars: int = 720) -> list[str]:
    """Split a very long unit at readable boundaries instead of mid-word."""

    chunks: list[str] = []
    remaining = unit.strip()
    while len(remaining) > max_chars:
        cut = max(
            remaining.rfind(". ", 0, max_chars),
            remaining.rfind("; ", 0, max_chars),
            remaining.rfind("。", 0, max_chars),
            remaining.rfind("；", 0, max_chars),
        )
        if cut < max_chars // 2:
            cut = remaining.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        chunk = remaining[: cut + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


_NUMBER_TOKEN_RE = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
_REVIEW_TOKEN_RE = re.compile(
    r"<=|>=|≤|≥|(?<!-)[<>](?!-)|="
    r"|[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
    r"|[a-zµμ]+[a-z0-9µμ]*(?:[-_/][a-z0-9µμ]+)*|[\u4e00-\u9fff]+"
)


def _review_unit_key(value: str) -> str:
    """Normalize a unit for deciding whether a visible diff is substantive.

    The key ignores case, whitespace, and sentence punctuation, but keeps
    meaningful numeric tokens intact. That prevents false equivalence between
    values such as ``1.0 ps`` and ``10 ps`` while still suppressing PDF wrapping
    and comma/period noise.
    """

    normalized = normalize_for_similarity(value)
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("≤", "<=").replace("≥", ">=")
    normalized = re.sub(r"-\s*[<>]\s*", " ", normalized)
    normalized = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", normalized)
    normalized = re.sub(r"\b10\s+([0-9])\b", r"10\1", normalized)
    normalized = re.sub(r"(?<=[a-z])[-‐‑](?=[a-z])", "", normalized)
    normalized = re.sub(r"\bpreset\s*([0-9]+)\b", r"p\1", normalized)
    return " ".join(_canonical_review_token(token) for token in _REVIEW_TOKEN_RE.findall(normalized))


def _canonical_review_token(token: str) -> str:
    """Canonicalize token values only where protocol meaning is preserved."""

    if not _NUMBER_TOKEN_RE.fullmatch(token):
        return token
    sign = "-" if token.startswith("-") else ""
    body = token.lstrip("+-")
    if body.startswith("."):
        body = f"0{body}"
    try:
        value = Decimal(body)
    except InvalidOperation:
        return token
    if value == 0:
        sign = ""
    numeric = format(value.normalize(), "f")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    return f"{sign}{numeric}"


def _report_unit(value: str) -> str:
    """Return a human-readable snippet without odd mid-sentence ellipses."""

    readable = " ".join(_split_long_unit(value, max_chars=1200))
    if len(readable) <= 1400:
        return readable
    cut = max(
        readable.rfind(". ", 0, 1200),
        readable.rfind("; ", 0, 1200),
        readable.rfind("。", 0, 1200),
        readable.rfind("；", 0, 1200),
    )
    if cut < 600:
        cut = readable.rfind(" ", 0, 1200)
    if cut < 600:
        cut = 1200
    return readable[: cut + 1].strip() + " [片段过长，已截断；请见源 PDF 对应页]"


def _best_changed_block(candidate_units: list[str], other_units: list[str]) -> str:
    """Pick the most useful part of an unequal block for a replacement snippet."""

    other_keys = {_review_unit_key(unit) for unit in other_units}
    changed = [unit for unit in candidate_units if _review_unit_key(unit) not in other_keys]
    return " ".join(changed or candidate_units)


def _materialize_delta_candidates(
    candidates: list[_DeltaCandidate],
    max_snippets: int,
) -> tuple[list[str], list[str], list[SnippetPair], int]:
    """Apply snippet limits after all meaningful differences have been scanned."""

    unique_candidates = _dedupe_candidates(candidates)
    if max_snippets <= 0:
        return [], [], [], len(unique_candidates)

    if len(unique_candidates) <= max_snippets:
        selected = unique_candidates
        omitted_count = 0
    else:
        prioritized = sorted(unique_candidates, key=lambda item: (-item.priority, item.order))
        selected = sorted(prioritized[:max_snippets], key=lambda item: item.order)
        omitted_count = len(unique_candidates) - len(selected)

    added: list[str] = []
    removed: list[str] = []
    replaced: list[SnippetPair] = []
    for candidate in selected:
        if candidate.kind == "added":
            added.append(candidate.text)
        elif candidate.kind == "removed":
            removed.append(candidate.text)
        elif candidate.kind == "replaced" and candidate.pair:
            replaced.append(candidate.pair)
    return added, removed, replaced, omitted_count


def _dedupe_candidates(candidates: list[_DeltaCandidate]) -> list[_DeltaCandidate]:
    """Remove duplicate snippets while preserving first occurrence and priority."""

    by_key: dict[tuple[str, str], _DeltaCandidate] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        existing = by_key.get(key)
        if existing is None or candidate.priority > existing.priority:
            by_key[key] = candidate
    return sorted(by_key.values(), key=lambda item: item.order)


def _candidate_key(candidate: _DeltaCandidate) -> tuple[str, str]:
    """Build a stable dedupe key for one candidate."""

    if candidate.pair:
        return (candidate.kind, f"{candidate.pair.old}\n---\n{candidate.pair.new}")
    return (candidate.kind, candidate.text)


def _substantive_priority(*values: str) -> int:
    """Rank protocol-risky deltas above ordinary wording changes."""

    joined = "\n".join(values)
    if _has_numeric_token(joined):
        return 3
    if _has_identifier_token(joined):
        return 2
    return 1


def _has_numeric_token(value: str) -> bool:
    """Return True when a snippet contains a number-like protocol value."""

    return bool(_NUMBER_TOKEN_RE.search(value))


def _has_identifier_token(value: str) -> bool:
    """Return True for compact identifiers such as TS1/TS2, P5, or Gen6."""

    normalized = normalize_for_similarity(value)
    return bool(re.search(r"\b[a-z]+[0-9]+(?:[-_/][a-z0-9]+)*\b", normalized))


def _source_page_count(extraction: ExtractionResult) -> int:
    """Return the source PDF page count, inferring it for hand-built tests."""

    if extraction.total_pages:
        return extraction.total_pages
    if not extraction.pages:
        return 0
    return max(page.page_number for page in extraction.pages)


def _selected_start_page(extraction: ExtractionResult) -> int | None:
    """Return the first extracted source page, preserving explicit metadata."""

    if extraction.selected_start_page is not None:
        return extraction.selected_start_page
    if not extraction.pages:
        return None
    return min(page.page_number for page in extraction.pages)


def _selected_end_page(extraction: ExtractionResult) -> int | None:
    """Return the last extracted source page, preserving explicit metadata."""

    if extraction.selected_end_page is not None:
        return extraction.selected_end_page
    if not extraction.pages:
        return None
    return max(page.page_number for page in extraction.pages)


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
            previous_text = line[:index].strip()
            if previous_char.isdigit() and next_char.isdigit():
                should_split = False
            elif (
                re.fullmatch(r"\d{1,3}", previous_text)
                and next_char
                and next_char.isspace()
            ):
                should_split = False
            elif (
                re.fullmatch(r"[A-Za-z]", previous_text)
                and next_char
                and next_char.isspace()
            ):
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


def _first_units(text: str, max_snippets: int) -> tuple[list[str], int]:
    """Return leading snippets for added or deleted whole sections."""

    units = [_report_unit(unit) for unit in _split_units(text)]
    if max_snippets <= 0:
        return [], len(units)
    return units[:max_snippets], max(0, len(units) - max_snippets)


def _change_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Sort by new-page location first, then deleted old-page location."""

    section = change.new_section or change.old_section
    page = section.start_page if section else 0
    type_order = {"modified": 0, "added": 1, "deleted": 2, "unchanged": 3}
    return (page, type_order.get(change.change_type, 9), change.report_location)
