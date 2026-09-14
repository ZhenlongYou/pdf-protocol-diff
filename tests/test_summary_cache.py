import copy
import unittest
from unittest.mock import patch

from protocol_pdf_diff import compare as c
from protocol_pdf_diff.comparison_session import _caches, comparison_scope
from protocol_pdf_diff.models import SnippetPair


class SummaryCacheTests(unittest.TestCase):
 def test_complete_key_and_return_isolation(self):
  original=c._compute_summarize_text_delta
  with patch.object(c,'_compute_summarize_text_delta',wraps=original) as work,comparison_scope():
   args=('Voltage shall be 800 mV.','Voltage shall be 900 mV.',12)
   expected=c._summarize_text_delta(*args);snapshot=copy.deepcopy(expected)
   expected[2].append(SnippetPair('bad','bad'));expected[0].append('poison')
   self.assertEqual(c._summarize_text_delta(*args),snapshot);self.assertEqual(work.call_count,1)
   cases=[(('Other old',args[1],12),{}),((args[0],'Other new',12),{}),((args[0],args[1],1),{}),(args,{'leading_replacement':SnippetPair('heading A','heading B')}),(args,{'suppressed_old_table_unit_keys':{'old'}}),(args,{'suppressed_new_table_unit_keys':{'new'}}),(args,{'suppressed_old_table_unit_keys':set()})]
   for index,(a,k) in enumerate(cases,2):c._summarize_text_delta(*a,**k);self.assertEqual(work.call_count,index)
 def test_scope_release_and_no_session(self):
  with patch.object(c,'_compute_summarize_text_delta',wraps=c._compute_summarize_text_delta) as work:
   c._summarize_text_delta('a','b',2);c._summarize_text_delta('a','b',2);self.assertEqual(work.call_count,2);self.assertIsNone(_caches.get())
   with comparison_scope():c._summarize_text_delta('a','b',2);c._summarize_text_delta('a','b',2)
   self.assertIsNone(_caches.get());self.assertEqual(work.call_count,3)
   with comparison_scope():c._summarize_text_delta('a','b',2)
   self.assertEqual(work.call_count,4)
 def test_bound_and_exception_not_cached(self):
  with comparison_scope():
   for i in range(2055):c._summarize_text_delta(str(i),'z',2)
   cache=_caches.get()[c._cached_summarize_text_delta.__wrapped__];self.assertEqual(cache.cache_info().maxsize,2048);self.assertEqual(cache.cache_info().currsize,2048)
  with patch.object(c,'_compute_summarize_text_delta',side_effect=RuntimeError('cancelled')) as work,comparison_scope():
   for i in range(2):
    with self.assertRaises(RuntimeError):c._summarize_text_delta('a','b',2)
   self.assertEqual(work.call_count,2)

 def test_two_whole_ordered_scans_retain_nonadjacent_keys(self):
  with patch.object(c,'_compute_summarize_text_delta',wraps=c._compute_summarize_text_delta) as work,comparison_scope():
   for phase in range(2):
    for i in range(140):c._summarize_text_delta('old '+str(i),'new '+str(i),12)
   self.assertEqual(work.call_count,140)
