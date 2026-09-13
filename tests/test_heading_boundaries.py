import unittest
from protocol_pdf_diff import sectioning
from protocol_pdf_diff.models import PageText,DocumentBlock,DocumentBlockKind

def adjacent_heading_page(text):
    blocks=[]
    for i,(line,y,font,size) in enumerate([
        ('1.2 Alpha',10.,'Arial-Bold',12.),
        ('1.2.1 Data Patterns',30.,'Arial-Bold',12.),
        ('The receiver shall preserve its operating conditions. '*8,80.,'Arial',10.),
    ]):
        x=20.;words=[]
        for word in line.split():
            width=len(word)*size*.4;words.append((word,x,y,x+width,y+size));x+=width+3
        blocks.append(DocumentBlock(1,(20.,y,x,y+size),DocumentBlockKind.TEXT,line,i,'fixture',
            word_boxes=tuple(words),word_styles=tuple((font,size) for _ in words)))
    return PageText(1,text,blocks=tuple(blocks))

class HeadingBoundaryTests(unittest.TestCase):
    def test_two_complete_physical_headings_are_not_joined(self):
        page=adjacent_heading_page('1.2 Alpha\n1.2.1 Data Patterns')
        self.assertEqual(page.text,sectioning._merge_source_split_heading_lines([page])[0].text)

    def test_split_heading_stops_at_next_independent_heading(self):
        page=adjacent_heading_page('1.2 Al\npha\n1.2.1 Data Patterns')
        self.assertEqual('1.2 Alpha\n1.2.1 Data Patterns',sectioning._merge_source_split_heading_lines([page])[0].text)

if __name__=='__main__':unittest.main()
