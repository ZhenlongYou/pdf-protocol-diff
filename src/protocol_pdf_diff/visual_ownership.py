"""Occurrence-level visual ownership while immutable source geometry is alive.

A crop's vocabulary is never evidence that an arbitrary occurrence belongs to
that crop. Only a unique ordered source-word occurrence may authorize trimming.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from .text_utils import compact_inline
from .models import DocumentBlockKind


def _tokens(text):
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r'\S+', compact_inline(text))]


def _inside(word, box):
    return (len(box) == 4 and all(math.isfinite(v) for v in (*word[1:], *box))
            and box[0] <= word[1] < word[3] <= box[2]
            and box[1] <= word[2] < word[4] <= box[3])


def build_visual_owned_spans(result, old_extraction, new_extraction, visual_groups, *, _physical=None):
    """Retain small removal spans, not a second copy of all page words."""
    output = {}
    char_candidates = {}
    figure_intervals = {}
    figure_span_owners = defaultdict(set)
    char_boxes_by_side = {}
    extractions = {"old": old_extraction, "new": new_extraction}
    for side, extraction in (('old', old_extraction), ('new', new_extraction)):
        boxes = defaultdict(list)
        char_boxes = defaultdict(list)
        char_boxes_by_side[side] = char_boxes
        figure_boxes = defaultdict(list)
        box_owners = defaultdict(set)
        for table in getattr(result, side + '_table_visuals'):
            if (table.bbox and table.row_texts and table.content_fully_represented
                    and table.row_alignment_reliable and table.data_rows_fully_represented):
                boxes[table.page_number].append(table.bbox)
        pages = {page.page_number: page for page in extraction.pages}
        for group_index, group in enumerate(visual_groups):
            # This identity names an already paired group, never pairs figures
            # by their array positions. Within it, captions must be bilateral unique.
            old_caps = [compact_inline(c) for c in group.old_figure_captions]
            new_caps = [compact_inline(c) for c in group.new_figure_captions]
            group_complete = (bool(group.old_section_id) and bool(group.new_section_id)
                and len(old_caps) == len(group.old_figure_visuals)
                and len(new_caps) == len(group.new_figure_visuals))
            captions = getattr(group, side + '_figure_captions')
            for visual, caption in zip(getattr(group, side + '_figure_visuals'), captions):
                page = pages.get(visual.page_number)
                if page is None:
                    continue
                caption_boxes = []
                for block in page.blocks:
                    if block.kind != DocumentBlockKind.TEXT:
                        continue
                    if compact_inline(block.text) == compact_inline(caption):
                        caption_boxes.append(block.bbox)
                        continue
                    if [t[0] for t in _tokens(block.text)] != [w[0] for w in block.word_boxes]:
                        continue
                    kept = [w for w in block.word_boxes if not any(_inside(w, b) for b in page.visual_noise_bboxes)]
                    if kept and compact_inline(' '.join(w[0] for w in kept)) == compact_inline(caption):
                        caption_boxes.append((min(w[1] for w in kept), min(w[2] for w in kept),
                                              max(w[3] for w in kept), max(w[4] for w in kept)))
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
                    figure_boxes[page.page_number].append(box)
                    cap_key = compact_inline(caption)
                    if (group_complete and cap_key
                            and old_caps.count(cap_key) == new_caps.count(cap_key) == 1):
                        box_owners[(page.page_number, tuple(box))].add(
                            (group_index, group.old_section_id, group.new_section_id, cap_key))
                    if (visual.image_data_uri and group_complete and cap_key
                            and old_caps.count(cap_key) == new_caps.count(cap_key) == 1):
                        char_boxes[page.page_number].append((box, caption,
                            (group_index, group.old_section_id, group.new_section_id)))
        if _physical is not None:
            boxes = defaultdict(list)
            owner_side, owner_page, owner_box = _physical
            if side == owner_side:
                boxes[owner_page].append(owner_box)
        streams = {}
        unknown_tokens = {}
        for page in extraction.pages:
            if page.page_number not in boxes or page.ocr_used:
                continue
            seen, words, incomplete = set(), [], set()
            for block in sorted(page.blocks, key=lambda b: b.reading_order):
                if block.kind == DocumentBlockKind.TABLE:
                    continue  # Derived table summaries are not extra native occurrences.
                block_tokens = [t[0] for t in _tokens(block.text)]
                word_tokens = [w[0] for w in block.word_boxes]
                if block_tokens != word_tokens:
                    incomplete.update(block_tokens)
                    incomplete.update(word_tokens)
                    words.append(('', 0., 0., 0., 0.))
                    continue  # an unmapped block is an ambiguity, not an absence
                for word in block.word_boxes:
                    if any(_inside(word, box) for box in page.visual_noise_bboxes):
                        continue  # Only the extractor's proved furniture intervals.
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
            if _physical is not None and not (
                    side == _physical[0] and section.start_page <= _physical[1] <= section.end_page):
                continue  # Keep the full section uniqueness scan for relevant owners.
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
                from .source_char_evidence import char_owned_candidate
                proof = char_owned_candidate(value, section, extraction.pages, char_boxes)
                if proof is not None:
                    char_candidates[(side, section.section_id, compact_inline(value))] = proof

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
                    source_text = page.text
                    if _physical is not None:
                        # Remove only complete derived summaries from the
                        # duplicate count; unexplained native occurrences veto.
                        source_text = compact_inline(page.text)
                        for block in page.blocks:
                            derived = compact_inline(block.text)
                            if block.kind == DocumentBlockKind.TABLE and derived:
                                source_text = source_text.replace(derived, '', 1)
                    text_count = compact_inline(source_text).count(compact_inline(value))
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
                generated_figure_spans = []
                for box in boxes[page_number]:
                    before_spans = len(spans)
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
                    if box in figure_boxes[page_number]:
                        generated_figure_spans.extend(spans[before_spans:])
                        for span in spans[before_spans:]:
                            key = (side, section.section_id, compact_inline(value))
                            owners = box_owners[(page_number, tuple(box))]
                            figure_span_owners[(key, span)].update(
                                owner for owner in owners
                                if owner[1 if side == 'old' else 2] == section.section_id)
                spans = [(a, b) for a, b in spans if not re.search(
                    r"(?i)\b(?:shall|should|must|required|prohibited)\b", compact_inline(value)[a:b])]
                if spans:
                    figure_intervals[(side, section.section_id, compact_inline(value))] = [p for p in generated_figure_spans if p in spans]
                    output.setdefault(side + ':' + section.section_id, {})[compact_inline(value)] = sorted(set(spans))
    from .source_char_evidence import equivalent_char_ownership
    denied_keys = set()
    # Physical-row recursion has no Figure certificates and must not inspect
    # unrelated sections while authorizing a single source table row.
    for change in result.changes if _physical is None else ():
        # A direct graphic label may disappear from prose only when its exact
        # value still has a unique physical counterpart in the paired section.
        for side, other_side, direct, audit in (
                ('old', 'new', change.removed_snippets, change.audit_removed_snippets),
                ('new', 'old', change.added_snippets, change.audit_added_snippets)):
            section = getattr(change, side + '_section')
            other_section = getattr(change, other_side + '_section')
            if section is None:
                continue
            for raw_value in {*direct, *(audit or ())}:
                value = compact_inline(raw_value)
                key = (side, section.section_id, value)
                proof = char_candidates.get(key)
                if not figure_intervals.get(key) and proof is None:
                    continue
                counterpart = None
                if other_section is not None and proof is not None:
                    counterpart = char_owned_candidate(value, other_section,
                        extractions[other_side].pages, char_boxes_by_side[other_side])
                if counterpart is not None and equivalent_char_ownership(proof, counterpart):
                    output.setdefault(side + ':' + section.section_id, {})[value] = proof['spans']
                    figure_intervals[key] = proof['spans']
                else:
                    denied_keys.add(key)
        if change.old_section is None or change.new_section is None:
            continue
        pairs = list(change.replaced_snippets)
        pairs += list(change.audit_replaced_snippets or ())
        for pair in pairs:
            a = char_candidates.get(('old', change.old_section.section_id, compact_inline(pair.old)))
            b = char_candidates.get(('new', change.new_section.section_id, compact_inline(pair.new)))
            old_key = ('old', change.old_section.section_id, compact_inline(pair.old))
            new_key = ('new', change.new_section.section_id, compact_inline(pair.new))
            old_figure = figure_intervals.get(old_key, ())
            new_figure = figure_intervals.get(new_key, ())
            spatial_equal = a is not None and b is not None and equivalent_char_ownership(a, b)
            if old_figure or new_figure or a is not None or b is not None:
                old_removed = tuple(compact_inline(pair.old)[x:y] for x,y in sorted(set(old_figure)))
                new_removed = tuple(compact_inline(pair.new)[x:y] for x,y in sorted(set(new_figure)))
                old_owners = [figure_span_owners[(old_key, span)] for span in sorted(set(old_figure))]
                new_owners = [figure_span_owners[(new_key, span)] for span in sorted(set(new_figure))]
                same_word_figure = (old_removed and old_removed == new_removed
                    and len(old_owners) == len(new_owners)
                    and all(len(x) == len(y) == 1 and x == y
                            for x, y in zip(old_owners, new_owners)))
                if not (spatial_equal or same_word_figure):
                    denied_keys.update((old_key, new_key))
            if a is not None and b is not None and equivalent_char_ownership(a, b):
                for side, section, value, proof in [('old',change.old_section,pair.old,a),
                                                   ('new',change.new_section,pair.new,b)]:
                    output.setdefault(side + ':' + section.section_id, {})[compact_inline(value)] = proof['spans']
                    figure_intervals[(side, section.section_id, compact_inline(value))] = proof['spans']
    # Denials dominate all producers and all pairs sharing the same value key.
    for side, section_id, value in denied_keys:
        values = output.get(side + ':' + section_id, {})
        if value in values:
            values[value] = [span for span in values[value] if span not in figure_intervals.get((side, section_id, value), ())]
            if not values[value]:values.pop(value)
    if _physical is None:
        # These candidates require a receipt from the actual report outputs.
        for side in ('old', 'new'):
            for table in getattr(result, side + '_table_visuals'):
                for row in table.physical_rows:
                    candidates = build_visual_owned_spans(
                        result, old_extraction, new_extraction, (),
                        _physical=(side, table.page_number, row.bbox))
                    for key, value in candidates.items():
                        output['physical:' + row.row_id + ':' + key] = value
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
