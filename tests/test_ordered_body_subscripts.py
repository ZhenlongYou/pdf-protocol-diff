import copy,json
from pathlib import Path
import unittest
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order as repair

class OrderedBodySubscriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items=json.loads((Path(__file__).parent/'fixtures/content-correspondence/ordered-body-subscripts.json').read_text())

    def test_real_four_source_lines(self):
        results=[]
        for item in self.items:
            text=repair(item['text'],item['words']);results.append(text)
        self.assertEqual(results[0],results[1]);self.assertIn('RI for',results[0])
        self.assertEqual(results[2],results[3]);self.assertTrue(results[2].startswith('J3u , JRMS , and EOJ'))

    def test_unrelated_inserted_text_cannot_be_crossed(self):
        item=self.items[2];text=item['text'].replace('3u\n','3u\nshall not reverse polarity\n')
        self.assertEqual(repair(text,item['words']),text)

    def test_duplicate_source_and_missing_word_fail_closed(self):
        item=self.items[2]
        self.assertEqual(repair(item['text'],item['words'][:-1]),item['text'])
        self.assertEqual(repair(item['text']+'\n'+item['text'],item['words']*2),item['text']+'\n'+item['text'])

    def test_real_baseline_R_I_is_not_joined(self):
        item=copy.deepcopy(self.items[0]);base=next(w for w in item['words'] if w['text']=='R');suffix=next(w for w in item['words'] if w['text']=='I')
        suffix.update(top=base['top'],bottom=base['bottom'],size=base['size'])
        self.assertEqual(repair(item['text'],item['words']),item['text'])

    def test_changed_suffix_relationship_is_preserved(self):
        item=copy.deepcopy(self.items[2]);source=repair(item['text'],item['words'])
        for w in item['words']:
            if w['text']=='3u':w['text']='RMS'
            elif w['text']=='RMS':w['text']='3u'
        changed=item['text'].replace('3u','TEMP').replace('RMS','3u').replace('TEMP','RMS')
        output=repair(changed,item['words'])
        self.assertNotEqual(output,source)
        self.assertTrue(output.startswith('JRMS , J3u , and EOJ'))

    def test_same_characters_with_swapped_values_remain_different(self):
        words=[]
        for text,x,lowered in [('J',0,False),('3u',6,True),('=0.1',22,False),('J',60,False),('RMS',66,True),('=0.2',92,False)]:
            top=105 if lowered else 100;size=9.6 if lowered else 12
            words.append(dict(text=text,x0=x,x1=x+(6 if text=='J' else 12),top=top,bottom=top+size))
        raw='J\n3u\n=0.1 J\nRMS\n=0.2'
        first=repair(raw,words)
        changed=copy.deepcopy(words)
        changed[2]['text']='=0.2';changed[5]['text']='=0.1'
        second=repair(raw.replace('0.1','TEMP').replace('0.2','0.1').replace('TEMP','0.2'),changed)
        self.assertIn('J3u =0.1',first);self.assertIn('JRMS =0.2',first)
        self.assertIn('J3u =0.2',second);self.assertIn('JRMS =0.1',second)
        self.assertNotEqual(first,second)

if __name__=='__main__':unittest.main()
