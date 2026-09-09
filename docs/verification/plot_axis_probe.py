"""Public extraction/report regression for plot labels and real small tables.

Drawings are test inputs; expected table inventory is independently specified in
JSON. Original user pages are rerun separately, with hashes recorded in the
acceptance note rather than bundled copyrighted source PDFs.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path
import fitz
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'src'))
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.reporting import write_reports
from plot_axis_oracle import check


def draw(path, case, value):
    doc=fitz.open();p=doc.new_page(width=600,height=800)
    p.insert_text((50,30),'1 Receiver requirements',fontsize=12)
    p.insert_text((50,55),'The receiver limit is '+value+' mV.',fontsize=10)
    if case.get('plot',True):
        p.insert_text((100,85),'Figure 7. Transfer response',fontsize=10)
        for y in (110,170,230,290,350):p.draw_line((100,y),(450,y),width=.5)
        for x in (100,170,240,310,380,450):p.draw_line((x,110),(x,350),width=.5)
        p.draw_polyline([(100,120),(160,125),(230,160),(300,220),(370,270),(450,320)],width=1)
        for y,v in zip((110,170,230,290,350),('0','-2','-4','-6','-8')):p.insert_text((78,y+3),v,fontsize=9)
        for x,v in zip((100,215,330,450),case.get('ticks',['0.1','1','10','40'])):
            p.insert_text((x-5,365),v,fontsize=9)
        if case.get('separator'):
            p.insert_text((195,378),case['separator'],fontsize=8)
    # The empty first cell and lettering crossing its boundary reproduce the
    # detector shape seen on the reported axes, without copying source pages.
    for y in (382,387,402):p.draw_line((220,y),(325,y),width=.35)
    for x in (220,325):p.draw_line((x,382),(x,402),width=.35)
    p.insert_text((224,398),case.get('label','Horizontal (unit)'),fontsize=9)
    if case.get('table_title'):
        p.insert_text((210,379),'Table 2. Operating state',fontsize=8)
    # A real table containing Frequency must survive in every case.
    p.insert_text((100,458),'Table 3. Limits',fontsize=10)
    for y in (470,490,510):p.draw_line((100,y),(450,y),width=.5)
    for x in (100,280,450):p.draw_line((x,470),(x,510),width=.5)
    for x,y,t in [(105,484,'Parameter'),(285,484,'Nominal'),(105,504,'Frequency (GHz)'),(285,504,'40')]:p.insert_text((x,y),t,fontsize=10)
    doc.save(path);doc.close()


def run(case, root):
    root.mkdir(parents=True,exist_ok=True)
    old,new=root/'old.pdf',root/'new.pdf';draw(old,case,'10');draw(new,case,'12')
    options=DiffOptions(visual_watchdog=False)
    result=run_diff(old,new,options)
    files=write_reports(result,root/'report',options)
    data=json.loads(files['json'].read_text());html=files['html'].read_text()
    tables=result.old_table_visuals
    observed=dict(table_count=len(tables),titles=[t.title for t in tables],
        real_rows=[s for t in tables if 'Table 3.' in t.title for s in t.row_texts],
        table_payloads=[s for t in tables if 'Table 3.' not in t.title for s in t.row_texts],
        body='\n'.join(s.body for s in result.old_sections),
        has_images=bool('data:image/' in html),full_table_pages=all(t.context_bbox==(0.,0.,600.,800.) for t in tables),
        changes=len(data['content_changes']),report=str(files['html']))
    check(case,observed)
    return observed


def main():
    ap=argparse.ArgumentParser();ap.add_argument('fixtures',nargs='+');ap.add_argument('--artifact');args=ap.parse_args();rows=[]
    print('PLOT_AXIS_TEST',flush=True)
    try:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(args.artifact).parent/'public-reports' if args.artifact else Path(temp)
            for file in args.fixtures:
                for case in json.loads(Path(file).read_text())['cases']:
                    rows.append(dict(id=case['id'],observed=run(case,root/case['id'])))
            if args.artifact:Path(args.artifact).write_text(json.dumps(rows,indent=2))
    except AssertionError as e:print('PLOT_AXIS_CONTRACT_FAIL',str(e)[:1500]);return 1
    print('PLOT_AXIS_CONTRACT_OK',len(rows));return 0
if __name__=='__main__':sys.exit(main())
