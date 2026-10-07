"""字面分词只改变分组，不解释数值，不删除源字符。"""
import unittest
from protocol_pdf_diff.literal_atoms import literal_atoms, literal_atom_spans


class LiteralAtomsTests(unittest.TestCase):
    def test_numbers_identifiers_and_unicode_are_lossless(self):
        for text in ('阈值−3.30伏→+3.3伏', 'MODE3.3 3.3V 1e-6s TP4a',
                     '3.0 3.00 1,000.0 V σ₁²≤.05GHz', 'a-3.3 A/B µV \ue011'):
            atoms=literal_atom_spans(text)
            self.assertEqual(''.join(text.split()),''.join(m.group() for m in atoms))
            self.assertTrue(all(text[m.start():m.end()]==m.group() for m in atoms))
        self.assertEqual(['MODE3.3','3.3','V','1e-6','s','TP4a'],literal_atoms('MODE3.3 3.3V 1e-6s TP4a'))
        self.assertNotEqual(literal_atoms('3.0'),literal_atoms('3.00'))
        self.assertNotEqual(literal_atoms('1e-6'),literal_atoms('0.000001'))
