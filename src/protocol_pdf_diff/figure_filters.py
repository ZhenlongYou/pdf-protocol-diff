"""Reader-only filtering for standalone Figure captions and diagram text.

Raw extraction and audit data remain untouched. These helpers only prevent
source-image labels from being presented as if they were prose requirements.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from .comparison_session import memoize_comparison
from .pdf_extract import (
    _figure_caption_is_identifier_only,
    _looks_like_figure_caption,
)
from .text_utils import compact_inline

_PROSE_OR_REQUIREMENT_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|being|been|"
    r"show(?:s|n|ed|ing)?|illustrates?|depicts?|describes?|"
    r"defin(?:e|es|ed|ing)|specifies?|contains?|"
    r"lists?|measure(?:s|d)?|capture(?:s|d|ing)?|require(?:s|d)?|use(?:s|d)?|meet(?:s)?|"
    r"provide(?:s|d)?|preserve(?:s|d)?|apply|applies|applied)\b"
    r"|应|必须|不得|要求|规定|显示|说明|描述|定义"
)
_PROSE_SENTENCE_OPENER_RE = re.compile(
    r"(?i)^(?:a|an|all|any|each|either|every|it|neither|one|the|these|"
    r"this|those|we|recommended)$"
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
    *,
    allow_interleaved_prefix: bool = True,
) -> str:
    """Remove prefixes proven by individual coordinate-owned visual crops."""

    compact = compact_inline(value)
    if not compact or not source_texts:
        return compact
    if not any(character.isalnum() for character in compact):
        return ""
    # One crop must prove one contiguous removal.  Never merge occurrence
    # budgets from several Figures: two unrelated images cannot jointly erase
    # an ordinary sentence that happens to reuse words from both.
    remaining = compact
    changed = False
    # A single crop may be emitted as several consecutive OCR runs.  Reusing
    # that same crop is safe because every iteration must shorten the value;
    # a hard bound protects against malformed tokenization.
    for _iteration in range(max(4, len(source_texts) * 8)):
        if not remaining:
            return ""
        candidates: list[tuple[int, str, int]] = []
        for source_index, source_text in enumerate(source_texts):
            prefix_candidate = _strip_one_coordinate_figure_prefix(
                remaining,
                source_text,
                allow_interleaved_prefix=allow_interleaved_prefix,
            )
            suffix_candidate = _strip_one_coordinate_visual_suffix(
                remaining,
                source_text,
            )
            for candidate in (prefix_candidate, suffix_candidate):
                if candidate == remaining:
                    continue
                candidates.append(
                    (len(remaining) - len(candidate), candidate, source_index)
                )
        if not candidates:
            break
        _removed_chars, remaining, source_index = max(candidates, key=lambda item: item[0])
        changed = True
        remaining = remaining.strip(" \t\n,;:|/\\-–—")
        if not remaining:
            return ""
        if _coordinate_value_starts_with_prose(remaining):
            return remaining
    return remaining if changed else compact


@memoize_comparison(maxsize=256)
def _strip_one_coordinate_figure_prefix(
    value: str,
    source_text: str,
    *,
    allow_interleaved_prefix: bool,
) -> str:
    """Strip a whole value or one prefix using exactly one Figure crop."""

    observed = _figure_text_tokens(value)
    source = _figure_text_tokens(source_text)
    if not observed or not source:
        return value
    source_canonical = _figure_text_canonical(source_text)
    first_token = observed[0][0]
    if (
        _coordinate_value_starts_with_prose(value)
        and (
            bool(_PROSE_SENTENCE_OPENER_RE.fullmatch(first_token))
            or bool(re.fullmatch(r"\d+[.)]?", first_token))
        )
    ):
        return value  # 明确句首或编号步骤优先；后文与 Figure 重词不能裁掉真实主语。
    # A Figure label wall can be followed by a complete sentence.  Prove and
    # remove that coordinate-owned prefix before applying the whole-value
    # sentence guard; otherwise the later verb in the sentence protects the
    # labels too (for example ``Time Undershoot VMA 1 All ... are ...``).
    prose_start = _coordinate_mixed_prose_start(value, observed) if allow_interleaved_prefix else None
    if allow_interleaved_prefix and prose_start is not None:
        # A bare numeric Figure label immediately before ``All ...`` can look
        # like a numbered prose item.  Consume it only when the same crop owns
        # that number; punctuation-bearing ``2. Capture ...`` remains prose.
        numeric_label = re.match(r"(\d+)\s+(?=[A-Z])", value[prose_start:])
        if numeric_label is not None:
            candidate_start = prose_start + numeric_label.end()
            candidate_prefix = value[:candidate_start]
            candidate_canonical = _figure_text_canonical(candidate_prefix)
            candidate_coverage = sum(
                (Counter(candidate_canonical) & Counter(source_canonical)).values()
            ) / max(len(candidate_canonical), 1)
            if candidate_coverage >= 0.94:
                prose_start = candidate_start
        prefix = value[:prose_start]
        prefix_canonical = _figure_text_canonical(prefix)
        prefix_coverage = sum(
            (Counter(prefix_canonical) & Counter(source_canonical)).values()
        ) / max(len(prefix_canonical), 1)
        if (
            len(prefix_canonical) >= 8
            and prefix_coverage >= 0.94
            and not _PROSE_OR_REQUIREMENT_VERB_RE.search(prefix)
        ):
            return value[prose_start:]
    if _coordinate_value_starts_with_prose(value):
        return value
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
    observed_letters = "".join(
        character for character in observed_canonical if character.isalpha()
    )
    source_letters = "".join(
        character for character in source_canonical if character.isalpha()
    )
    if (
        len(observed_canonical) >= 20
        and character_coverage >= 0.94
        and sequence_similarity >= 0.65
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
    ):
        return ""
    if (
        len(observed_letters) >= 6
        and observed_letters in source_letters
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

    # Table headers and diagram labels may be emitted column-first while the
    # bbox source is row-first (for example ``gDC2 gDC Location ...`` versus
    # ``g g Location DC2 DC ...``).  Near-complete character ownership by one
    # crop is sufficient only for a non-sentence fragment; raw audit data stays
    # untouched even when the reader layer omits it.
    if (
        len(observed_canonical) >= 4
        and character_coverage >= 0.98
        and len(source_canonical) <= len(observed_canonical) * 8
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
    ):
        return ""

    # A lone Figure/Table label such as ``TP1a`` or ``HCB`` is not a useful
    # prose delta when an individual coordinate crop contains that exact token.
    if (
        len(observed) <= 2
        and matched_count == len(observed)
        and len(source) >= 5
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
        prefix_coverage = sum(
            (
                Counter(prefix_canonical)
                & Counter(source_canonical)
            ).values()
        ) / max(len(prefix_canonical), 1)
        if (
            token_index + 1 < 3
            or len(prefix_canonical) < 8
            or (
                prefix_canonical not in source_canonical
                and (
                    not allow_interleaved_prefix
                    or prefix_coverage < 0.98
                )
            )
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


@memoize_comparison(maxsize=256)
def _strip_one_coordinate_visual_suffix(value: str, source_text: str) -> str:
    """Strip a non-prose tail proven by one crop after a sentence boundary."""

    observed = _figure_text_tokens(value)
    source_canonical = _figure_text_canonical(source_text)
    if len(observed) < 4 or not source_canonical:
        return value
    for token_index in range(1, len(observed) - 2):
        _token, start, _end = observed[token_index]
        prose_prefix = value[:start].rstrip()
        if not prose_prefix.endswith((".", ":", ";")):
            continue
        suffix = value[start:]
        suffix_canonical = _figure_text_canonical(suffix)
        if len(suffix_canonical) < 8 or _PROSE_OR_REQUIREMENT_VERB_RE.search(suffix):
            continue
        character_coverage = sum(
            (Counter(suffix_canonical) & Counter(source_canonical)).values()
        ) / max(len(suffix_canonical), 1)
        if (
            suffix_canonical in source_canonical
            or (
                character_coverage >= 0.98
                and len(source_canonical) <= len(suffix_canonical) * 8
            )
        ):
            return prose_prefix
    return value


def _figure_text_tokens(value: str) -> list[tuple[str, int, int]]:
    """Tokenize Figure evidence while retaining source spans for prefix cuts."""

    tokens: list[tuple[str, int, int]] = []
    for match in re.finditer(r"[\w.±%+-]+", value, flags=re.UNICODE):
        normalized = unicodedata.normalize("NFKC", match.group(0)).casefold()
        if normalized:
            tokens.append((normalized, match.start(), match.end()))
    return tokens


@memoize_comparison(maxsize=1024)
def _figure_text_canonical(value: str) -> str:
    """Normalize spacing-fragmented Figure text for same-crop containment."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _coordinate_mixed_prose_start(
    value: str,
    tokens: list[tuple[str, int, int]],
) -> int | None:
    """Find a grammatical prose suffix after at least three visual tokens."""

    for token_index, (_token, start, _end) in enumerate(tokens):
        if token_index < 3:
            continue
        if not _coordinate_tokens_start_prose(value, tokens, token_index):
            continue
        return start
    return None


def _coordinate_value_starts_with_prose(value: str) -> bool:
    """Protect a grammatical suffix before reusing the same visual crop."""

    tokens = _figure_text_tokens(value)
    return bool(tokens and _coordinate_tokens_start_prose(value, tokens, 0))


def _coordinate_tokens_start_prose(
    value: str,
    tokens: list[tuple[str, int, int]],
    token_index: int,
) -> bool:
    """Recognize a generic sentence start without protocol-specific labels."""

    at_value_start = token_index == 0
    token, start, _end = tokens[token_index]
    if (
        re.fullmatch(r"\d+[.)]?", token)
        and token_index + 1 < len(tokens)
    ):
        token_index += 1
        token, start, _end = tokens[token_index]
        # Numbered procedure steps are prose only when the marker is followed
        # by an ordinary capitalized sentence.  A numeric axis or table value
        # therefore cannot obtain this protection by itself.
    first_character = value[start : start + 1]
    if not first_character or not (
        first_character.isupper()
        or "\u3400" <= first_character <= "\u9fff"
    ):
        return False
    short_end = tokens[min(len(tokens), token_index + 3) - 1][2]
    long_end = tokens[min(len(tokens), token_index + 24) - 1][2]
    has_immediate_verb = bool(
        _PROSE_OR_REQUIREMENT_VERB_RE.search(value[start:short_end])
    )
    has_nearby_verb = bool(
        _PROSE_OR_REQUIREMENT_VERB_RE.search(value[start:long_end])
    )
    return has_immediate_verb or (
        (
            bool(_PROSE_SENTENCE_OPENER_RE.fullmatch(token))
            or (
                at_value_start
                and bool(re.search(r"[.!?。！？]\s*$", value))
            )
        )
        and has_nearby_verb
    )


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
