"""Conservative comparison-only view; original PDF and page remain untouched."""
import json
import math
from collections import Counter


def _normalized_visual_value(value):
    """Normalize numeric representation without relaxing visual identity."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("number", float(value))
    if isinstance(value, (list, tuple)):
        return tuple(_normalized_visual_value(item) for item in value)
    return value


def deduplicate_figure_text_layers(page, graphic_bboxes):
    """Remove only co-located duplicate text layers inside proven graphics.

    Some PDFs paint the same figure label several times at one physical
    position.  Their metadata may differ only in representation (for example
    ``0`` versus ``0.0`` colors), so the strict whole-dictionary comparison
    cannot identify them.  This view deliberately ignores marked-content
    bookkeeping while retaining text, geometry, font, transform, size,
    advance, color and color-space identity.  Text outside a proven graphic
    bbox is never changed.
    """

    if not graphic_bboxes:
        return page
    try:
        chars = page.chars
        if not isinstance(chars, (list, tuple)):
            return page
        groups = {}
        for char in chars:
            if not isinstance(char, dict) or not char.get("text", "").strip():
                continue
            try:
                x0 = float(char["x0"])
                x1 = float(char["x1"])
                top = float(char["top"])
                bottom = float(char["bottom"])
            except (KeyError, TypeError, ValueError):
                continue
            if not any(
                left <= x0 and x1 <= right and upper <= top and bottom <= lower
                for left, upper, right, lower in graphic_bboxes
            ):
                continue
            key = (
                char.get("text"),
                x0,
                x1,
                top,
                bottom,
                char.get("fontname"),
                _normalized_visual_value(char.get("size")),
                _normalized_visual_value(char.get("adv")),
                _normalized_visual_value(char.get("matrix")),
                _normalized_visual_value(char.get("stroking_color")),
                _normalized_visual_value(char.get("non_stroking_color")),
                char.get("ncs"),
                char.get("upright"),
            )
            groups.setdefault(key, []).append(char)
        discarded = {
            id(char)
            for group in groups.values()
            if len(group) > 1
            for char in group[1:]
        }
        if not discarded:
            return page
        return page.filter(lambda obj: id(obj) not in discarded)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return page


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
