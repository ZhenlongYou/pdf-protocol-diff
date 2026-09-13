"""Bounded forward mapping of the actual numbered-formula split operations."""

import re

from .compare import (
    _DISPLAYED_FORMULA_LABEL_RE,
    _is_displayed_formula_review_unit,
    _is_math_continuation_fragment,
    _retain_formula_adjacent_span,
)
from .compare import (
    _prose_outside_displayed_formula as actual,
)


def strip(s, offsets):
    lo = len(s) - len(s.lstrip())
    hi = len(s.rstrip())
    return s[lo:hi], offsets[lo:hi]


def forward(unit):
    if not _is_displayed_formula_review_unit(unit):
        return [{"text": unit, "offsets": list(range(len(unit))), "removed": []}]
    labels = [
        m
        for m in _DISPLAYED_FORMULA_LABEL_RE.finditer(unit)
        if re.search(r"(?i)\bEquation\s*$", unit[: m.start()]) is None
    ]
    spans = []
    cursor = 0
    for m in labels:
        spans.append(strip(unit[cursor : m.start()], list(range(cursor, m.start()))))
        cursor = m.end()
    spans.append(strip(unit[cursor:], list(range(cursor, len(unit)))))
    joined = []
    for s, ix in spans:
        if (
            joined
            and _is_math_continuation_fragment(s)
            and _is_math_continuation_fragment(joined[-1][0])
            and (
                _retain_formula_adjacent_span(joined[-1][0] + " " + s)
                or not any(_retain_formula_adjacent_span(x) for x in (joined[-1][0], s))
            )
        ):
            prev, pi = joined.pop()
            joined.append(strip(prev + " " + s, pi + [None] + ix))
        else:
            joined.append((s, ix))
    out = [
        {
            "text": s,
            "offsets": ix,
            "removed": [
                {"start": m.start(), "end": m.end(), "text": m.group()} for m in labels
            ],
        }
        for s, ix in joined
        if s and _retain_formula_adjacent_span(s)
    ]
    if [x["text"] for x in out] != actual(unit):
        raise ValueError("production_output_drift")
    for x in out:
        if any(
            (i is None and not ch.isspace()) or (i is not None and unit[i] != ch)
            for ch, i in zip(x["text"], x["offsets"])
        ):
            raise ValueError("invalid_forward_origin")
    return out


def normalized(s):
    chars = []
    origins = []
    pending = []
    for i, ch in enumerate(s):
        if ch.isspace():
            pending.append(i)
            continue
        if chars and pending:
            chars.append(" ")
            origins.append(pending[0])
        pending = []
        chars.append(ch)
        origins.append(i)
    return "".join(chars), origins


def bind(unit, section, pages, page_number):
    if not section.start_page <= page_number <= section.end_page:
        raise ValueError("wrong_section_page")
    source = dict(section.page_bodies)
    expected = set(range(section.start_page, section.end_page + 1))
    if set(source) != expected or not expected.issubset(pages):
        raise ValueError("incomplete_section_page_coverage")
    if (
        normalized(section.body)[0]
        != normalized(" ".join(text for _, text in section.page_bodies))[0]
    ):
        raise ValueError("section_body_page_bodies_mismatch")
    if len(source) != len(section.page_bodies) or page_number not in source:
        raise ValueError("invalid_page_bodies")
    target, ui = normalized(unit)
    hits = []
    for pn, text in section.page_bodies:
        flat, ix = normalized(text)
        for m in re.finditer("(?=" + re.escape(target) + ")", flat):
            start = m.start()
            end = start + len(target)
            if (start == 0 or flat[start - 1].isspace()) and (
                end == len(flat) or flat[end].isspace()
            ):
                hits.append((pn, ix[start:end]))
    if len(hits) != 1 or hits[0][0] != page_number:
        raise ValueError("missing_or_duplicate_unit")
    page = pages[page_number]
    body = source[page_number]
    pb, bi = normalized(body)
    full, fi = normalized(page)
    bh = [
        m.start()
        for m in re.finditer("(?=" + re.escape(pb) + ")", full)
        if (m.start() == 0 or full[m.start() - 1].isspace())
        and (m.start() + len(pb) == len(full) or full[m.start() + len(pb)].isspace())
    ]
    if len(bh) != 1:
        raise ValueError("page_body_not_exactly_bound")
    # Only whitespace normalization is admitted at either link; no reverse skipping.
    body_to_page = {bi[j]: fi[bh[0] + j] for j in range(len(pb))}
    unorm_to_body = hits[0][1]
    umap = {ui[j]: body_to_page[unorm_to_body[j]] for j in range(len(ui))}
    out = []
    for mapped in forward(unit):
        entries = []
        for ch, pos in zip(mapped["text"], mapped["offsets"]):
            if ch.isspace():
                entries.append(None)
                continue
            if pos not in umap or page[umap[pos]] != ch:
                raise ValueError("nonwhitespace_origin_missing")
            entries.append(umap[pos])
        out.append(dict(mapped, page_offsets=entries, page_number=page_number))
    return out
