"""Fail-closed extraction character identity for unchanged native page order."""

import math, re
import unicodedata
from collections import Counter, defaultdict
from .text_utils import compact_inline


def has_unproven_private_use(value):
    """Private-use codepoints have no glyph identity without a source-font receipt.

    Neither existing word nor character ownership records carry that receipt.
    Include supplementary private-use planes, not only legacy Symbol BMP codes.
    """
    return any(unicodedata.category(char) == "Co" for char in value)


def source_char_map_for_final_text(text, textmap):
    if textmap.line_dir_render != "ttb" or textmap.char_dir_render != "ltr":
        return ()
    raw = "".join(s for s, _ in textmap.tuples)
    if raw != textmap.as_string or "".join(raw.split()) != "".join(text.split()):
        return ()
    output = []
    ids = {}
    for value, glyph in textmap.tuples:
        for char in value:
            if char.isspace():
                continue
            if not isinstance(glyph, dict):
                return ()
            try:
                box = tuple(float(glyph[k]) for k in ("x0", "top", "x1", "bottom"))
            except (KeyError, TypeError, ValueError):
                return ()
            if (
                not all(math.isfinite(v) for v in box)
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                return ()
            gid = ids.setdefault(id(glyph), len(ids))
            output.append((char, *box, gid))
    return tuple(output)


def inside(char, box):
    return (
        box[0] <= char[1] < char[3] <= box[2] and box[1] <= char[2] < char[4] <= box[3]
    )


def char_owned_candidate(value, section, pages, boxes):
    value = compact_inline(value)
    if has_unproven_private_use(value):
        return None
    found = []
    for page in pages:
        if not section.start_page <= page.page_number <= section.end_page:
            continue
        content = compact_inline(page.text)
        starts = [
            m.start()
            for m in re.finditer("(?=" + re.escape(value) + ")", content)
            if (m.start() == 0 or content[m.start() - 1].isspace())
            and (
                m.start() + len(value) == len(content)
                or content[m.start() + len(value)].isspace()
            )
        ]
        for start in starts:
            found.append((page, content, start))
    if len(found) != 1:
        return None
    page, content, start = found[0]
    mapping = getattr(page, "source_char_map", ())
    if page.ocr_used or "".join(c[0] for c in mapping) != "".join(content.split()):
        return None
    count = sum(not c.isspace() for c in content[:start])
    length = sum(not c.isspace() for c in value)
    chars = mapping[count : count + length]
    if "".join(c[0] for c in chars) != "".join(value.split()):
        return None
    used = {c[5] for c in chars}
    if any(c[5] in used for c in (*mapping[:count], *mapping[count + length :])):
        return None
    # The certificate removes whole whitespace-delimited source tokens, only
    # at the snippet ends. Interior labels remain unresolved in this candidate.
    tokens = list(re.finditer(r"\S+", value))
    cursor = 0
    tokenchars = []
    for token in tokens:
        chunk = chars[cursor : cursor + len(token.group())]
        cursor += len(token.group())
        tokenchars.append(chunk)
    proofs = []
    for item in boxes.get(page.page_number, ()):
        # Unbound boxes cannot prove correspondence to a particular other figure.
        if len(item) != 3:
            continue
        box, caption, visual_group = item
        if not visual_group:
            continue
        owned = [all(inside(c, box) for c in chunk) for chunk in tokenchars]
        a = 0
        while a < len(owned) and owned[a]:
            a += 1
        b = len(owned)
        while b > a and owned[b - 1]:
            b -= 1
        spans = []
        if a:
            spans.append((0, tokens[a - 1].end()))
        if b < len(owned):
            spans.append((tokens[b].start(), len(value)))
        if not spans or any(
            re.search(r"(?i)\b(?:shall|should|must|required|prohibited)\b", value[x:y])
            for x, y in spans
        ):
            continue
        removed = []
        pos = 0
        for i, char in enumerate(value):
            if char.isspace():
                continue
            if any(x <= i < y for x, y in spans):
                removed.append(chars[pos])
            pos += 1
        proofs.append(
            {
                "spans": spans,
                "chars": removed,
                "box": box,
                "caption": compact_inline(caption),
                "visual_group": visual_group,
            }
        )
    # Multiple nested actual frames with equivalent coverage do not invent
    # ownership; require one maximal enclosing physical box for this proof.
    proofs = [
        p
        for p in proofs
        if not any(
            p is not q
            and p["box"] != q["box"]
            and q["box"][0] <= p["box"][0]
            and q["box"][1] <= p["box"][1]
            and p["box"][2] <= q["box"][2]
            and p["box"][3] <= q["box"][3]
            for q in proofs
        )
    ]
    if len(proofs) != 1:
        return None
    return proofs[0]


def equivalent_char_ownership(a, b):
    if any(has_unproven_private_use(char[0])
           for proof in (a, b) for char in proof["chars"]):
        return False  # Equal PUA values and coordinates do not prove equal glyphs.
    if (
        not a.get("visual_group")
        or a["visual_group"] != b.get("visual_group")
        or a["caption"] != b["caption"]
        or len(a["chars"]) != len(b["chars"])
    ):
        return False

    def coord(c, box):
        return (
            (c[1] - box[0]) / (box[2] - box[0]),
            (c[2] - box[1]) / (box[3] - box[1]),
            (c[3] - box[0]) / (box[2] - box[0]),
            (c[4] - box[1]) / (box[3] - box[1]),
        )

    if len(a["chars"]) > 1024:
        return False  # Refuse excessive work; never infer equivalence.
    buckets = defaultdict(list)
    for j, y in enumerate(b["chars"]):
        buckets[y[0]].append((j, y))
    old_degrees = Counter()
    new_degrees = Counter()
    scale = [b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]] * 2
    for i, x in enumerate(a["chars"]):
        ac = coord(x, a["box"])
        for j, y in buckets.get(x[0], ()):
            bc = coord(y, b["box"])
            if all(abs(v - w) * z <= 1 for v, w, z in zip(ac, bc, scale)):
                old_degrees[i] += 1
                new_degrees[j] += 1
    return all(old_degrees[k] == 1 for k in range(len(a["chars"]))) and all(
        new_degrees[k] == 1 for k in range(len(b["chars"]))
    )
