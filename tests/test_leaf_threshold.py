import unittest
from protocol_pdf_diff.models import Section,DiffOptions
from protocol_pdf_diff import compare

def leaf_pair():
    body='The electrical signaling is based on high speed low voltage logic with a nominal differential impedance of 100 Ω.'
    old=Section('old','27.3 Electrical Characteristics','Electrical Characteristics',2,
        ('27 CEI-112G-LR-PAM4 Long Reach Interface','27.3 Electrical Characteristics'),('27','27.3'),624,624,body)
    new=Section('new','27.3 Electrical Characteristics','Electrical Characteristics',2,
        ('27.3 Electrical Characteristics',),('27.3',),628,628,body)
    return old,new

class LeafThresholdTests(unittest.TestCase):
    def test_strict_threshold_cannot_reenter_through_fallback(self):
        old,new=leaf_pair()
        matches=compare._match_sections([old],[new],DiffOptions(min_section_match_similarity=1.0))
        self.assertFalse(any(a is not None and b is not None for a,b,*_ in matches),matches)
        self.assertEqual({(0,None),(None,0)},{(a,b) for a,b,*_ in matches})

    def test_default_threshold_retains_exact_body_leaf_anchor(self):
        old,new=leaf_pair()
        matches=compare._match_sections([old],[new],DiffOptions())
        self.assertEqual(1,len(matches))
        a,b,score,basis=matches[0]
        self.assertEqual((0,0,'structural_leaf_body_anchor'),(a,b,basis))
        self.assertAlmostEqual(0.8708708708708709,score)
        self.assertLess(score,1.0)

if __name__=='__main__':unittest.main()
