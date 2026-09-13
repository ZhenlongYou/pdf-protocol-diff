"""Conservative comparison-only view; original PDF and page remain untouched."""
from collections import Counter
import json
import math


def exact_glyph_comparison_view(page):
    """Remove only wholly identical character dictionaries (zero tolerance).

    All provider-exposed properties enter identity, including full bounds, transform,
    font, size, advance, color space, both colors, and marked-content metadata.
    Missing required evidence or unsupported values cause retention.
    """
    required = {'text','x0','x1','top','bottom','fontname','size',
                'stroking_color','non_stroking_color','matrix','upright','adv','ncs','mcid','tag'}
    try:
        chars = page.chars
        if not isinstance(chars, (list, tuple)):
            return page
        seen, discarded = set(), set()
        for char in chars:
            if not isinstance(char, dict) or not required <= char.keys():
                continue
            if not all(isinstance(char[k], (int,float)) and math.isfinite(char[k])
                       for k in ('x0','x1','top','bottom','size')):
                continue
            try:
                key = json.dumps(char, ensure_ascii=False, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                continue
            if key in seen:
                discarded.add(id(char))
            else:
                seen.add(key)
        # If the same dictionary object occurs twice, identity filtering cannot
        # distinguish those occurrences; retain it rather than dropping both.
        repeated_ids = {key for key, count in Counter(map(id, chars)).items() if count > 1}
        discarded.difference_update(repeated_ids)
        if not discarded:
            return page
        return page.filter(lambda obj: id(obj) not in discarded)
    except Exception:
        return page
