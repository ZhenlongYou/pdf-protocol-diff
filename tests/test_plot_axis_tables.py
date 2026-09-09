"""Plot ownership must reject font grids without suppressing real table data."""
import unittest
from protocol_pdf_diff.pdf_extract import _table_bbox_is_plot_axis_label


def word(text, x, y, width=16, height=10, **extra):
    return dict(text=text, x0=x, x1=x+width, top=y, bottom=y+height, **extra)


def geometry():
    words = [word('Figure 7. Transfer response', 100, 65, 250)]
    words += [word(v, x-8, 307) for v,x in zip(['0.1','1','10','40'],[100,200,300,400])]
    words += [word(v, 75, y-5) for v,y in zip(['0','-2','-4','-6'],[100,165,230,300])]
    words += [word('Arbitrary axis (unit)', 202, 328, 96)]
    return words


class PlotAxisTablesTests(unittest.TestCase):
    def check(self, expected, *, words=None, lines=None, title='', bbox=(200,325,300,345), grids=((100,100,400,300),)):
        self.assertEqual(expected, _table_bbox_is_plot_axis_label(
            bbox, lines if lines is not None else ['表格行: T2 | Column 1=Arbitrary axis (unit)'],
            geometry() if words is None else words, title=title, grid_bboxes=grids))

    def test_logarithmic_and_rotated_tick_labels(self):
        self.check(True)
        self.check(True, words=[dict(w, upright=False) for w in geometry()])

    def test_missing_geometry_and_numeric_data_remain(self):
        self.check(False, words=[])
        self.check(False, bbox=None)
        self.check(False, grids=())
        self.check(False, lines=['表格行: T2 | Column 1=100'])
        self.check(False, words=[w for w in geometry() if w['x0']!=75])

    def test_invalid_candidate_and_plot_boxes_never_suppress_tables(self):
        for box in [(200,325,300,320), (200,325,300,float('nan')),
                    (200,325,300,float('-inf')), (200,325,200,345)]:
            self.check(False,bbox=box)
        for box in [(100,300,400,100), (100,100,float('inf'),300)]:
            self.check(False,grids=(box,))

    def test_explicit_table_and_technical_schema_remain(self):
        self.check(False, title='Table 3. Frequency limits')
        self.check(False, lines=['表格行: T2 | Parameter=Frequency | Nominal=40 GHz'])
        self.check(False, lines=['表格行: T2 | Column 1=Frequency','表格行: T2 | Column 1=40 GHz'])
        self.check(False, words=geometry()+[word('Table 3. Limits',200,318,90,5)])

    def test_adjacent_column_prose_and_nonmonotonic_ticks_remain(self):
        self.check(False, bbox=(405,325,495,345))
        self.check(False, words=geometry()+[word('The receiver shall meet the limit.',100,319,280,5)])
        words=geometry();words[2]=dict(words[2], text='99');self.check(False,words=words)
        self.check(False,words=[w for w in geometry() if not w['text'].startswith('Figure')])

    def test_legitimate_small_or_continuation_table_without_plot_remains(self):
        self.check(False, grids=((200,325,300,345),))
        self.check(False, words=[word('Frequency (GHz)',202,328,90)])

    def test_existing_axis_label_or_short_prose_protects_following_box(self):
        for text in ['Time (s)', 'The options are listed below.']:
            self.check(False, words=geometry()+[word(text,150,318,140,5)])

    def test_same_baseline_neighbor_cannot_extend_figure_caption(self):
        words=geometry()[1:]+[word('Figure 2. Left plot',-180,65,200),word('Limits',210,65,50)]
        self.check(False, words=words)

if __name__=='__main__':unittest.main()
