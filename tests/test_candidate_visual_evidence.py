import unittest
from dataclasses import dataclass
from types import SimpleNamespace as N
from unittest.mock import patch

from protocol_pdf_diff import table_view_transaction as t


@dataclass
class Page:
    text: str='native'
    source_char_map: tuple=()
    bbox: tuple=(0,0,600,800)

class CandidateVisualTests(unittest.TestCase):
    def setUp(self):
        self.audit=N(enabled=True)
        self.original=N(provenance=N(visual_watchdog_audit=self.audit),visual_review_items=['old'],visual_review_warnings=('watchdog only',),warnings=['prose'])
        self.candidate=N()
        self.ex=N(source_sha256='hash',pdf_path='file',pages=[Page()])
        self.sources=(self.ex,self.ex)
        self.inputs=([1],{2},{3:4},{5:6})
    def call(self,recorded=None,enabled=True,projected=None):
        return t._candidate_visual_evidence(self.original,self.candidate,self.sources,projected or self.sources,N(visual_watchdog=enabled),self.inputs if recorded is None else recorded)
    def test_exact_reuses_only_explicit_warnings(self):
        with patch.object(t,'_visual_inputs',return_value=self.inputs),patch('protocol_pdf_diff.visual_watchdog.detect_visual_review_items') as run:
            self.assertEqual(self.call(),(['old'],('watchdog only',),self.audit));run.assert_not_called()
    def test_each_changed_visual_input_rescans(self):
        for i in range(4):
            changed=list(self.inputs);changed[i]='different'
            with self.subTest(i=i),patch.object(t,'_visual_inputs',return_value=tuple(changed)),patch('protocol_pdf_diff.visual_watchdog.detect_visual_review_items',return_value=(['new'],['fresh'],self.audit)) as run:
                self.assertEqual(self.call()[0],['new']);run.assert_called_once_with(*self.sources,semantic_result=self.candidate)
    def test_missing_audit_rescans(self):
        self.original.provenance=None
        with patch('protocol_pdf_diff.visual_watchdog.detect_visual_review_items',return_value=([],[],self.audit)) as run:
            self.call();run.assert_called_once()
    def test_source_geometry_and_unknown_provider_rescan(self):
        for page in (Page(bbox=(0,0,601,800)),N(text='native')):
            changed=N(source_sha256='hash',pdf_path='file',pages=[page])
            with self.subTest(page=page),patch('protocol_pdf_diff.visual_watchdog.detect_visual_review_items',return_value=([],[],self.audit)) as run:
                self.call(projected=(changed,self.ex));run.assert_called_once()
    def test_disabled_keeps_diagnostic(self):
        with patch('protocol_pdf_diff.visual_watchdog.detect_visual_review_items') as run:
            items,warnings,audit=self.call(enabled=False)
            self.assertEqual(items,[]);self.assertIn('已关闭',warnings[0]);self.assertFalse(audit.enabled);run.assert_not_called()
    def test_capture_is_snapshot(self):
        state={};token=t._ACTIVE.set(state)
        try:
            with patch.object(t,'_visual_inputs',return_value=self.inputs):t.capture_visual_inputs(None,None,None)
            self.inputs[0].append(99)
            self.assertEqual(state['visual_inputs'][0],[1])
        finally:t._ACTIVE.reset(token)
if __name__=='__main__':unittest.main()
