import unittest
from dataclasses import replace
from protocol_pdf_diff.models import Section,SectionChange,SnippetPair
from protocol_pdf_diff.bullet_context_review import partial_bullet_context_review as project
from protocol_pdf_diff.reporting import _change_to_dict,_rows_for_csv,_render_review_pairs_html
A='Reach 1000 mm with 2 connectors'
B='The receiver shall tolerate 800 mV.'
OLD='• Alpha\nRate 20 GBd\n'+A+'\n• Beta\nRate 40 GBd\n'+A+'\n'+B
NEW=OLD.replace('\n'+B,'\n• Gamma\nRate 50 GBd\n'+B)
def section(text,p=1):
    return Section('s','1 Overview','Overview',1,('Overview',),('1',),p,p,text,page_bodies=((p,text),))
def change(old=OLD,new=NEW):
    return SectionChange('modified',section(old),section(new,2),0.9,
        added_snippets=[A,'• Gamma','Rate 50 GBd'],replaced_snippets=[SnippetPair(A+' '+B,B)],
        audit_added_snippets=[A,'• Gamma','Rate 50 GBd'],audit_removed_snippets=[],audit_replaced_snippets=[SnippetPair(A+' '+B,B)])
class PartialContextReviewTests(unittest.TestCase):
    def test_800_scope_change_is_explicitly_unknown(self):
        raw=change();out=project(raw,raw)
        self.assertEqual(['• Gamma','Rate 50 GBd'],out.added_snippets)
        self.assertEqual([],out.replaced_snippets)
        self.assertEqual(1,len(out.context_review_records))
        record=out.context_review_records[0]
        self.assertEqual(B,record['old']['text']);self.assertEqual(B,record['new']['text'])
        self.assertIn('Beta\nRate 40',record['old']['source_context'])
        self.assertIn('Gamma\nRate 50',record['new']['source_context'])
        self.assertIn('800',out.review_replaced_snippets[0].old)
        self.assertIn('800',out.review_replaced_snippets[0].new)
        self.assertIn('待核实',out.review_reason)
        self.assertEqual(raw.audit_replaced_snippets,out.audit_replaced_snippets)
        self.assertEqual([A,'• Gamma','Rate 50 GBd'],raw.added_snippets)
    def test_serialized_json_csv_and_html_retain_unknown(self):
        raw=change();out=project(raw,raw)
        d=_change_to_dict(raw,display_change=out)
        self.assertEqual(1,len(d['context_review_records']))
        self.assertIn(A,d['replaced_snippets'][0]['old'])
        self.assertEqual([],d['display_replaced_snippets'])
        csv=_rows_for_csv([out])[0]
        self.assertIn('800',csv['context_review_records']);self.assertIn('Gamma',csv['context_review_records'])
        self.assertIn('待核实',csv['summary'])
        html=_render_review_pairs_html(out.review_replaced_snippets,context_scope=True)
        self.assertIn('800',html);self.assertNotIn('结构顺延',html)
    def test_source_images_use_real_unannotated_pixels(self):
        from protocol_pdf_diff.models import ProseSourceVisual,ProseSourceVisualGroup
        from protocol_pdf_diff.reporting import _render_change_html
        raw=change();out=project(raw,raw)
        visual=ProseSourceVisual(1,(0,0,100,100),'colored-pixels',1,1,raw_image_data_uri='unannotated-pixels')
        group=ProseSourceVisualGroup('modified','s','s',(visual,),(visual,))
        html=_render_change_html(1,out,prose_source_visual=group)
        self.assertIn('src="unannotated-pixels"',html)
        self.assertNotIn('src="colored-pixels"',html)
        self.assertIn('不作新增或删除标色',html)

    def test_missing_page_context_retains_original(self):
        raw=change();raw=replace(raw,old_section=replace(raw.old_section,page_bodies=()))
        self.assertEqual(raw,project(raw,raw))
    def test_incomplete_page_context_retains_original(self):
        raw=change();raw=replace(raw,new_section=replace(raw.new_section,page_bodies=((2,B),)))
        self.assertEqual(raw,project(raw,raw))
    def test_number_change_retains_original(self):
        raw=change(new=NEW.replace('1000','1100'));self.assertEqual(raw,project(raw,raw))
    def test_condition_change_before_a_retains_original(self):
        raw=change(new=NEW.replace('Rate 40','Rate 41'));self.assertEqual(raw,project(raw,raw))
    def test_duplicate_owner_retains_original(self):
        raw=change(OLD.replace('Beta','Alpha'),NEW.replace('Beta','Alpha'));self.assertEqual(raw,project(raw,raw))
    def test_a_under_other_owner_retains_original(self):
        raw=change(new=NEW.replace('Beta','Delta'));self.assertEqual(raw,project(raw,raw))
    def test_missing_a_retains_original(self):
        raw=change(new=NEW.replace(A,'Absent'));self.assertEqual(raw,project(raw,raw))
    def test_missing_raw_body_retains_original(self):
        raw=change(old='');self.assertEqual(raw,project(raw,raw))
    def test_structured_table_retains_original(self):
        raw=change(OLD.replace('Rate 20','Min | Max'),NEW.replace('Rate 20','Min | Max'));self.assertEqual(raw,project(raw,raw))
    def test_actual_b_numeric_change_retains_original(self):
        raw=change(new=NEW.replace('800','900'));self.assertEqual(raw,project(raw,raw))
    def test_repeated_projection_does_not_duplicate_review(self):
        raw=change();out=project(raw,raw);self.assertEqual(out,project(out,raw))
if __name__=='__main__':unittest.main()
