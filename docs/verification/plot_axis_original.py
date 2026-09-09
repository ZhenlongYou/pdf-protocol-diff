"""Rerun the exact user source positions with adjacent pages and record identity.

The source PDFs are local validation inputs, not repository-distributed fixtures.
Table/figure identities, rather than page offsets alone, establish the pairing.
"""
import hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'src'))
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.reporting import write_reports

def main():
    root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True);rows=[]
    old=Path('/Users/mac/Desktop/OIF-CEI-5.1.pdf');new=Path('/Users/mac/Desktop/OIF-CEI-05.3.pdf')
    for a,b,caption in [(292,296,'13-14'),(349,353,'16-16')]:
        t=time.perf_counter();opts=DiffOptions(old_start_page=a-1,old_end_page=a+1,new_start_page=b-1,new_end_page=b+1)
        r=run_diff(old,new,opts);paths=write_reports(r,root/f'original-{a}-{b}',opts)
        data=json.loads(paths['json'].read_text())
        oldtables=[v for v in r.old_table_visuals if v.page_number==a]
        newtables=[v for v in r.new_table_visuals if v.page_number==b]
        assert len(oldtables)==(1 if a==292 else 0),[(v.title,v.row_texts) for v in oldtables]
        assert len(newtables)==(1 if a==292 else 0)
        if a==292:assert not [v for v in r.old_table_visuals if v.page_number==291]
        for tables in (oldtables,newtables):
            if a==292:
                assert 'Table 13-8.' in tables[0].title and len(tables[0].row_texts)==10
                assert '8.31' in str(tables[0].row_texts)
        for sections in (r.old_sections,r.new_sections):
            assert caption in '\n'.join(s.body for s in sections),caption
        visuals=[v for g in r.prose_source_visuals for v in (*g.old_visuals,*g.new_visuals,*g.old_figure_visuals,*g.new_figure_visuals)]
        image_pages=sorted({v.page_number for v in visuals})
        assert a in image_pages and b in image_pages,image_pages
        assert not data['table_changes'],data['table_changes']
        row=dict(pages=[a,b],seconds=time.perf_counter()-t,old_tables=[v.title for v in oldtables],new_tables=[v.title for v in newtables],html=str(paths['html']),html_sha256=hashlib.sha256(paths['html'].read_bytes()).hexdigest(),source_visual_pages=image_pages,table_changes=len(data['table_changes']));rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    (root/'original-public.json').write_text(json.dumps(dict(sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [old,new]},runs=rows),ensure_ascii=False,indent=2))
    print('PLOT_ORIGINAL_PUBLIC_OK')
if __name__=='__main__':main()
