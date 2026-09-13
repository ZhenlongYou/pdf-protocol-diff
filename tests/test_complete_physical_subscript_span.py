"""Self-contained C36 source-layout regression; no local PDF or report dependency."""
import copy
import unittest
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order as repair

WORDS = [{'bottom': 542.5360512, 'text': 'a)', 'top': 530.5168512, 'x0': 72.12, 'x1': 82.82790528000001},
 {'bottom': 542.5360512, 'text': '|C', 'top': 530.5168512, 'x0': 86.15001216, 'x1': 97.97570304},
 {'bottom': 545.0255999999999, 'text': '-1', 'top': 535.4256, 'x0': 97.86, 'x1': 106.37711999999999},
 {'bottom': 542.5360512, 'text': '|+|C', 'top': 530.5168512, 'x0': 106.38, 'x1': 128.32345344},
 {'bottom': 545.0255999999999, 'text': '0', 'top': 535.4256, 'x0': 128.28, 'x1': 133.6176},
 {'bottom': 542.5360512, 'text': '|+|C', 'top': 530.5168512, 'x0': 133.62, 'x1': 155.56345344000002},
 {'bottom': 545.0255999999999, 'text': '1', 'top': 535.4256, 'x0': 155.52, 'x1': 160.85760000000002},
 {'bottom': 542.5360512, 'text': '|,', 'top': 530.5168512, 'x0': 160.86, 'x1': 167.31551232},
 {'bottom': 542.5360512, 'text': 'the', 'top': 530.5168512, 'x0': 170.63521536000002, 'x1': 187.3202688},
 {'bottom': 542.5360512,
  'text': 'peak',
  'top': 530.5168512,
  'x0': 190.63997184000002,
  'x1': 216.66514560000002},
 {'bottom': 542.5360512,
  'text': 'output',
  'top': 530.5168512,
  'x0': 219.98484864000002,
  'x1': 253.34413824000006},
 {'bottom': 542.5360512,
  'text': 'voltage',
  'top': 530.5168512,
  'x0': 256.6638412800001,
  'x1': 295.33321344},
 {'bottom': 542.5360512,
  'text': 'shall',
  'top': 530.5168512,
  'x0': 298.65051264,
  'x1': 323.31391104000005},
 {'bottom': 542.5360512,
  'text': 'not',
  'top': 530.5168512,
  'x0': 326.63121024000003,
  'x1': 343.31385983999996},
 {'bottom': 542.5360512,
  'text': 'exceed',
  'top': 530.5168512,
  'x0': 346.63115904,
  'x1': 385.32096384000005},
 {'bottom': 542.5360512,
  'text': '1200',
  'top': 530.5168512,
  'x0': 388.68033024,
  'x1': 415.33891583999997},
 {'bottom': 542.5360512,
  'text': 'mVppd.',
  'top': 530.5168512,
  'x0': 418.69708031999994,
  'x1': 459.99505151999995},
 {'bottom': 567.5560512, 'text': 'b)', 'top': 555.5368512, 'x0': 72.12, 'x1': 82.83391488000001},
 {'bottom': 567.5560512, 'text': 'C', 'top': 555.5368512, 'x0': 86.16203136, 'x1': 94.83989376},
 {'bottom': 570.0456, 'text': '-1', 'top': 560.4456, 'x0': 94.74, 'x1': 103.26575999999999},
 {'bottom': 567.5560512, 'text': '+', 'top': 555.5368512, 'x0': 105.96, 'x1': 112.9792128},
 {'bottom': 567.5560512, 'text': 'C', 'top': 555.5368512, 'x0': 116.28208896, 'x1': 124.95995135999999},
 {'bottom': 570.0456, 'text': '0', 'top': 560.4456, 'x0': 124.92, 'x1': 130.2576},
 {'bottom': 567.5560512, 'text': '+', 'top': 555.5368512, 'x0': 132.96, 'x1': 139.9792128},
 {'bottom': 567.5560512, 'text': 'C', 'top': 555.5368512, 'x0': 143.28208896, 'x1': 151.95995136000002},
 {'bottom': 570.0456, 'text': '1', 'top': 560.4456, 'x0': 151.92, 'x1': 157.2576},
 {'bottom': 567.5560512, 'text': ',', 'top': 555.5368512, 'x0': 157.26, 'x1': 160.6013376},
 {'bottom': 567.5560512, 'text': 'the', 'top': 555.5368512, 'x0': 163.92104064, 'x1': 180.60609408},
 {'bottom': 567.5560512,
  'text': 'steady-state',
  'top': 555.5368512,
  'x0': 183.92579712,
  'x1': 249.27538944000003},
 {'bottom': 567.5560512, 'text': 'output', 'top': 555.5368512, 'x0': 252.59509248, 'x1': 285.95438208},
 {'bottom': 567.5560512,
  'text': 'voltage',
  'top': 555.5368512,
  'x0': 289.27408512000005,
  'x1': 328.00355328},
 {'bottom': 567.5560512, 'text': 'shall', 'top': 555.5368512, 'x0': 331.32085248, 'x1': 355.98425088},
 {'bottom': 567.5560512, 'text': 'be', 'top': 555.5368512, 'x0': 359.30155007999997, 'x1': 372.65488128},
 {'bottom': 567.5560512,
  'text': 'greater',
  'top': 555.5368512,
  'x0': 375.97218047999996,
  'x1': 413.98290047999996},
 {'bottom': 567.5560512,
  'text': 'than',
  'top': 555.5368512,
  'x0': 417.30019967999993,
  'x1': 440.65350528},
 {'bottom': 567.5560512, 'text': 'or', 'top': 555.5368512, 'x0': 443.97080447999997, 'x1': 454.64385408},
 {'bottom': 567.5560512,
  'text': 'equal',
  'top': 555.5368512,
  'x0': 457.96115327999996,
  'x1': 487.28800127999995},
 {'bottom': 567.5560512,
  'text': 'to',
  'top': 555.5368512,
  'x0': 490.60409855999995,
  'x1': 500.64253439999993},
 {'bottom': 567.5560512,
  'text': '140',
  'top': 555.5368512,
  'x0': 503.99949695999993,
  'x1': 523.9994457599998}]
RAW = 'a) |C |+|C |+|C |, the peak output voltage shall not exceed 1200 mVppd.\n-1 0 1\nb) C\n-1\n+ C\n0\n+ C\n1\n, the steady-state output voltage shall be greater than or equal to 140'

class CompletePhysicalSubscriptSpanTests(unittest.TestCase):
    def test_seven_raw_lines_bind_one_complete_physical_row(self):
        result = repair(RAW, WORDS)
        self.assertIn('b) C-1 + C0 + C1 , the steady-state', result)
        self.assertIn('140', result)
        self.assertEqual(''.join(RAW.split('b)', 1)[1].split()), ''.join(result.split('b)', 1)[1].split()))

    def test_unstyled_repeated_base_blocks_binding(self):
        words = [dict(text='t', x0=10, x1=20, top=100, bottom=112),
                 dict(text='x', x0=20, x1=28, top=104.8, bottom=114.4),
                 dict(text='t', x0=10, x1=20, top=140, bottom=152)]
        self.assertEqual('t\nx\nt', repair('t\nx\nt', words))

    def test_missing_words_or_unmapped_neighbor_rejected(self):
        for raw, words in [(RAW, WORDS[:-1]),
                           (RAW.replace('b) C\n', 'b) C\nshall retain 900 mV\n'), WORDS),
                           (RAW.replace('140', '141'), WORDS)]:
            with self.subTest(raw=raw):
                self.assertEqual(raw, repair(raw, words))

    def test_real_value_change_and_neighbor_value_are_preserved(self):
        results = []
        for value, neighbor in [('140', '800'), ('141', '900')]:
            words = copy.deepcopy(WORDS)
            for word in words:
                if word['text'] == '140':
                    word['text'] = value
            for text, x0, x1 in [('Neighbor', 80, 125), (neighbor, 130, 150), ('mV.', 160, 180)]:
                words.append(dict(text=text, x0=x0, x1=x1, top=610, bottom=622))
            raw = RAW.replace('140', value) + '\nNeighbor ' + neighbor + ' mV.'
            result = repair(raw, words)
            self.assertIn('b) C-1 + C0 + C1', result)
            self.assertIn(value, result)
            self.assertTrue(result.endswith('Neighbor ' + neighbor + ' mV.'))
            self.assertEqual(''.join(raw.split('b)', 1)[1].split()), ''.join(result.split('b)', 1)[1].split()))
            results.append(result)
        self.assertNotEqual(*results)

    def test_duplicate_complete_physical_rows_rejected(self):
        words = copy.deepcopy(WORDS)
        for word in copy.deepcopy(WORDS):
            word['top'] += 200
            word['bottom'] += 200
            words.append(word)
        raw = RAW + '\n' + RAW
        self.assertEqual(raw, repair(raw, words))

if __name__ == '__main__':
    unittest.main()
