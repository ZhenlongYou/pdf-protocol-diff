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


def filter_figure_visual_snippets(values: list[str] | tuple[str, ...]) -> list[str]:
    """Remove Figure labels plus adjacent caption/diagram fragments.

    A normal sentence that references a Figure is retained by the extractor's
    existing classifier. After a naked ``Figure N.`` label, only consecutive
    non-sentence fragments are removed; the first prose requirement closes the
    visual run and is preserved.
    """

    observed = [(value, compact_inline(value)) for value in values]
    observed = [(value, compact) for value, compact in observed if compact]
    kept: list[str] = []
    index = 0
    while index < len(observed):
        value, compact = observed[index]
        if not _figure_caption_is_identifier_only(compact):
            kept.append(value)
            index += 1
            continue

        index += 1  # 独立 Figure 编号本身是定位标签，不作为技术变化展示。
        fragment_start = index
        while index < len(observed) and _is_following_figure_visual_fragment(
            observed[index][1]
        ):
            index += 1
        if index < len(observed):
            kept.extend(
                value for value, _compact in observed[fragment_start:index]
            )  # 后面还有正常正文时，短标签缺少坐标证据，必须保守保留。
    return kept


def is_figure_visual_pair(old: str, new: str) -> bool:
    """Return True only when both sides are standalone Figure visual text."""

    return (
        not filter_figure_visual_snippets([old])
        and not filter_figure_visual_snippets([new])
    )


def _is_following_figure_visual_fragment(value: str) -> bool:
    """Recognize a split caption/diagram line after a naked Figure label."""

    if len(value) > 500:
        return False
    if _PROSE_OR_REQUIREMENT_VERB_RE.search(value):
        return False
    if re.match(r"^(?:\d+(?:\.\d+)+|[A-Z]?\d+[.)])\s+", value):
        return False  # A numbered clause/list item starts real document structure.
    return not re.search(
        r"[.!?。！？]\s*$",
        value,
    )  # A complete sentence after the caption begins prose again.
