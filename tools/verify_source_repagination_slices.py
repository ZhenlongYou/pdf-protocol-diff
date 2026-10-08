"""用小型真实入口检查原文定位与重分页；可选真实 Np 单页版本对。"""
import argparse
import json
import os
import re
import sys
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import fitz
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.table_view_transaction import run_comparison


class FocusTargets(HTMLParser):
    def __init__(self):
        super().__init__(); self.targets=[]; self.raw_images=None; self.in_raw=False
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'data-focus-targets' in attrs:
            self.targets.append(json.loads(attrs['data-focus-targets']))
        if tag=='script' and attrs.get('id')=='context-source-images': self.in_raw=True
    def handle_endtag(self,tag):
        if tag=='script': self.in_raw=False
    def handle_data(self,data):
        if self.in_raw: self.raw_images=json.loads(data)


def run_case(old, new, output, options=None):
    options=options or DiffOptions(ocr_language='eng',ocr_time_budget_seconds=30)
    outcome=run_comparison(old,new, output, options)
    payload=json.loads(outcome.outputs['json'].read_text())
    parsed=FocusTargets();parsed.feed(outcome.outputs['html'].read_text())
    return payload,parsed,str(outcome.outputs['html'])


def write_text_pages(path,pages,fontfile=None):
    with fitz.open() as pdf:
        for text in pages:
            page=pdf.new_page(width=612,height=792)
            if fontfile: page.insert_font(fontname='sourcefont',fontfile=str(fontfile))
            assert page.insert_textbox((55,80,550,720),text,fontsize=12,
                                       fontname='sourcefont' if fontfile else 'helv') >= 0
        pdf.save(path)


def verify_reflow(output, text=None, old_value='+3.0', new_value='+3.5', name='reflow',fontfile=None):
    directory=output/name;directory.mkdir()
    text=text or ('the receiver retains every declared operating condition with limit +3.0 V. '
                 'the command ENABLE sets the output for the entire operating interval. '
                 'the controller records each measurement after the selected interval ends.')
    assert text.count(old_value)==1
    old_words=text.split(); new_words=text.replace(old_value,new_value).split()
    def partition(words, count):
        return [' '.join(words[len(words)*i//count:len(words)*(i+1)//count]) for i in range(count)]
    old,new=directory/'old.pdf',directory/'new.pdf'
    write_text_pages(old,partition(old_words,2),fontfile);write_text_pages(new,partition(new_words,3),fontfile)
    data,focus,html=run_case(old,new,directory/'reports')
    changes=data['content_changes']
    assert len(changes)==1 and changes[0]['change_type']=='modified',[(c['change_type'],c.get('old_pages'),c.get('new_pages')) for c in changes]
    change=changes[0]
    assert not change['added_snippets'] and not change['removed_snippets'],change
    assert len(change['replaced_snippets'])==1,change
    assert old_value in change['replaced_snippets'][0]['old'] and new_value in change['replaced_snippets'][0]['new'],change
    assert change['old_pages']=='1-2' and change['new_pages']=='1-3',change
    return dict(case=name,status='PASS',html=html,scope='2→3页，已知单次改动；保留双方完整页来源')


def verify_real_manual(output):
    """真实手册段落的人为重排和否定词删除，不冒充自然发布的版本对。"""
    import pdfplumber
    source=Path('/Users/mac/Documents/嵌入去嵌/outputs/cdr-research-rebuild-20260906/DPOJET-077004818.pdf')
    digest=sha256(source.read_bytes()).hexdigest()
    assert digest=='0f8954d26a5dcd749c6127333e8bd4c833fc46623384d40ad2fe8cd4f5965411'
    with pdfplumber.open(source) as pdf:
        page=' '.join(pdf.pages[87].extract_text(x_tolerance=1).split())
    start='In Explicit Clock Recovery,'
    end='measurement back to a single-source measurement.'
    assert page.count(start)==1 and page.count(end)==1
    text=page[page.index(start):page.index(end)+len(end)]
    record=verify_reflow(output,text,'not derived','derived','manual-reflow',
                         Path('/System/Library/Fonts/Supplemental/Arial.ttf'))
    record.update(source=str(source),source_sha256=digest,source_page=88,
                  scope='真实p88正文，人为2→3页及删除not；非自然版本对')
    return record


def verify_moved_image(output):
    directory=output/'image-region';directory.mkdir()
    boxes={'old':(65,220,415,290),'new':(100,260,450,330)}
    for side,value in (('old','3.3'),('new','5.5')):
        with fitz.open() as pdf, fitz.open() as image:
            page=pdf.new_page(width=612,height=792)
            page.insert_text((55,70),'1 Scope',fontsize=15)
            page.insert_text((55,105),'The controller shall preserve the original operating mode.',fontsize=12)
            p=image.new_page(width=350,height=70)
            p.insert_text((10,40),'Image threshold shall be '+value+' V.',fontsize=15)
            page.insert_image(fitz.Rect(boxes[side]),stream=p.get_pixmap(matrix=fitz.Matrix(3,3)).tobytes('png'))
            pdf.save(directory/(side+'.pdf'))
    data,focus,html=run_case(directory/'old.pdf',directory/'new.pdf',directory/'reports')
    assert len(data['content_changes'])==1 and data['content_changes'][0]['change_type']=='review'
    targets=[t for t in focus.targets if any(v and v.get('scope')=='source-image-region' for v in t.values())]
    assert len(targets)==1,targets
    for side in ('old','new'):
        assert targets[0][side]['box']==list(boxes[side]),targets
        assert targets[0][side]['page']==1
        assert side+':1' in focus.raw_images
    return dict(case='image-region',status='PASS',html=html,scope='旧新图片分别用自身bbox，OCR复核不升级为文字定位')


def verify_real_np(output):
    case=next(c for c in json.loads((ROOT/'docs/verification/pdf-adaptability-slices-20261007.json').read_text()) if c['name']=='oif-np')
    for side in ('old','new'):
        digest=sha256()
        with Path(case[side]).open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
        assert digest.hexdigest()==case[side+'_sha256']
    options=DiffOptions(old_start_page=13,old_end_page=13,new_start_page=15,new_end_page=15,ocr_time_budget_seconds=30)
    data,focus,html=run_case(case['old'],case['new'],output/'oif-np',options)
    assert len(data['content_changes'])==2
    for change in data['content_changes']:
        assert len(change['replaced_snippets'])==1 and not change['added_snippets'] and not change['removed_snippets']
        pair=change['replaced_snippets'][0]
        expected,count=re.subn(r'\bNp\s*=\s*53\b','Np=60',pair['old'])
        assert count==1 and ''.join(expected.split())==''.join(pair['new'].split()),pair
    exact=[t for t in focus.targets if t.get('old') and t.get('new') and not any(v.get('scope') for v in t.values() if v)]
    for low,high in ((190,230),(390,430)):
        hits=[t for t in exact if all(low <= t[side]['box'][1] < t[side]['box'][3] <= high for side in ('old','new'))]
        assert len(hits)==1,(low,exact)
        assert (hits[0]['old']['page'],hits[0]['new']['page'])==(13,15)
    return dict(case='oif-np',status='PASS',html=html,scope='实际旧13/新15页，两处53→60分别精确导航，无串位')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--real-np',action='store_true')
    parser.add_argument('--real-manual',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    os.environ['PROTOCOL_PDF_DIFF_OCR_CACHE']='off'
    results=[verify_reflow(args.output),verify_moved_image(args.output)]
    if args.real_np: results.append(verify_real_np(args.output))
    if args.real_manual: results.append(verify_real_manual(args.output))
    (args.output/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    for result in results: print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
