"""Reader-only filtering for standalone Figure captions and diagram text.

Raw extraction and audit data remain untouched. These helpers only prevent
source-image labels from being presented as if they were prose requirements.
"""

from __future__ import annotations

import re

from .pdf_extract import (
    _figure_caption_is_identifier_only,
    _looks_like_figure_caption,
)
from .text_utils import compact_inline

_PROSE_OR_REQUIREMENT_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|being|been|"
    r"show(?:s|n|ed|ing)?|illustrates?|depicts?|describes?|"
    r"defin(?:e|es|ed|ing)|specifies?|contains?|"
    r"includes?|consists?|lists?|measure(?:s|d)?|capture(?:s|d|ing)?|require(?:s|d)?|use(?:s|d)?|meet(?:s)?|"
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


def figure_crop_owns_whole_tokens(
    value: str,
    source_texts: tuple[str, ...] | list[str],
) -> bool:
    """Return True when ONE Figure crop's text inventory holds every whole token.

    只用于行合并碎片：抽取器会把图内相邻标签合并成 ``Zero line`` 这类片段，
    它在源词流里没有连续有序出现，因此坐标归属证明不到；这里只检查同一张
    Figure 裁图的文本清单是否包含片段的每个整词，不做子串覆盖、不合并多张
    图的词表，也不接受句子形态。调用方必须已经证明双侧 Figure 配对，才能
    把片段从读者差异降级为图示重排。
    """

    compact = compact_inline(value)
    if not compact or not source_texts:
        return False
    if _PROSE_OR_REQUIREMENT_VERB_RE.search(compact):
        return False  # 真实句子（含谓语/条件句）优先保留。
    if _is_figure_visual_prose_boundary(compact):
        return False  # 句末标点、编号步骤或长段正文都按正文处理。
    tokens = re.findall(r"\S+", compact)
    if not tokens or len(tokens) > 6:
        return False  # 只处理短标签碎片，避免吞掉整句改写。
    for source_text in source_texts:
        source_tokens = set(re.findall(r"\S+", compact_inline(source_text)))
        if all(token in source_tokens for token in tokens):
            return True  # 单张裁图必须独立覆盖全部整词。
    return False


def strip_coordinate_owned_figure_fragment(
    value: str,
    source_texts: tuple[str, ...] | list[str],
) -> str:
    """Compatibility wrapper: source strings alone cannot prove occurrence ownership."""

    return strip_coordinate_owned_visual_fragment(value, source_texts)


def strip_coordinate_owned_visual_fragment(
    value: str,
    source_texts: tuple[str, ...] | list[str],
    *,
    allow_interleaved_prefix: bool = True,
) -> str:
    """Text-only callers have no occurrence ownership and cannot delete text.

    Verified word-coordinate intervals are applied by visual_ownership before
    reader projection. Retaining this compatibility entrypoint is conservative
    for callers holding only a crop's text inventory.
    """
    return compact_inline(value)


def _is_combined_figure_visual_fragment(value: str) -> bool:
    """Recognize one extracted block containing a Figure caption plus labels."""

    return bool(
        not _figure_caption_is_identifier_only(value)
        and _looks_like_figure_caption(value)
        and not _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
    )


def _is_figure_visual_prose_boundary(value: str) -> bool:
    """Return whether a fragment clearly resumes ordinary document prose."""

    return bool(
        len(value) > 500
        or _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
        or re.match(r"^(?:\d+(?:\.\d+)+|[A-Z]?\d+[.)])\s+", value)
        or re.search(r"[.!?。！？]\s*$", value)
    )
