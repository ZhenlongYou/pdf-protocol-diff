"""经公开事务入口验证小数点、数值摘要、同页数重分页和跨页单元格边界。"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tests.test_table_repagination import write_split_table_pair
from tests.test_visual_coverage_disclosure import small_decimal_pdf
from tools.verify_source_repagination_slices import run_case, write_text_pages, verify_real_np, verify_real_manual


def record(name, html, scope):
    return dict(case=name, status='PASS', html=html, scope=scope)


def verify_decimal(output, same=False):
    name='tiny-identical' if same else 'tiny-decimal'
    directory=output/name; directory.mkdir()
    old=small_decimal_pdf(directory/'old.pdf')
    new=small_decimal_pdf(directory/'new.pdf', decimal=same)
    data,_,html=run_case(old,new,directory/'reports')
    issues=data['provenance']['visual_watchdog_run']['coverage_issues']
    assert not data['content_changes']
    if same:
        assert not issues and data['assessment']['allows_no_difference_conclusion']
    else:
        assert len(issues)==1 and issues[0]['category']=='residual',issues
        assert not data['assessment']['allows_no_difference_conclusion']
        assert '未检查或需要复核的内容' in Path(html).read_text()
    return record(name,html,'同文小栅格独立生成对照' if same else '小数点变化不能授权无差异结论')


def verify_unit(output):
    directory=output/'attached-unit'; directory.mkdir()
    old,new=directory/'old.pdf',directory/'new.pdf'
    text='1 Requirements\nThe receiver shall retain the output voltage at 3.3V during the entire operating interval.'
    write_text_pages(old,[text]); write_text_pages(new,[text.replace('3.3V','3.5V')])
    data,_,html=run_case(old,new,directory/'reports')
    assert len(data['content_changes'])==1
    source=Path(html).read_text()
    assert '<del>3.3</del>' in source and '<ins>3.5</ins>' in source
    assert '<del>3V</del>' not in source
    return record('attached-unit',html,'紧邻单位的小数摘要保留完整数值')


def verify_same_count(output):
    directory=output/'same-count'; directory.mkdir()
    a='the receiver retains every declared operating condition with limit +3.0 V.'
    b='the command ENABLE sets the output for the entire operating interval.'
    c='the controller records each measurement after the selected interval ends with reference limit +5.0 V.'
    old,new=directory/'old.pdf',directory/'new.pdf'
    write_text_pages(old,[a+' '+b,c]); write_text_pages(new,[a.replace('+3.0','+3.5'),b+' '+c])
    data,_,html=run_case(old,new,directory/'reports')
    assert len(data['content_changes'])==1
    change=data['content_changes'][0]
    assert change['change_type']=='modified' and len(change['replaced_snippets'])==1
    assert not change['added_snippets'] and not change['removed_snippets']
    assert change['old_pages']=='1-2' and change['new_pages']=='1-2'
    assert '+3.0' in change['replaced_snippets'][0]['old']
    assert '+3.5' in change['replaced_snippets'][0]['new']
    return record('same-count',html,'2→2页重排，同时存在另一处未变小数，仍只报告一处改字')


def verify_table(output, edited=False):
    name='table-edited' if edited else 'table-split'
    directory=output/name; directory.mkdir()
    tail='and resume operation after 10 ms when the interval ends.'
    new_tail=tail.replace('10','20') if edited else tail
    old,new=write_split_table_pair(directory,old_tail=tail,new_tail=new_tail)
    data,_,html=run_case(old,new,directory/'reports')
    changes=data['content_table_changes']
    assert len(changes)==1 and changes[0]['change_type']=='review'
    assert len(changes[0]['row_changes'])==1
    row=changes[0]['row_changes'][0]
    assert tail in row['old_value'] and new_tail in row['new_value']
    assert row['change_type']=='需人工复核'
    assert {(r['side'],r['page']) for r in row['source_receipts']}=={('old',1),('new',1),('new',2)}
    return record(name,html,'1→2页两列叙述格拆分，保留原文和所有物理行，仅合并为一条复核')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--real-np',action='store_true')
    parser.add_argument('--real-manual',action='store_true')
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    os.environ['PROTOCOL_PDF_DIFF_OCR_CACHE']='off'
    results=[]
    checks=[lambda:verify_decimal(args.output),lambda:verify_decimal(args.output,True),
            lambda:verify_unit(args.output),lambda:verify_same_count(args.output),
            lambda:verify_table(args.output),lambda:verify_table(args.output,True)]
    if args.real_np: checks.append(lambda:verify_real_np(args.output))
    if args.real_manual: checks.append(lambda:verify_real_manual(args.output))
    for check in checks:
        result=check(); results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    (args.output/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
