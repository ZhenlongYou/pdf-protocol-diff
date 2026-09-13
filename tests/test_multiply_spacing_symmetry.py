import unittest

from protocol_pdf_diff.compare import _review_unit_key as key
from protocol_pdf_diff.compare import _semantic_operator_signatures as signatures


class MultiplySpacingSymmetryTests(unittest.TestCase):
    def test_independent_horizontal_spaces_keep_explicit_multiply(self):
        base='A Figure Of Merit (FOMILD ) for the channel is the weighted insertion loss deviation from fILmin to (3/4)*fILmax .'
        for operator in ['*',' *','* ',' * ','\t*','*\t']:
            self.assertEqual(key(base),key(base.replace('*',operator)))
            self.assertEqual(signatures('(3/4)*fILmax'),signatures('(3/4)'+operator+'fILmax'))

    def test_word_sign_unit_operator_and_unknown_identity_changes_stay_distinct(self):
        pairs=[('f b','fb'),('1e-3','1e3'),('800mV','900mV'),('800mV','800MV'),('x-y','x y'),('a - b','a + b'),('(3/4)*f','(3/5)*f'),('(3/4)*f','(3/4)/f'),('a*\ue001','a*\ue002'),('a*b','b*a'),('value 1 2','value 12')]
        for a,b in pairs:
            with self.subTest(a=a,b=b):self.assertNotEqual(key(a),key(b))

    def test_no_new_cross_line_or_page_join(self):
        for value in ['a*\nb','a\n*b','a*\fb','a\f*b','a*\rb','a\r*b']:
            self.assertEqual(signatures(value),[])

    def test_leading_or_missing_operand_is_not_multiply(self):
        for value in ['* item','a *','( * b','a * )','*b']:
            self.assertEqual(signatures(value),[])

    def test_minus_and_slash_contract_is_unchanged(self):
        for value in ['a- b','a -b','a/ b','a /b']:
            self.assertEqual(signatures(value),[])
        self.assertTrue(signatures('a - b'));self.assertTrue(signatures('a / b'))

if __name__=='__main__':unittest.main()
