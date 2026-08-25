"""Reader-only filtering for standalone Figure captions and diagram text.

Raw extraction and audit data remain untouched. These helpers only prevent
source-image labels from being presented as if they were prose requirements.
"""

from __future__ import annotations

import re

from .pdf_extract import _figure_caption_is_identifier_only
from .text_utils import compact_inline

_PROSE_OR_REQUIREMENT_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|being|been|"
    r"shows?|illustrates?|depicts?|describes?|defines?|specifies?|contains?|"
    r"lists?|measure(?:s|d)?|require(?:s|d)?|use(?:s|d)?|meet(?:s)?|"
    r"provide(?:s|d)?|preserve(?:s|d)?|apply|applies|applied)\b"
    r"|应|必须|不得|要求|规定|显示|说明|描述|定义"
)
_FIGURE_PREFIX_RE = re.compile(
    r"(?i)^\s*Figure\s+[A-Z]?\d+(?:[-.]\d+)*(?:\s*[.:])?\s*(?P<tail>.*)$"
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


def _is_combined_figure_visual_fragment(value: str) -> bool:
    """Recognize one extracted block containing a Figure caption plus labels."""

    match = _FIGURE_PREFIX_RE.match(value)
    if match is None or _figure_caption_is_identifier_only(value):
        return False
    tail = match.group("tail").strip()
    return bool(tail) and not _is_figure_visual_prose_boundary(tail)


def _is_figure_visual_prose_boundary(value: str) -> bool:
    """Return whether a fragment clearly resumes ordinary document prose."""

    return bool(
        len(value) > 500
        or _PROSE_OR_REQUIREMENT_VERB_RE.search(value)
        or re.match(r"^(?:\d+(?:\.\d+)+|[A-Z]?\d+[.)])\s+", value)
        or re.search(r"[.!?。！？]\s*$", value)
    )
