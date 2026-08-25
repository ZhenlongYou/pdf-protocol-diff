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

    kept: list[str] = []
    inside_figure_visual = False
    for value in values:
        compact = compact_inline(value)
        if not compact:
            continue
        if _figure_caption_is_identifier_only(compact):
            inside_figure_visual = True
            continue
        if inside_figure_visual and _is_following_figure_visual_fragment(compact):
            continue
        inside_figure_visual = False
        kept.append(value)
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
