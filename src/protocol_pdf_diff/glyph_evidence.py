"""Map only source-proven empty TrueType glyphs to spaces, without global hooks.

The adapter keeps the original Unicode, actual CID/GID and embedded-font digest.
Unsupported fonts and malformed evidence retain their original text. Geometry,
advance, marked content and rendering are inherited from pdfplumber/pdfminer.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdftypes import PDFStream, dict_value, resolve1
from pdfminer.psparser import PSLiteral
from pdfplumber.page import Page, PDFPageAggregatorWithMarkedContent


@dataclass(frozen=True)
class EmptyGlyphFont:
    digest: str
    empty_gids: frozenset[int]
    cid_to_gid: bytes | None  # None means ISO 32000 Identity, explicit or default.

    def glyph(self, cid: int) -> int | None:
        if cid < 0:
            return None
        if self.cid_to_gid is None:
            gid = cid
        elif 2 * cid + 2 <= len(self.cid_to_gid):
            gid = int.from_bytes(self.cid_to_gid[2 * cid:2 * cid + 2], "big")
        else:
            return None
        # GID 0 is .notdef: missing text is not evidence of a whitespace character.
        return gid if gid != 0 and gid in self.empty_gids else None


def empty_truetype_glyphs(data: bytes) -> frozenset[int]:
    """Read bounded SFNT tables; only zero-length glyf entries prove no outline."""
    try:
        if len(data) < 12 or data[:4] not in (b"\x00\x01\x00\x00", b"true"):
            return frozenset()
        count = struct.unpack_from(">H", data, 4)[0]
        if 12 + count * 16 > len(data):
            return frozenset()
        tables = {}
        for index in range(count):
            tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, 12 + index * 16)
            if tag in tables or offset < 12 + count * 16 or offset + length > len(data):
                return frozenset()
            tables[tag] = data[offset:offset + length]
        if set(tables) & {b"CFF ", b"CFF2", b"COLR", b"SVG ", b"CBDT", b"EBDT", b"sbix"}:
            return frozenset()
        head, maxp, loca, glyf = (tables[tag] for tag in (b"head", b"maxp", b"loca", b"glyf"))
        if len(head) < 54 or len(maxp) < 6:
            return frozenset()
        form = struct.unpack_from(">h", head, 50)[0]
        glyph_count = struct.unpack_from(">H", maxp, 4)[0]
        if form not in (0, 1):
            return frozenset()
        width = 2 if form == 0 else 4
        if len(loca) != (glyph_count + 1) * width:
            return frozenset()
        offsets = [int.from_bytes(loca[i:i + width], "big") * (2 if form == 0 else 1)
                   for i in range(0, len(loca), width)]
        if any(a > b or b > len(glyf) for a, b in zip(offsets, offsets[1:])):
            return frozenset()
        return frozenset(i for i, (a, b) in enumerate(zip(offsets, offsets[1:])) if a == b and i)
    except (KeyError, ValueError, struct.error):
        return frozenset()


def _font_evidence(spec) -> EmptyGlyphFont | None:
    try:
        subtype = resolve1(spec.get("Subtype"))
        if not isinstance(subtype, PSLiteral) or subtype.name != "CIDFontType2":
            return None
        descriptor = dict_value(spec.get("FontDescriptor"))
        stream = resolve1(descriptor.get("FontFile2"))
        mapping = resolve1(spec.get("CIDToGIDMap"))
        if not isinstance(stream, PDFStream):
            return None
        # ISO 32000-1:2008, 9.7.4 Table 117: omitted CIDToGIDMap defaults to Identity.
        # https://opensource.adobe.com/dc-acrobat-sdk-docs/standards/pdfstandards/pdf/PDF32000_2008.pdf
        if "CIDToGIDMap" not in spec or (isinstance(mapping, PSLiteral) and mapping.name == "Identity"):
            cid_map = None
        elif isinstance(mapping, PDFStream):
            cid_map = mapping.get_data()
            if len(cid_map) % 2:
                return None
        else:
            return None
        data = stream.get_data()
        gids = empty_truetype_glyphs(data)
        if not gids:
            return None
        return EmptyGlyphFont(hashlib.sha256(data).hexdigest(), gids, cid_map)
    except Exception:
        return None  # Font inspection can never authorize deletion on an error.


class EvidenceResourceManager(PDFResourceManager):
    def get_font(self, objid, spec):
        font = super().get_font(objid, spec)
        if not hasattr(font, "_empty_glyph_evidence"):
            font._empty_glyph_evidence = _font_evidence(spec)
        return font


class EvidenceAggregator(PDFPageAggregatorWithMarkedContent):
    def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):
        advance = super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate)
        proof = getattr(font, "_empty_glyph_evidence", None)
        if proof is not None and advance > 0 and not font.is_vertical():
            gid = proof.glyph(cid)
            if gid is not None:
                char = self.cur_item._objs[-1]
                original = char.get_text()
                if original and not original.isspace():
                    char._source_blank_glyph = (original, proof.digest, cid, gid)
                    char._text = " "  # Preserve separation; do not concatenate adjacent tokens.
        return advance


class EvidencePage(Page):
    @property
    def layout(self):
        if not hasattr(self, "_layout"):
            device = EvidenceAggregator(self.pdf.rsrcmgr, pageno=self.page_number,
                                        laparams=self.pdf.laparams)
            PDFPageInterpreter(self.pdf.rsrcmgr, device).process_page(self.page_obj)
            self._layout = device.get_result()
        return self._layout

    def process_object(self, obj):
        result = super().process_object(obj)
        proof = getattr(obj, "_source_blank_glyph", None)
        if proof is not None:
            result["source_blank_glyph"] = proof
        return result


def source_blank_glyphs(page):
    return tuple(((c["x0"], c["top"], c["x1"], c["bottom"]), *c["source_blank_glyph"])
                 for c in getattr(page, "chars", ()) if "source_blank_glyph" in c)


def evidence_page(pdf, page, index):
    """Wrap genuine pdfplumber pages; alternate page providers remain unchanged."""
    if not isinstance(page, Page):
        return page
    return EvidencePage(pdf, page.page_obj, page_number=index,
                        initial_doctop=page.initial_doctop)
