"""Bind heading style to actual words, excluding independently owned regions."""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from statistics import median
from types import SimpleNamespace
from .models import DocumentBlockKind
from .text_utils import compact_inline
from .source_regions import non_heading_regions


class _PageIdentity:
    # Dataclass page equality intentionally excludes font metadata. Cache source
    # evidence by object identity, never by that comparison-facing equality.
    def __init__(self, page):
        self.page = page
    def __hash__(self):
        return id(self.page)
    def __eq__(self, other):
        return isinstance(other, _PageIdentity) and self.page is other.page


def _page_word_index(page):
    return _cached_page_word_index(_PageIdentity(page))


@lru_cache(maxsize=8)
def _cached_page_word_index(identity):
    page = identity.page
    # The immutable page is the cache key; retained evidence is bounded to eight
    # pages. Recomputing all glyph/owner intersections for each heading is costly.
    lines = defaultdict(list)
    body = Counter()
    physical_lines = []
    candidate_boxes = ()
    if getattr(page, 'ambiguous_line_number_sides', ()) and getattr(page, 'page_bbox', None):
        from .pdf_extract import _candidate_line_number_gutter_boxes
        page_words = [dict(text=w[0], x0=w[1], top=w[2], x1=w[3], bottom=w[4])
                      for block in page.blocks for w in block.word_boxes]
        candidate_boxes = _candidate_line_number_gutter_boxes(
            SimpleNamespace(width=page.page_bbox[2]-page.page_bbox[0],
                            height=page.page_bbox[3]-page.page_bbox[1]), words=page_words)
    for block in page.blocks:
        styles = getattr(block, 'word_styles', ())
        if block.kind != DocumentBlockKind.TEXT or len(styles) != len(block.word_boxes):
            continue
        words = [(word, style) for word, style in zip(block.word_boxes, styles)
                 if not any(box[0] <= (word[1]+word[3])/2 <= box[2]
                            and box[1] <= (word[2]+word[4])/2 <= box[3]
                            for box in getattr(page, 'visual_noise_bboxes', ()))]
        if words:
            lines[compact_inline(' '.join(w[0] for w, _ in words))].append(words)
            # A native y-row can span two separately reconstructed text columns.
            # Bind each exact contiguous column span only across a conspicuous
            # physical gutter; never grant arbitrary substrings heading identity.
            positive_gaps = [b[0][1]-a[0][3] for a,b in zip(words,words[1:])
                             if b[0][1] > a[0][3]]
            ordinary_gap = median(positive_gaps) if positive_gaps else 0
            clusters = [[]]
            for item in words:
                if clusters[-1]:
                    previous = clusters[-1][-1]
                    if item[0][1]-previous[0][3] >= max(20, ordinary_gap*4,
                                                               previous[1][1]*2, item[1][1]*2):
                        clusters.append([])
                clusters[-1].append(item)
            if len(clusters) > 1:
                for cluster in clusters:
                    lines[compact_inline(' '.join(w[0] for w, _ in cluster))].append(cluster)

            # Ambiguous margin numbers remain in comparison text. Only bind the
            # heading candidate to its exact remaining source-word subsequence.
            if candidate_boxes:
                reduced = [(w,s) for w,s in words if not (w[0].isdigit() and any(
                    b[0] <= (w[1]+w[3])/2 <= b[2] and b[1] <= (w[2]+w[4])/2 <= b[3]
                    for b in candidate_boxes))]
                if reduced and reduced != words:
                    lines[compact_inline(' '.join(w[0] for w,_ in reduced))].append(reduced)
                    words = reduced
            # A raised, smaller adjacent glyph can be attached by the text
            # extractor (title footnote). Keep its original word identity.
            rendered = words[0][0][0]
            for (prior, prior_style), (word, style) in zip(words, words[1:]):
                attached = (style[1] <= prior_style[1]*.8 and word[0].isdigit()
                            and -.5 <= word[1]-prior[3] <= prior_style[1]*.15
                            and word[4] < prior[4]-prior_style[1]*.15)
                rendered += ('' if attached else ' ') + word[0]
            normal = ' '.join(w[0] for w,_ in words)
            if compact_inline(rendered) != compact_inline(normal):
                lines[compact_inline(rendered)].append(words)
            physical_lines.append(words)
            for w, style in words:
                if style[0] and style[1] > 0:
                    body[(style[0], round(style[1], 1))] += len(w[0])
    # The sectioner may join a standalone number and its wrapped title. Preserve
    # exact adjacent source spans instead of accepting an arbitrary substring.
    for start in range(len(physical_lines)):
        joined = list(physical_lines[start])
        for following in physical_lines[start + 1:start + 3]:
            gap = min(w[2] for w, _ in following) - max(w[4] for w, _ in joined)
            size = max(style[1] for _w, style in joined)
            if (gap < 0 and len({style[0] for _w, style in [*joined, *following]}) == 1
                    and min(w[1] for w, _ in following) >= min(w[1] for w, _ in joined)
                    and max(w[3] for w, _ in following) <= max(w[3] for w, _ in joined)):
                # Reuse the extractor's one-to-one source-coordinate subscript
                # proof. A lower symbol row is not a wrapped paragraph/column.
                from .pdf_extract import _repair_body_visual_subscript_order
                raw = '\n'.join(' '.join(w[0] for w, _ in row) for row in (joined, following))
                records = [dict(text=w[0], x0=w[1], top=w[2], x1=w[3], bottom=w[4])
                           for w, _style in [*joined, *following]]
                repaired = _repair_body_visual_subscript_order(raw, records)
                if repaired != raw and '\n' not in repaired:
                    lines[compact_inline(repaired)].append([*joined, *following])
            left = min(w[1] for w, _ in joined)
            next_left = min(w[1] for w, _ in following)
            if (not 0 <= gap <= max(1.0, size * 1.6)
                    or abs(left - next_left) > max(8.0, size * 1.5)):
                break
            joined.extend(following)
            lines[compact_inline(' '.join(w[0] for w, _ in joined))].append(list(joined))
    return lines, body, non_heading_regions(page, physical_lines)


def line_word_evidence(page, text):
    matches = _page_word_index(page)[0].get(compact_inline(text), ())
    # Several proof routes (column gap, margin-number removal, joined title)
    # may identify the very same original word objects. Count source spans,
    # not proof routes; equal text at different locations stays ambiguous.
    unique = {tuple(id(word) for word, _style in span): span for span in matches}
    return next(iter(unique.values())) if len(unique) == 1 else []


def strong_heading_style(page, text):
    """Require a unique non-table source span and contrast to observed body style."""
    words = line_word_evidence(page, text)
    if not words:
        return False
    owner_boxes = [b.bbox for b in page.blocks if b.kind == DocumentBlockKind.TABLE]
    if any(box[0] <= (w[1]+w[3])/2 <= box[2]
           and box[1] <= (w[2]+w[4])/2 <= box[3]
           for w, _ in words for box in owner_boxes):
        return False
    body = _page_word_index(page)[1]
    if not body or sum(body.values()) < 200:
        return False
    (body_font, body_size), _ = body.most_common(1)[0]
    weight = sum(len(w[0]) for w, _ in words)
    distinct_weight = sum(len(w[0]) for w, (font, size) in words
                          if size >= body_size * 1.12
                          or ('bold' in font.lower() and 'bold' not in body_font.lower()))
    return weight > 0 and distinct_weight / weight >= .70


def source_region_role(page, text):
    words = line_word_evidence(page, text)
    if not words:
        return None
    for box, role in _page_word_index(page)[2]:
        contained = [box[0] <= (w[1]+w[3])/2 <= box[2] and
                     box[1] <= (w[2]+w[4])/2 <= box[3] for w, _s in words]
        if all(contained) or (role == 'table' and any(contained)):
            return role
    return None
