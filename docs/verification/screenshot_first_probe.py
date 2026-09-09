"""Exercise public PDF/report APIs with independent drawings and display oracle."""
import argparse,base64,io,json,re,sys,tempfile
from PIL import Image
from pathlib import Path
from dataclasses import replace
import fitz
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'src'))
from protocol_pdf_diff.models import DiffResult,DiffOptions,Section,SectionChange,SnippetPair
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.prose_source_visuals import build_prose_source_visuals
from screenshot_first_oracle import check

def pdf(path,value,table=False):
    doc=fitz.open();page=doc.new_page(width=300,height=400)
    page.insert_text((20,30),'1 Receiver',fontsize=12)
    page.insert_text((20,55),'The receiver limit is '+value+' mV.',fontsize=10)
    page.insert_text((20,80),'Complete surrounding context remains visible.',fontsize=9)
    if table:
        page.insert_text((20,105),'Table 1. Limits',fontsize=10)
        for y in (120,140,160):page.draw_line((20,y),(240,y),width=.5)
        for x in (20,150,240):page.draw_line((x,120),(x,160),width=.5)
        for x,y,text in [(25,134,'Parameter'),(155,134,'Value'),(25,154,'Voltage'),(155,154,value)]:page.insert_text((x,y),text,fontsize=10)
    doc.save(path);doc.close()

def run(case,root):
    root.mkdir(parents=True,exist_ok=True);old,new=root/'old.pdf',root/'new.pdf'
    pdf(old,'10',case['kind']=='table');pdf(new,'12',case['kind']=='table')
    if case['kind']=='partition':
        def sec(sid,text):return Section(sid,'1 Receiver','Receiver',1,('1 Receiver',),('1',),1,1,text)
        a,b=sec('a','Limit is 10 mV.'),sec('b','Limit is 12 mV.')
        if case.get('glyph'):
            a=replace(a,body=a.body+' Glyph \ue123 in mode A.');b=replace(b,body=b.body+' Glyph \ue123 in mode B.')
        result=DiffResult(old,new,[a],[b],[SectionChange('modified',a,b,case['score'],replaced_snippets=[SnippetPair(a.body,b.body)])],[])
    elif case['kind']=='similar':
        for path,value in [(old,'100'),(new,'120')]:
            path.unlink();doc=fitz.open();page=doc.new_page(width=600,height=300)
            page.insert_text((20,30),'1 Receiver',fontsize=12)
            page.insert_text((20,60),'The receiver limit is '+value+' mV in mode A.'+(' Additional operating conditions apply.' if case.get('isolated') else ''),fontsize=11)
            page.insert_text((20,90),'100 mV' if case.get('isolated') else 'The receiver limit is 100 mV in mode B.',fontsize=11)
            doc.save(path);doc.close()
        result=run_diff(old,new,DiffOptions(visual_watchdog=False))
    else:result=run_diff(old,new,DiffOptions(visual_watchdog=False))
    if case['kind']=='invalid':
        ea,eb=extract_pdf_text(old),extract_pdf_text(new)
        ea=replace(ea,source_sha256='f'*64)
        groups,warnings=build_prose_source_visuals(result,ea,eb)
        return dict(images=len(groups),warnings=warnings)
    files=write_reports(result,root/'report',DiffOptions(visual_watchdog=False));html=files['html'].read_text();data=json.loads(files['json'].read_text())
    if case['kind']=='partition':
        return dict(main=len(data['content_changes']),appendix=len(data.get('similarity_review_changes',[])),raw=len(data['changes']),reviewable_text=re.sub('<[^>]+>','',html),reader_safe=all(not re.search('[\ue000-\uf8ff]',files[k].read_text()) for k in ('html','markdown','text')))
    if case['kind']=='similar':
        visual=next(v for g in result.prose_source_visuals for v in g.old_visuals)
        image=Image.open(io.BytesIO(base64.b64decode(visual.image_data_uri.split(',')[1])))
        reds=[]
        with fitz.open(old) as doc:
            for word in [w for w in doc[0].get_text('words') if w[4]=='100']:
                box=tuple(round(v*image.size[j%2]/(600 if j%2==0 else 300)) for j,v in enumerate(word[:4]))
                pixels=image.crop(box).convert('RGB')
                reds.append(max(pixels.getpixel((x,y))[0]-pixels.getpixel((x,y))[1] for x in range(pixels.width) for y in range(pixels.height)))
        return dict(old_red=reds,old_regions=[v.highlight_region_count for g in result.prose_source_visuals for v in g.old_visuals],new_regions=[v.highlight_region_count for g in result.prose_source_visuals for v in g.new_visuals])
    if case['kind']=='short':
        visuals=[v for g in result.prose_source_visuals for v in (*g.old_visuals,*g.new_visuals)]
        return dict(images=len(visuals),full_pages=bool(visuals) and all(v.crop_bbox==(0.,0.,300.,400.) for v in visuals),highlighted=bool(visuals) and all(v.highlight_region_count>0 for v in visuals),image_before_text=('class="prose-source-visual"' in html and html.index('class="prose-source-visual"')<html.index('class="prose-text-details"')),folded='<details class="prose-text-details">' in html)
    tables=result.new_table_visuals
    t=tables[0] if tables else None
    return dict(tables=len(tables),context_bbox=list(t.context_bbox) if t and t.context_bbox else None,detector_bbox=list(t.bbox) if t else None,context_image=bool(t and t.context_image_data_uri),context_in_html=bool(t and t.context_image_data_uri and '· 完整原页' in html))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('fixtures',nargs='+');ap.add_argument('--artifact');args=ap.parse_args();rows=[]
    print('SCREENSHOT_FIRST_TEST',flush=True)
    try:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(args.artifact).parent/'public-reports' if args.artifact else Path(temp)
            for f in args.fixtures:
                for c in json.loads(Path(f).read_text())['cases']:
                    observed=run(c,root/c['id']);check(c,observed);rows.append(dict(id=c['id'],kind=c['kind'],passed=True))
            if args.artifact:Path(args.artifact).write_text(json.dumps(rows,indent=2))
    except AssertionError as error:print('SCREENSHOT_CONTRACT_FAIL',str(error)[:1000]);return 1
    print('SCREENSHOT_CONTRACT_OK',len(rows));return 0
if __name__=='__main__':sys.exit(main())
