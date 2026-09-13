"""Shared geometric owners that cannot also act as chapter headings.

Classification preserves source text. These owners only constrain structure and
source crops; neither figure nor formula text becomes a semantic deletion.
"""
from __future__ import annotations
import re
from statistics import median
from .models import DocumentBlockKind

CAPTION = re.compile(r'(?i)^(?:figure|fig\.|table|图|表)\s*(?:\d+|[A-Z])(?:[.\-–]\w+)*(?:[.：:]|\s)')
FURNITURE = re.compile(r'(?i)\b(?:clause|consortium|copyright|draft|edition|forum|page|revision|specification|standard|version|working\s+group|www\.|https?://)\b|©')


def bounds(words):
    return (min(w[1] for w, _s in words), min(w[2] for w, _s in words),
            max(w[3] for w, _s in words), max(w[4] for w, _s in words))


def bottom_margin_furniture(text, bbox, page_bbox, content_left, content_right):
    if page_bbox is None:
        return False
    height = page_bbox[3] - page_bbox[1]
    return bool('\n' not in text and bbox[1] >= page_bbox[1] + height * .9
                and bbox[3] - bbox[1] <= height * .035
                and bbox[2] - bbox[0] >= (content_right-content_left) * .55
                and FURNITURE.search(text))


def running_footer_folio(words, page_bbox):
    """Return the actual isolated end-word proven to be a folio, or None.

    The end integer must occupy an outer margin slot separated from the text
    cluster by both two glyph heights and four normal word gaps. A sentence's
    final value or publication revision has ordinary spacing, not this geometry.
    """
    if not page_bbox or len(words) < 3:
        return None
    words = sorted(words, key=lambda w: w[1])
    text = ' '.join(w[0] for w in words)
    left, top, right, bottom = bounds([(w, None) for w in words])
    width, height = page_bbox[2]-page_bbox[0], page_bbox[3]-page_bbox[1]
    if (top < page_bbox[1]+height*.9 or bottom-top > height*.035
            or re.search(r'(?i)\b(?:shall|must|should|required|prohibited)\b', text)):
        return None
    for at_start in (True, False):
        folio = words[0] if at_start else words[-1]
        rest = words[1:] if at_start else words[:-1]
        if not folio[0].isdigit():
            continue
        gap = rest[0][1]-folio[3] if at_start else folio[1]-rest[-1][3]
        normal_gaps = [b[1]-a[3] for a,b in zip(rest,rest[1:]) if b[1]>a[3]]
        glyph_height = median(w[4]-w[2] for w in words)
        outer = (folio[1] <= page_bbox[0]+width*.15 if at_start
                 else folio[3] >= page_bbox[0]+width*.85)
        # A wide publisher/clause footer supplies an independent signature;
        # ordinary numeric sentence endings retain the stricter gap test.
        publisher_footer = bool(
            right - left >= width * .55
            and not 1900 <= int(folio[0]) <= 2100  # a citation year is not a folio proof
            and re.search(r'(?i)\b(?:clause|chapter|section|part)\s+\d', text)
            and re.search(r'(?i)\s[-–—|]\s.*\b(?:forum|consortium|standard|institute|association)\b', text)
        )
        required_gap = max(glyph_height * (1.5 if publisher_footer else 2), median(normal_gaps) * 4) if normal_gaps else float('inf')
        if (outer and normal_gaps and gap >= required_gap
                and (FURNITURE.search(text) or gap >= width*.4)):
            return folio
    return None


def running_footer_words(words, page_bbox):
    return running_footer_folio(words, page_bbox) is not None


def non_heading_regions(page, rows):
    regions = [(box, 'displayed_formula') for box in getattr(page, 'formula_bboxes', ())]
    regions.extend((block.bbox, 'table') for block in page.blocks
                   if block.kind == DocumentBlockKind.TABLE)
    caption = None
    equations = [bounds(words) for words in rows
                 if any(re.search(r'[=≤≥∑∫]', word[0]) for word, _style in words)]
    numbered_equations = [bounds(words) for words in rows
                          if re.search(r'\([A-Z]?\d+(?:[-.]\d+)+\)\s*$',
                                       ' '.join(w[0] for w, _style in words))]
    for words in rows:
        text = ' '.join(w[0] for w, _s in words)
        bbox = bounds(words)
        styles = {style for _word, style in words}
        page_box = getattr(page, 'page_bbox', None)
        if running_footer_words([w for w, _style in words], page_box):
            regions.append((bbox, 'page_furniture'))
        if CAPTION.match(text):
            caption = (bbox, styles)
            regions.append((bbox, 'caption'))
        elif caption:
            previous, previous_styles = caption
            size = max(style[1] for _w, style in words)
            overlap = min(previous[2], bbox[2]) - max(previous[0], bbox[0])
            if (styles == previous_styles and 0 <= bbox[1]-previous[3] <= max(1, size*.25)
                    and abs((bbox[0]+bbox[2]) - (previous[0]+previous[2])) <= size*2
                    and overlap >= .8 * min(previous[2]-previous[0], bbox[2]-bbox[0])):
                regions.append((bbox, 'caption'))
                caption = (bbox, styles)
            else:
                caption = None
        # A fraction fragment must share the actual equation's vertical band
        # and horizontal extent. Short technical headings (I/O, Tx/Rx) alone
        # do not establish mathematical ownership.
        if any(eq != bbox and eq[0] <= bbox[0] and eq[2] >= bbox[2]
               and min(eq[3], bbox[3]) > max(eq[1], bbox[1]) for eq in numbered_equations):
            regions.append((bbox, 'numbered_equation_fragment'))
        if (re.search(r'[+\-–−=≤≥<>×÷*/^∑∫√]', text)
                and not re.search(r'[A-Za-z]{3,}|[\u4e00-\u9fff]', text)
                and len(text.split()) <= 12
                and any(eq != bbox and eq[0] <= bbox[0] and eq[2] >= bbox[2]
                        and min(eq[3], bbox[3]) > max(eq[1], bbox[1])
                        for eq in equations)):
            regions.append((bbox, 'mathematical_fragment'))
    return tuple(regions)
