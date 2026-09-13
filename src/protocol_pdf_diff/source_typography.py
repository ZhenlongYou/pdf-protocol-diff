"""Restore proven raised glyph typography in reader text, never infer powers."""
from dataclasses import replace
import math
import re

_SUPER = str.maketrans('0123456789+-−', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻')



def _source_line_parts(block, page):
    """Remove only extractor-proven furniture, keeping native word/style indices."""
    words, styles = block.word_boxes, block.word_styles
    if ''.join(''.join(w[0].split()) for w in words) != ''.join(block.text.split()):
        return (), ()
    boxes = getattr(page, 'visual_noise_bboxes', ())
    kept = [i for i, word in enumerate(words) if not any(
        len(box) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
        and box[0] <= word[1] < word[3] <= box[2]
        and box[1] <= word[2] < word[4] <= box[3] for box in boxes)]
    return (tuple(words[i] for i in kept),
            tuple(styles[i] for i in kept) if len(styles) == len(words) else ())


def source_superscript_receipts(extraction):
    """Keep complete native-line evidence; offsets refer to nonspace characters."""
    receipts = []
    from collections import Counter, defaultdict
    for page in extraction.pages:
        if page.ocr_used:
            continue
        page_key = ''.join(page.text.split())
        for block in page.blocks:
            words, styles = _source_line_parts(block, page)
            if len(words) != len(styles) or len(words) < 2:
                continue
            key = ''.join(''.join(w[0].split()) for w in words)
            if len(key) < 20 or page_key.count(key) != 1:
                continue
            if len({tuple(w[1:]) for w in words}) != len(words):
                continue
            spans = []
            offset = len(''.join(words[0][0].split()))
            for index in range(1, len(words)):
                base, raised = words[index - 1], words[index]
                font, size = styles[index]
                base_font, base_size = styles[index - 1]
                length = len(''.join(raised[0].split()))
                values = (*base[1:], *raised[1:], size, base_size)
                if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in values):
                    offset += length
                    continue
                # The actual smaller and raised source glyphs are sufficient
                # typography evidence, including footnote markers. No ^ is added.
                height = base[4] - base[2]
                if (re.fullmatch(r'[+\-−]?[0-9]+', raised[0])
                        and font == base_font and base_size > 0 and height > 0
                        and .55 <= size / base_size <= .85
                        and -.1 * base_size <= raised[1] - base[3] <= .15 * base_size
                        and .1 * height <= base[2] - raised[2] <= .65 * height
                        and .25 * height <= base[4] - raised[4] <= .8 * height
                        and raised[4] > base[2]
                        and raised[3] > raised[1]):
                    spans.append((offset, offset + length))
                offset += length
            if spans:
                receipts.append((page.page_number, key, tuple(spans)))
    # Do not discard valid lines because another chapter repeats them. Keep
    # page-local evidence, including explicit blockers for unstyled or repeated
    # occurrences; only the caller knows the current section's page scope.
    proven = defaultdict(list)
    for page_number, key, spans in receipts:
        proven[(page_number, key)].append(spans)
    keys = {key for _, key, _ in receipts}
    scoped = []
    for page in extraction.pages:
        page_key = ''.join(page.text.split())
        block_counts = Counter(''.join(''.join(w[0].split()) for w in _source_line_parts(b, page)[0])
                               for b in page.blocks)
        for key in sorted(keys):
            count = page_key.count(key)
            if not count and not block_counts[key]:
                continue
            matches = proven[(page.page_number, key)]
            spans = (matches[0] if count == block_counts[key] == len(matches) == 1
                     and not page.ocr_used else ())
            scoped.append((page.page_number, key, spans))
    return tuple(scoped)


def restore_superscript_display(value, section, receipts):
    if section is None:
        return value
    positions = [i for i, char in enumerate(value) if not char.isspace()]
    key = ''.join(value[i] for i in positions)
    replacements = {}
    from collections import defaultdict
    scoped = defaultdict(list)
    for page, source, spans in receipts:
        if section.start_page <= page <= section.end_page:
            scoped[source].append(spans)
    for source, proofs in scoped.items():
        if len(proofs) != 1 or not proofs[0]:
            continue  # A second occurrence in this section cannot borrow styles.
        spans = proofs[0]
        occurrences = [m.start() for m in re.finditer('(?=' + re.escape(source) + ')', key)]
        if len(occurrences) != 1:
            continue
        start = occurrences[0]
        for left, right in spans:
            if not 0 <= left < right <= len(source):
                continue
            for offset in range(left, right):
                index = positions[start + offset]
                translated = value[index].translate(_SUPER)
                if index in replacements and replacements[index] != translated:
                    return value
                replacements[index] = translated
    return ''.join(replacements.get(i, char) for i, char in enumerate(value))


def restore_change_typography(change, old_receipts, new_receipts):
    def old(value):
        return restore_superscript_display(value, change.old_section, old_receipts)
    def new(value):
        return restore_superscript_display(value, change.new_section, new_receipts)
    def pair(value):
        return replace(value, old=old(value.old), new=new(value.new))
    return replace(change,
        added_snippets=[new(v) for v in change.added_snippets],
        removed_snippets=[old(v) for v in change.removed_snippets],
        replaced_snippets=[pair(v) for v in change.replaced_snippets],
        review_replaced_snippets=[pair(v) for v in change.review_replaced_snippets])
