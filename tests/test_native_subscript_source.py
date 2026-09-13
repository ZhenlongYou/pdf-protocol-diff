import unittest
from types import SimpleNamespace as N
from protocol_pdf_diff.native_subscript_source import repair_native_subscripts as repair
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order_with_duplicate_fallback as public


def fixture(unstyled_third=False):
    words=[];tuples=[]
    for row in range(3 if unstyled_third else 2):
        glyph_words=[]
        for text,x,lower in [('3',0,False),('for',10,False),('f',30,False),('b',35,True),('=29',45,False),('GHz).',70,False)]:
            y=100+row*100+(4.8 if lower and row!=2 else 0);size=9.6 if lower and row!=2 else 12
            chars=[dict(text=c,x0=x+n*5,x1=x+(n+1)*5,top=y,bottom=y+size) for n,c in enumerate(text)]
            w=dict(text=text,x0=x,x1=x+len(text)*5,top=y,bottom=y+size,chars=chars);words.append(w);glyph_words.append(w)
        order=[3,0,1,2,4,5] if row==1 and unstyled_third else [0,1,2,3,4,5]
        for index in order:
            if tuples and tuples[-1][0]!='\n':tuples.append((' ',None))
            tuples.extend((g['text'],g) for g in glyph_words[index]['chars'])
        if row<(2 if unstyled_third else 1):tuples.append(('\n',None))
    text=''.join(v for v,g in tuples)
    tm=N(as_string=text,tuples=tuples,line_dir_render='ttb',char_dir_render='ltr')
    page=N(get_textmap=lambda **kw:tm,extract_words=lambda **kw:words)
    observed=[{k:w[k] for k in ('text','x0','x1','top','bottom')} for w in words]
    return text,page,observed,tm,words


class NativeSubscriptSourceTests(unittest.TestCase):
    def test_positive_repeated_each_native_owner(self):
        text,page,words,tm,source=fixture()
        self.assertEqual('3 for fb =29 GHz).\n3 for fb =29 GHz).',repair(text,page,words))

    def test_independent_p1_actual_entry_preserves_unstyled_third(self):
        text,page,words,tm,source=fixture(True)
        result=public(text,page,words,[],None)
        self.assertEqual(result.splitlines()[0],'3 for fb =29 GHz).')
        self.assertEqual(result.splitlines()[1],text.splitlines()[1])
        self.assertEqual(result.splitlines()[2],text.splitlines()[2])

    def test_native_order_mismatch_rejects(self):
        text,page,words,tm,source=fixture();changed=text.replace('3 for','for 3',1)
        self.assertEqual(changed,repair(changed,page,words))

    def test_missing_word_mapping_rejects(self):
        text,page,words,tm,source=fixture()
        self.assertEqual(text,repair(text,page,words[:-1]))

    def test_reused_native_glyph_rejects(self):
        text,page,words,tm,source=fixture()
        tm.tuples[-1]=(tm.tuples[-1][0],tm.tuples[0][1])
        self.assertEqual(text,repair(text,page,words))

    def test_exact_overpaint_rejects(self):
        text,page,words,tm,source=fixture();g=source[3]['chars'][0];h=source[2]['chars'][0]
        g.update({k:h[k] for k in ('x0','x1','top','bottom')})
        self.assertEqual(text,repair(text,page,words))

    def test_source_word_membership_mismatch_rejects(self):
        text,page,words,tm,source=fixture();source[3]['chars']=source[9]['chars']
        self.assertEqual(text,repair(text,page,words))

    def test_changed_value_stays_changed(self):
        text,page,words,tm,source=fixture();source[4]['text']='=30';words[4]['text']='=30'
        source[4]['chars'][1]['text']='3';source[4]['chars'][2]['text']='0'
        tm.tuples=[(g['text'] if g else v,g) for v,g in tm.tuples];tm.as_string=''.join(v for v,g in tm.tuples)
        result=repair(tm.as_string,page,words)
        self.assertIn('=30',result);self.assertIn('=29',result)

    def test_intervening_foreign_body_is_not_crossed(self):
        text,page,words,tm,source=fixture()
        chars=[dict(text=c,x0=120+n*5,x1=125+n*5,top=400,bottom=412) for n,c in enumerate('WARNING900')]
        w=dict(text='WARNING900',x0=120,x1=170,top=400,bottom=412,chars=chars)
        source.append(w);words.append({k:w[k] for k in ('text','x0','x1','top','bottom')})
        start=next(i for i,(v,g) in enumerate(tm.tuples) if g is source[3]['chars'][0])
        tm.tuples[start:start]=[(g['text'],g) for g in chars]+[(' ',None)]
        tm.as_string=''.join(v for v,g in tm.tuples)
        result=repair(tm.as_string,page,words)
        self.assertTrue(result.startswith('3 for f WARNING900 b =29 GHz).'))
        self.assertIn('WARNING900',result)

if __name__=='__main__':unittest.main()
