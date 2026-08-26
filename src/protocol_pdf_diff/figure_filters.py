"""Reader-only filtering for standalone Figure captions and diagram text.

Raw extraction and audit data remain untouched. These helpers only prevent
source-image labels from being presented as if they were prose requirements.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from .pdf_extract import (
    _figure_caption_is_identifier_only,
    _looks_like_figure_caption,
)
from .text_utils import compact_inline

_PROSE_OR_REQUIREMENT_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|being|been|"
    r"shows?|illustrates?|depicts?|describes?|defines?|specifies?|contains?|"
    r"lists?|measure(?:s|d)?|require(?:s|d)?|use(?:s|d)?|meet(?:s)?|"
    r"provide(?:s|d)?|preserve(?:s|d)?|apply|applies|applied)\b"
    r"|应|必须|不得|要求|规定|显示|说明|描述|定义"
)
def filter_figure_visual_snippets(values: list[str] | tuple[str, ...]) -> list[str]:
    """Remove Figure labels plus adjacent caption/diagram fragments.

    A normal sentence that references a Figure is retained by the extractor's
    existing classifier. After a naked ``Figure N.`` label, every consecutive
    non-prose fragment belongs to the source image, including terse VMA/axis
    labels and formulas. The first genuine prose sentence closes the visual run.
    """

    observed = [(value, compact_inline(value)) for value in values]
    observed = [(value, compact) for value, compact in observed if compact]
    kept: list[str] = []
    index = 0
    while index < len(observed):
        value, compact = observed[index]
        if _is_combined_figure_visual_fragment(compact):
            index += 1
            continue
        if not _figure_caption_is_identifier_only(compact):
            kept.append(value)
            index += 1
            continue

        index += 1  # 独立 Figure 编号本身是定位标签，不作为技术变化展示。
        while index < len(observed):
            candidate, candidate_compact = observed[index]
            if _figure_caption_is_identifier_only(candidate_compact):
                index += 1
                continue
            if _is_figure_visual_prose_boundary(candidate_compact):
                kept.append(candidate)
                index += 1
                break
            index += 1
    return kept


def is_figure_visual_pair(old: str, new: str) -> bool:
    """Return True only when both sides are standalone Figure visual text."""

    return (
        not filter_figure_visual_snippets([old])
        and not filter_figure_visual_snippets([new])
    )


def strip_coordinate_owned_figure_fragment(
    value: str,
    source_texts: tuple[str, ...] | list[str],
) -> str:
    """Remove only text proven to lie inside a rendered Figure crop.

    PDF reading order often drops the caption prefix and emits a wall of box,
    axis, and connector labels as ordinary prose.  The Figure crop is already
    coordinate-backed, so its words are stronger ownership evidence than a
    protocol-specific vocabulary list.  A mixed block may continue with real
    prose; in that case only a sufficiently long coordinate-owned prefix is
    removed.
    """

    return strip_coordinate_owned_visual_fragment(value, source_texts)


def strip_coordinate_owned_visual_fragment(
    value: str,
    source_texts: tuple[str, ...] | list[str],
) -> str:
    """Remove prefixes proven by individual coordinate-owned visual crops."""

    compact = compact_inline(value)
    if not compact or not source_texts:
        return compact
    # One crop must prove one contiguous removal.  Never merge occurrence
    # budgets from several Figures: two unrelated images cannot jointly erase
    # an ordinary sentence that happens to reuse words from both.
    remaining = compact
    used_sources: set[int] = set()
    while remaining:
        candidates: list[tuple[int, str, int]] = []
        for source_index, source_text in enumerate(source_texts):
            if source_index in used_sources:
                continue
            candidate = _strip_one_coordinate_figure_prefix(remaining, source_text)
            if candidate != remaining:
                candidates.append(
                    (len(remaining) - len(candidate), candidate, source_index)
                )
        if not candidates:
            break
        _removed_chars, remaining, source_index = max(candidates, key=lambda item: item[0])
        used_sources.add(source_index)
        remaining = remaining.strip(" \t\n,;:|/\\-–—")
        if not remaining:
            return ""
        if _is_figure_visual_prose_boundary(remaining):
            return remaining
    return compact


def _strip_one_coordinate_figure_prefix(value: str, source_text: str) -> str:
    """Strip a whole value or one prefix using exactly one Figure crop."""

    observed = _figure_text_tokens(value)
    source = _figure_text_tokens(source_text)
    if not observed or not source:
        return value
    source_canonical = _figure_text_canonical(source_text)
    observed_canonical = _figure_text_canonical(value)
    if (
        len(observed_canonical) >= 8
        and observed_canonical in source_canonical
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
    ):
        return ""
    character_coverage = sum(
        (Counter(observed_canonical) & Counter(source_canonical)).values()
    ) / max(len(observed_canonical), 1)
    sequence_similarity = SequenceMatcher(
        None,
        observed_canonical,
        source_canonical,
        autojunk=False,
    ).ratio()
    if (
        len(observed_canonical) >= 20
        and character_coverage >= 0.94
        and sequence_similarity >= 0.65
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
    ):
        return ""

    source_counts = Counter(token for token, _start, _end in source)
    observed_counts = Counter(token for token, _start, _end in observed)
    matched_count = sum(
        min(count, source_counts[token])
        for token, count in observed_counts.items()
    )
    if (
        matched_count >= 3
        and matched_count / len(observed) >= 0.86
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
    ):
        return ""

    # PDF text layers may split a reversed axis label into single letters while
    # the section assembler joins them again.  Whitespace-insensitive character
    # containment restores that same-crop proof without protocol vocabulary.
    for token_index in range(len(observed) - 1, -1, -1):
        _token, _start, end = observed[token_index]
        prefix = value[:end]
        prefix_canonical = _figure_text_canonical(prefix)
        if (
            token_index + 1 < 3
            or len(prefix_canonical) < 8
            or prefix_canonical not in source_canonical
            or _PROSE_OR_REQUIREMENT_VERB_RE.search(prefix)
        ):
            continue
        return value[end:]

    remaining = source_counts.copy()
    prefix_count = 0
    prefix_end = 0
    for token, _start, end in observed:
        if remaining[token] <= 0:
            break
        remaining[token] -= 1
        prefix_count += 1
        prefix_end = end
    prefix = value[:prefix_end]
    if (
        prefix_count >= 5
        and prefix_count / len(observed) >= 0.25
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(prefix)
    ):
        return value[prefix_end:]
    return value


def _figure_text_tokens(value: str) -> list[tuple[str, int, int]]:
    """Tokenize Figure evidence while retaining source spans for prefix cuts."""

    tokens: list[tuple[str, int, int]] = []
    for match in re.finditer(r"[\w.±%+-]+", value, flags=re.UNICODE):
        normalized = unicodedata.normalize("NFKC", match.group(0)).casefold()
        if normalized:
            tokens.append((normalized, match.start(), match.end()))
    return tokens


def _figure_text_canonical(value: str) -> str:
    """Normalize spacing-fragmented Figure text for same-crop containment."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _is_combined_figure_visual_fragment(value: str) -> bool:
    """Recognize one extracted block containing a Figure caption plus labels."""

    return bool(
        not _figure_caption_is_identifier_only(value)
        and _looks_like_figure_caption(value)
    )


def _is_figure_visual_prose_boundary(value: str) -> bool:
    """Return whether a fragment clearly resumes ordinary document prose."""

    return bool(
        len(value) > 500
        or _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
        or re.match(r"^(?:\d+(?:\.\d+)+|[A-Z]?\d+[.)])\s+", value)
        or re.search(r"[.!?。！？]\s*$", value)
    )
