"""Native TextMap ownership, conditional on all physical-row report receipts."""
import json
import math
import re
import unicodedata
from collections import Counter
from .text_utils import compact_inline


def native_page_evidence(page):
    cached = getattr(page, '_physical_native_evidence', None)
    if cached is not None:
        return cached
    try:
        textmap = page.get_textmap(x_tolerance=1, y_tolerance=3)
        raw = ''.join(value for value, _ in textmap.tuples)
        if raw != textmap.as_string or textmap.line_dir_render != 'ttb' or textmap.char_dir_render != 'ltr':
            return None, ()
        text = compact_inline(raw)
        glyph_ids, chars = {}, []
        for value, glyph in textmap.tuples:
            for char in value:
                if char.isspace():
                    continue
                if not isinstance(glyph, dict) or glyph.get('text') != value:
                    return text, ()
                box = tuple(glyph[k] for k in ('x0', 'top', 'x1', 'bottom'))
                if not _valid_box(box):
                    return text, ()
                gid = glyph_ids.setdefault(id(glyph), len(glyph_ids))
                chars.append((len(chars), char, *box, gid))
        if ''.join(c[1] for c in chars) != ''.join(text.split()):
            return text, ()
        counts = Counter(c[6] for c in chars)
        result = (text, tuple((*c, counts[c[6]]) for c in chars))
        page._physical_native_evidence = result
        return result
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None, ()


def _valid_box(box):
    return (len(box) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
            and box[0] < box[2] and box[1] < box[3])


def _inside(char, box):
    return box[0] <= char[2] < char[4] <= box[2] and box[1] <= char[3] < char[5] <= box[3]


def valid_native_row(row):
    try:
        chars = row.native_chars
        if not chars or not _valid_box(row.bbox) or len(row.cells) != 4 or len(row.cell_bboxes) != 4:
            return False
        if any(len(c) != 8 or type(c[0]) is not int or c[0] < 0 or not isinstance(c[1], str) or len(c[1]) != 1
               or c[1].isspace() or unicodedata.category(c[1]) == 'Co' or not _valid_box(c[2:6])
               or type(c[6]) is not int or c[6] < 0 or type(c[7]) is not int or c[7] != 1 or not _inside(c, row.bbox) for c in chars):
            return False
        if [c[0] for c in chars] != sorted(set(c[0] for c in chars)) or len({c[6] for c in chars}) != len(chars):
            return False
        for cell, box in zip(row.cells, row.cell_bboxes, strict=True):
            if not _valid_box(box) or ''.join(c[1] for c in chars if _inside(c, box)) != ''.join(cell.split()):
                return False
        return all(sum(_inside(c, box) for box in row.cell_bboxes) == 1 for c in chars)
    except (AttributeError, TypeError, ValueError, IndexError):
        return False


def capture_native_row(row, page):
    from dataclasses import replace
    _, chars = native_page_evidence(page)
    selected = tuple(c for c in chars if _inside(c, row.bbox))
    candidate = replace(row, native_chars=selected)
    return candidate if valid_native_row(candidate) else row


def _occurrence_owners(value, section, pages, rows):
    needle = compact_inline(value)
    if not needle or re.search(r'(?i)\b(?:shall|should|must|required|prohibited)\b', needle):
        return None
    if any(unicodedata.category(c) == 'Co' for c in needle):
        return None
    selected = [p for p in pages if section.start_page <= p.page_number <= section.end_page]
    if sorted(p.page_number for p in selected) != list(range(section.start_page, section.end_page + 1)):
        return None
    dependencies = set()
    occurrence_count = 0
    for page in selected:
        text = getattr(page, 'physical_native_text', None)
        if not isinstance(text, str) or page.ocr_used:
            return None
        for match in re.finditer('(?=' + re.escape(needle) + ')', text):
            start, end = match.start(), match.start() + len(needle)
            if (start and not text[start-1].isspace()) or (end < len(text) and not text[end].isspace()):
                continue
            offset = sum(not c.isspace() for c in text[:start])
            needed = list(range(offset, offset + sum(not c.isspace() for c in needle)))
            owners = []
            for page_number, row in rows:
                if page_number != page.page_number:
                    continue
                mapping = {c[0]: c for c in row.native_chars}
                if all(i in mapping for i in needed) and ''.join(mapping[i][1] for i in needed) == ''.join(needle.split()):
                    owners.append(row.row_id)
            if len(owners) != 1:
                return None
            dependencies.add(owners[0])
            occurrence_count += 1
    return dependencies if occurrence_count else None


def native_owned_candidates(result, old_extraction, new_extraction):
    from .physical_table_rows import _valid_row, _key
    output = {}
    for change in result.changes:
        sections = (change.old_section, change.new_section)
        if not all(sections):
            continue
        sides = [[(t.page_number, row) for t in tables
                  if section.start_page <= t.page_number <= section.end_page
                  for row in t.physical_rows if _valid_row(row, t) and valid_native_row(row)]
                 for tables, section in zip((result.old_table_visuals, result.new_table_visuals), sections)]
        counts = [Counter(_key(r) for _, r in side) for side in sides]
        paired = [{_key(r) for _, r in side if counts[0][_key(r)] == counts[1][_key(r)] == 1} for side in sides]
        eligible = [[(p, r) for p, r in side if _key(r) in paired[0] & paired[1]] for side in sides]
        if not all(eligible):
            continue
        pairs = change.audit_replaced_snippets if change.audit_replaced_snippets is not None else change.replaced_snippets
        for pair in pairs:
            values = (pair.old, pair.new)
            proofs = [_occurrence_owners(value, section, extraction.pages, rows)
                      for value, section, extraction, rows in zip(values, sections, (old_extraction,new_extraction), eligible)]
            if not all(proofs):
                continue
            # A row used on either side requires both members of its exact
            # physical pair. Shared short snippets must not borrow a partial
            # receipt set from another replacement pair.
            used_keys = {_key(row) for rows, ids in zip(eligible, proofs)
                         for _, row in rows if row.row_id in ids}
            deps = sorted((side, row.row_id) for side, rows in zip(('old','new'), eligible)
                          for _, row in rows if _key(row) in used_keys)
            encoded = json.dumps(deps, separators=(',', ':'))
            for side, section, value in zip(('old','new'), sections, values):
                key = 'physical-native:' + encoded + ':' + side + ':' + section.section_id
                output.setdefault(key, {})[compact_inline(value)] = [(0, len(compact_inline(value)))]
    return output


def authorized_native_spans(candidates, side, section_id, receipts):
    output = {}
    suffix = ':' + side + ':' + section_id
    for key, values in candidates.items():
        if not key.startswith('physical-native:') or not key.endswith(suffix):
            continue
        try:
            dependencies = json.loads(key[len('physical-native:'):-len(suffix)])
            if not dependencies or not all(tuple(item) in receipts for item in dependencies):
                continue
        except (TypeError, ValueError):
            continue
        for text, spans in values.items():
            output.setdefault(text, []).extend(spans)
    return output
