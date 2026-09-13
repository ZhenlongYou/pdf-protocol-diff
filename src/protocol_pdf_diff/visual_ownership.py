"""Occurrence-level visual ownership while immutable source geometry is alive.

A crop's vocabulary is never evidence that an arbitrary occurrence belongs to
that crop. Only a unique ordered source-word occurrence may authorize trimming.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from .text_utils import compact_inline


def _tokens(text):
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r'\S+', compact_inline(text))]


def _inside(word, box):
    return (len(box) == 4 and all(math.isfinite(v) for v in (*word[1:], *box))
            and box[0] <= word[1] < word[3] <= box[2]
            and box[1] <= word[2] < word[4] <= box[3])


def build_visual_owned_spans(result, old_extraction, new_extraction, visual_groups):
    """Retain small removal spans, not a second copy of all page words."""
    output = {}
    for side, extraction in (('old', old_extraction), ('new', new_extraction)):
        boxes = defaultdict(list)
        for table in getattr(result, side + '_table_visuals'):
            if (table.bbox and table.row_texts and table.content_fully_represented
                    and table.row_alignment_reliable and table.data_rows_fully_represented):
                boxes[table.page_number].append(table.bbox)
        pages = {page.page_number: page for page in extraction.pages}
        for group in visual_groups:
            captions = getattr(group, side + '_figure_captions')
            for visual, caption in zip(getattr(group, side + '_figure_visuals'), captions):
                page = pages.get(visual.page_number)
                if page is None:
                    continue
                caption_boxes = [block.bbox for block in page.blocks
                                 if compact_inline(block.text) == compact_inline(caption)]
                if len(caption_boxes) != 1:
                    continue
                cap = caption_boxes[0]
                for box in page.vector_graphic_bboxes:
                    # A contextual screenshot is not an ownership region. A
                    # nearby same-column physical drawing boundary is required.
                    if not _inside(('', *box), visual.crop_bbox):
                        continue
                    gap = min(abs(box[1] - cap[3]), abs(cap[1] - box[3]))
                    overlap = min(box[2], cap[2]) - max(box[0], cap[0])
                    if gap > 40 or overlap < .8 * min(box[2]-box[0], cap[2]-cap[0]):
                        continue
                    if any(table.page_number == page.page_number and table.bbox
                           and min(box[2],table.bbox[2]) > max(box[0],table.bbox[0])
                           and min(box[3],table.bbox[3]) > max(box[1],table.bbox[1])
                           for table in getattr(result, side + '_table_visuals')):
                        continue  # Figure context cannot bypass the Table quality gate.
                    boxes[page.page_number].append(box)
        streams = {}
        unknown_tokens = {}
        for page in extraction.pages:
            if page.page_number not in boxes or page.ocr_used:
                continue
            seen, words, incomplete = set(), [], set()
            for block in sorted(page.blocks, key=lambda b: b.reading_order):
                block_tokens = [t[0] for t in _tokens(block.text)]
                word_tokens = [w[0] for w in block.word_boxes]
                if block_tokens != word_tokens:
                    incomplete.update(block_tokens)
                    incomplete.update(word_tokens)
                    words.append(('', 0., 0., 0., 0.))
                    continue  # an unmapped block is an ambiguity, not an absence
                for word in block.word_boxes:
                    if word in seen:
                        continue
                    seen.add(word)
                    # Do not invent subword positions when extraction merged tokens.
                    if len(_tokens(word[0])) != 1:
                        words.append(('', 0., 0., 0., 0.))
                    else:
                        words.append(word)
            streams[page.page_number] = words
            unknown_tokens[page.page_number] = incomplete
        for change in result.changes:
            section = getattr(change, side + '_section')
            if section is None:
                continue
            direct = change.removed_snippets if side == 'old' else change.added_snippets
            audit = change.audit_removed_snippets if side == 'old' else change.audit_added_snippets
            pairs = change.audit_replaced_snippets if change.audit_replaced_snippets is not None else change.replaced_snippets
            values = set(audit if audit is not None else direct)
            values.update(getattr(pair, side) for pair in pairs)
            for value in values:
                target = _tokens(value)
                if not target:
                    continue
                keys = [t[0] for t in target]
                matches = []
                for page in extraction.pages:
                    if not section.start_page <= page.page_number <= section.end_page:
                        continue
                    # Missing source-word coverage on a possible occurrence page
                    # cannot be used as evidence of uniqueness.
                    if compact_inline(value) in compact_inline(page.text) and page.page_number not in streams:
                        matches.append(None)
                        continue
                    if set(keys) & unknown_tokens.get(page.page_number, set()):
                        matches.append(None)
                        break
                    words = streams.get(page.page_number, ())
                    before_count = len(matches)
                    for start in range(len(words) - len(keys) + 1):
                        if words[start][0] != keys[0]:
                            continue
                        candidate = words[start:start + len(keys)]
                        if [w[0] for w in candidate] == keys:
                            matches.append((page.page_number, candidate))
                            if len(matches) > 1:
                                break
                    text_count = compact_inline(page.text).count(compact_inline(value))
                    if text_count > len(matches) - before_count:
                        matches.append(None)
                    if len(matches) > 1:
                        break
                if len(matches) != 1 or matches[0] is None:
                    continue
                page_number, words = matches[0]
                # Each removed run must belong to ONE actual crop; disjoint
                # figures cannot pool their words into an ownership certificate.
                spans = []
                for box in boxes[page_number]:
                    owned = [_inside(word, box) for word in words]
                    prefix = 0
                    while prefix < len(owned) and owned[prefix]:
                        prefix += 1
                    suffix = len(owned)
                    while suffix > prefix and owned[suffix - 1]:
                        suffix -= 1
                    if prefix:
                        spans.append((0, target[prefix - 1][2]))
                    if suffix < len(owned):
                        spans.append((target[suffix][1], len(compact_inline(value))))
                spans = [(a, b) for a, b in spans if not re.search(
                    r"(?i)\b(?:shall|should|must|required|prohibited)\b", compact_inline(value)[a:b])]
                if spans:
                    output.setdefault(side + ':' + section.section_id, {})[compact_inline(value)] = sorted(set(spans))
    return output


def apply_owned_spans(value, spans):
    """Apply bounded source-coordinate intervals, keeping all other characters."""
    value = compact_inline(value)
    if not spans:
        return value
    intervals = sorted(spans)
    if any(not isinstance(a, int) or not isinstance(b, int) or not 0 <= a < b <= len(value)
           for a, b in intervals):
        return value
    kept, cursor = [], 0
    for start, end in intervals:
        if start > cursor:
            kept.append(value[cursor:start])
        cursor = max(cursor, end)
    kept.append(value[cursor:])
    return ''.join(kept).strip()
