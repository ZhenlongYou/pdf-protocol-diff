"""Source discovery/TextMap bridge; produces receipts, never removes text."""

import re

import fitz
import pdfplumber
from pdfminer.converter import PDFPageAggregator
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage

from protocol_pdf_diff.exact_glyph_view import exact_glyph_comparison_view
from protocol_pdf_diff.glyph_evidence import EvidenceResourceManager, evidence_page


class Resources(PDFResourceManager):
    def get_font(self, objid, spec):
        font = super().get_font(objid, spec)
        font.source_objid = objid
        return font


def discover(path, pn):
    with fitz.open(path) as d:
        rects = {
            tuple(x["scissor"])
            for x in d[pn - 1].get_drawings(extended=True)
            if x["type"] == "clip"
        }
    with pdfplumber.open(path, pages=[pn]) as q:
        page = q.pages[0]
        candidates = []
        for b in rects:
            box = fitz.Rect(b)
            chars = [
                c
                for c in page.chars
                if box.contains(fitz.Rect(c["x0"], c["top"], c["x1"], c["bottom"]))
                and not c["text"].isspace()
            ]
            lines = [
                l
                for l in page.lines
                if l["top"] == l["bottom"]
                and box.contains(fitz.Rect(l["x0"], l["top"], l["x1"], l["bottom"]))
            ]
            if len(lines) == 1 and len({c["size"] for c in chars}) > 1 and chars:
                candidates.append(b)
        # A clip is only a source-region locator; this module asserts no visual equality.
        return [
            b
            for b in candidates
            if not any(
                b != c and fitz.Rect(b).contains(fitz.Rect(c)) for c in candidates
            )
        ]


class C(PDFPageAggregator):
    def __init__(self, r, roi, height):
        super().__init__(r)
        self.roi = fitz.Rect(roi)
        self.height = height
        self.glyphs = []

    def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, gs):
        adv = super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, gs)
        ch = self.cur_item._objs[-1]
        x0, y0, x1, y1 = ch.bbox
        box = (x0, self.height - y1, x1, self.height - y0)
        if (
            self.roi.intersects(fitz.Rect(box))
            and not self.roi.contains(fitz.Rect(box))
            and not ch.get_text().isspace()
        ):
            raise ValueError("partial_roi_glyph")
        if self.roi.contains(fitz.Rect(box)) and not ch.get_text().isspace():
            text = ch.get_text()
            self.glyphs.append(
                {
                    "text": text,
                    "bbox": box,
                    "cid": cid,
                    "font_object": font.source_objid,
                }
            )
        return adv


def bridge(path, pn, roi, final, noise_boxes=()):
    with pdfplumber.open(path, pages=[pn]) as p:
        p.rsrcmgr = EvidenceResourceManager()
        page = exact_glyph_comparison_view(evidence_page(p, p.pages[0], pn))
        page = page.filter(
            lambda obj: (
                not any(
                    box[0] <= obj.get("x0", -1e9)
                    and obj.get("x1", 1e9) <= box[2]
                    and box[1] <= obj.get("top", -1e9)
                    and obj.get("bottom", 1e9) <= box[3]
                    for box in noise_boxes
                )
            )
        )
        tm = page.get_textmap(x_tolerance=1, y_tolerance=3)
        tuples = list(tm.tuples)
        height = page.height
    r = Resources()
    c = C(r, roi, height)
    it = PDFPageInterpreter(r, c)
    with open(path, "rb") as f:
        for p in PDFPage.get_pages(f, pagenos={pn - 1}):
            it.process_page(p)
    mapped = []
    for idx, g in enumerate(c.glyphs):
        matches = [
            (j, s)
            for j, (s, x) in enumerate(tuples)
            if x
            and (x["x0"], x["top"], x["x1"], x["bottom"]) == tuple(g["bbox"])
            and s == g["text"]
        ]
        if len(matches) != 1:
            raise ValueError("missing_or_ambiguous_native_binding")
        mapped.append({"native_index": idx, "textmap_index": matches[0][0], **g})
    if len({x["textmap_index"] for x in mapped}) != len(mapped):
        raise ValueError("shared_textmap_glyph")
    # Map each complete TextMap line, with all punctuation/line-number characters,
    # to one exact final-text line; formula-specific synthetic table content is irrelevant.
    starts = []
    pos = 0
    for line in tm.as_string.splitlines(keepends=True):
        starts.append((pos, pos + len(line), line.strip()))
        pos += len(line)
    rows = []
    for lo, hi, line in starts:
        owned = [g for g in mapped if lo <= g["textmap_index"] < hi]
        if not owned:
            continue
        locations = [
            m.start() for m in re.finditer(r"(?m)^" + re.escape(line) + r"$", final)
        ]
        if len(locations) != 1:
            raise ValueError("missing_or_duplicate_final_line:" + line)
        for g in owned:
            g["final_index"] = locations[0] + g["textmap_index"] - lo
        rows.append(
            {
                "textmap_line": line,
                "final_start": locations[0],
                "glyph_indices": [g["native_index"] for g in owned],
            }
        )
    if any(final[g["final_index"]] != g["text"] for g in mapped):
        raise ValueError("final_character_offset_mismatch")
    return {"roi": roi, "glyphs": mapped, "line_bindings": rows}
