import unittest
from types import SimpleNamespace as N
from dataclasses import replace
from copy import deepcopy
from protocol_pdf_diff.url_literal_evidence import capture_url_receipts,project_url_change
from protocol_pdf_diff.models import Section,SectionChange,SnippetPair


def fixture(text,uri='https://example.org/a-b',box=None):
    tuples=[];x=0;y=0
    for c in text:
        if c=='\n':tuples.append((c,None));y+=20;x=0;continue
        g=dict(text=c,x0=x,top=y,x1=x+1,bottom=y+10);tuples.append((c,g));x+=1
    tm=N(tuples=tuples,as_string=text,line_dir_render='ttb',char_dir_render='ltr')
    p=N(get_textmap=lambda **kwargs:tm)
    a=dict(uri=uri,x0=0,top=0,x1=max(1,x),bottom=y+10)
    if box is not None:a.update(zip(('x0','top','x1','bottom'),box))
    return p,[a]


def receipts(text,**kw):
    p,a=fixture(text,**kw);return capture_url_receipts(p,a)


def change(a,b):
    def s(text):return Section('s','1 Ref','Ref',1,('Ref',),('1',),1,1,text,page_bodies=((1,text),))
    return SectionChange('modified',s(a),s(b),.99,replaced_snippets=[SnippetPair(a,b)],audit_replaced_snippets=[SnippetPair(a,b)])


class LiteralUrlTests(unittest.TestCase):
    old='https://example.org/a‐b';new='https://example.org/a-b'
    def run_pair(self,a=None,b=None,ra=None,rb=None):
        a=a or self.old;b=b or self.new
        return project_url_change(change(a,b),tuple((1,r) for r in (ra if ra is not None else receipts(a))),tuple((1,r) for r in (rb if rb is not None else receipts(b))))
    def test_literal_positive_raw_preserved(self):
        c=change(self.old,self.new);before=deepcopy(c.audit_replaced_snippets)
        self.assertIsNone(project_url_change(c,tuple((1,r) for r in receipts(self.old)),tuple((1,r) for r in receipts(self.new))))
        self.assertEqual(c.audit_replaced_snippets,before)
    def test_samehref_numeric(self):
        self.assertIsNotNone(self.run_pair('https://example.org/800','https://example.org/900'))
    def test_extra_display(self):
        self.assertEqual(receipts(self.old+' extra'),())
    def test_wrong_box(self):
        self.assertEqual(receipts(self.old,box=(0,0,2,10)),())
    def test_different_href(self):
        self.assertIsNotNone(self.run_pair(rb=receipts(self.new,uri='https://else.org/a-b')))
    def test_duplicate_url(self):
        self.assertEqual(receipts(self.old+'\n'+self.old),())
    def test_missing_coordinates(self):
        p,a=fixture(self.old);del p.get_textmap().tuples[2][1]['x0']
        self.assertEqual(capture_url_receipts(p,a),())
    def test_nan_box(self):
        self.assertEqual(receipts(self.old,box=(0,0,float('nan'),10)),())
    def test_same_baseline_spaces(self):
        self.assertEqual(receipts('https://example.org/a‐ b'),())
    def test_description_not_url(self):
        self.assertEqual(receipts('The receiver shall tolerate 800 mV.'),())
    def test_missing_source_page(self):
        c=change(self.old,self.new);c=replace(c,old_section=replace(c.old_section,page_bodies=()))
        self.assertIs(project_url_change(c,((1,receipts(self.old)[0]),),((1,receipts(self.new)[0]),)),c)
    def test_foreign_section(self):
        c=change(self.old,self.new);c=replace(c,new_section=replace(c.new_section,heading_path=('Other',)))
        self.assertIs(project_url_change(c,((1,receipts(self.old)[0]),),((1,receipts(self.new)[0]),)),c)
    def test_neighbor_numeric_change_preserved(self):
        a=self.old+'\nVoltage 800 mV';b=self.new+'\nVoltage 900 mV'
        ra=receipts(self.old);rb=receipts(self.new)
        z=self.run_pair(a,b,ra,rb)
        self.assertIn('800',z.replaced_snippets[0].old);self.assertIn('900',z.replaced_snippets[0].new)
    def test_missing_glyph_receipt(self):
        ra=deepcopy(receipts(self.old));ra[0]['glyphs']=ra[0]['glyphs'][:-1]
        self.assertIsNotNone(self.run_pair(ra=ra))
    def test_url_prefix_not_owned(self):
        self.assertIsNotNone(self.run_pair(self.old+'more',self.new+'more',receipts(self.old),receipts(self.new)))
    def test_pua(self):
        self.assertEqual(receipts(self.old.replace('‐','\uf02d')),())
    def test_shared_annotation_conflict(self):
        p,a=fixture(self.old);a.append(dict(a[0],uri='https://else.org/a-b'))
        self.assertEqual(capture_url_receipts(p,a),())

    def test_legacy_extraction_page_without_receipts(self):
        from protocol_pdf_diff.url_literal_evidence import extraction_url_receipts
        self.assertEqual(extraction_url_receipts(N(pages=[N(page_number=1,text='legacy page')])),())
