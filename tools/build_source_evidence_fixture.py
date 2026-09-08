"""Regenerate the owned tiny TrueType glyph fixture (requires fontTools).

The font is drawn here from rectangles; no third-party font data is included.
The PDFs are also generated locally and may be redistributed with the tests.
"""
from pathlib import Path
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import fitz

ROOT = Path(__file__).resolve().parents[1] / 'tests/fixtures/source_evidence'
ROOT.mkdir(parents=True, exist_ok=True)
chars = list(range(32, 127)) + [0xF020, 0xF021]
names = ['.notdef'] + [f'u{c:04X}' for c in chars]
builder = FontBuilder(1000, isTTF=True)
builder.setupGlyphOrder(names)
builder.setupCharacterMap({c:f'u{c:04X}' for c in chars})
glyphs = {}
for name in names:
    pen = TTGlyphPen(None)
    if name not in ['u0020', 'uF020']:
        pen.moveTo((50, 0)); pen.lineTo((450, 0)); pen.lineTo((450, 700)); pen.lineTo((50, 700)); pen.closePath()
    glyphs[name] = pen.glyph()
builder.setupGlyf(glyphs)
builder.setupHorizontalMetrics({name:(500,0) for name in names})
builder.setupHorizontalHeader(ascent=800, descent=-200)
builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200)
builder.setupNameTable({'familyName':'OwnedGlyphEvidence','styleName':'Regular','uniqueFontIdentifier':'OwnedGlyphEvidence1','fullName':'OwnedGlyphEvidence','psName':'OwnedGlyphEvidence'})
builder.setupPost()
builder.setupMaxp()
builder.font['head'].created = builder.font['head'].modified = 2082844800
builder.font.recalcTimestamp = False
font = ROOT/'owned-glyph-evidence.ttf'
builder.save(font)
for variant, text in [('empty', 'LEFT\uf020RIGHT'),('visible', 'LEFT\uf021RIGHT')]:
    doc = fitz.open(); page = doc.new_page()
    page.insert_font(fontname='Owned',fontfile=str(font))
    page.insert_text((72,120),text,fontname='Owned',fontsize=14)
    font_xref = page.get_fonts()[0][0]
    descendant = int(doc.xref_get_key(font_xref, 'DescendantFonts')[1].strip('[]').split()[0])
    doc.xref_set_key(descendant, 'CIDToGIDMap', '/Identity')
    doc.save(ROOT/(variant+'.pdf'), no_new_id=True); doc.close()
print(ROOT)
