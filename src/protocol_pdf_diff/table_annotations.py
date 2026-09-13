"""Conserve complete trailing spanning notes while classifying their row role."""
import math
import re
from collections import Counter
from .models import TableRowChange
from .table_codec import split_table_cells, split_table_field


def _box(b):
    return isinstance(b,(tuple,list)) and len(b)==4 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in b) and b[0]<b[2] and b[1]<b[3]


def _covered_annotation_line(row_text, table, receipt):
    """Require the entire ordered projection, never a substring of its payload."""
    cells=split_table_cells(row_text)
    if not cells or cells[0] != f"表格行: T{table.table_number}":return False
    fields=[split_table_field(cell) for cell in cells[1:]]
    if len(fields)!=len(receipt['raw_cells']) or any(f is None for f in fields):return False
    # The existing data projection supplies the actual preserved header labels.
    # Every column including empty values must stay present and in order.
    exemplar=split_table_cells(table.row_texts[0]) if table.row_texts else []
    headers=[split_table_field(cell) for cell in exemplar[1:]]
    if len(headers)!=len(fields) or any(f is None for f in headers):return False
    if [f[0] for f in fields] != [f[0] for f in headers]:return False
    values=[f[1] for f in fields]
    expected=[value or '' for value in receipt['raw_cells']]
    return [' '.join(v.split()) for v in values]==[' '.join(v.split()) for v in expected]


def annotation_receipts(table):
    rows,bounds=table.raw_source_cells,table.raw_cell_bounds
    if (not rows or len(rows)!=len(bounds) or not table.context_words or not table.image_data_uri
            or not table.content_fully_represented or not table.data_rows_fully_represented or not _box(table.bbox)):
        return []
    width=len(rows[0])
    if width<2 or len(bounds[0])!=width or any(not _box(b) for b in bounds[0]):return []
    grid=bounds[0]
    if any(abs(grid[i][2]-grid[i+1][0])>1e-6 for i in range(width-1)):return []
    if any(abs(b[1]-grid[0][1])>1e-6 or abs(b[3]-grid[0][3])>1e-6 for b in grid):return []
    if any(len(w)!=5 or not isinstance(w[0],str) or not _box(w[1:]) for w in table.context_words):return []
    result=[]
    for i in range(len(rows)-1,0,-1):
        row,boxes=rows[i],bounds[i]
        if len(row)!=width or len(boxes)!=width or not isinstance(row[0],str):break
        match=re.match(r'^(Note\s+[1-9][0-9]*)\s*[:：]',row[0])
        if not match or any(c is not None for c in row[1:]) or any(b is not None for b in boxes[1:]) or not _box(boxes[0]):break
        b=boxes[0]
        prior=[v for v in bounds[i-1] if v is not None]
        if not prior or any(not _box(v) for v in prior) or any(abs(v[3]-b[1])>1e-6 for v in prior):break
        if b[0]<table.bbox[0] or b[1]<table.bbox[1] or b[2]>table.bbox[2] or b[3]>table.bbox[3]:break
        if abs(b[0]-grid[0][0])>1e-6 or abs(b[2]-grid[-1][2])>1e-6:break
        selected=[w for w in table.context_words if b[0]<=((w[1]+w[3])/2)<=b[2] and b[1]<=((w[2]+w[4])/2)<=b[3]]
        if not selected or any(w[1]<b[0] or w[3]>b[2] or w[2]<b[1] or w[4]>b[3] for w in selected):break
        selected.sort(key=lambda w:(w[2],w[1]))
        if ''.join(row[0].split())!=''.join(''.join(w[0].split()) for w in selected):break
        result.append({'id':match.group(1),'text':row[0],'page':table.page_number,'table_number':table.table_number,'row_index':i,'bbox':b,'raw_cells':row,'words':selected})
    # Need actual multi-column data above the trailing annotation block.
    first=min((r['row_index'] for r in result),default=0)
    if first<2 or not any(sum(bool(c) for c in row)>1 for row in rows[1:first]):return []
    counts=Counter(r['id'] for r in result)
    return [r for r in reversed(result) if counts[r['id']]==1]


def classify_annotations(changes,old_tables,new_tables,make_row):
    def collect(tables,side):
        found=[]
        for table in tables:
            for receipt in annotation_receipts(table):
                matching=[row for row in table.row_texts if _covered_annotation_line(row,table,receipt)]
                if len(matching)!=1:continue
                projected=make_row('',matching[0],'新表新增行')
                found.append((projected.new_value,dict(receipt,side=side)))
        counts=Counter(v for v,r in found);ids=Counter(r['id'] for v,r in found)
        return {v:r for v,r in found if counts[v]==1 and ids[r['id']]==1}
    old,new=collect(old_tables,'old'),collect(new_tables,'new')
    old_ids=[r['id'] for r in old.values()];new_ids=[r['id'] for r in new.values()]
    shared=set(old_ids)&set(new_ids)
    if [k for k in old_ids if k in shared] != [k for k in new_ids if k in shared]:
        return changes  # A changed note order is not authorized as equality.
    classified=[];remaining=[]
    for row in changes:
        a=old.get(row.old_value) if row.old_value else None
        b=new.get(row.new_value) if row.new_value else None
        if (row.old_value and a is None) or (row.new_value and b is None) or not(a or b):remaining.append(row);continue
        if a and b and a['id']!=b['id']:remaining.append(row);continue
        classified.append((row,a,b))
    # Pair only unique same-note identities within the already paired group.
    for key in dict.fromkeys((a or b)['id'] for _,a,b in classified):
        entries=[(r,a,b) for r,a,b in classified if (a or b)['id']==key]
        aa=[a for r,a,b in entries if a];bb=[b for r,a,b in entries if b]
        if len(aa)>1 or len(bb)>1:
            remaining.extend(r for r,a,b in entries);continue
        a=aa[0] if aa else None;b=bb[0] if bb else None
        old_text=a['text'] if a else '';new_text=b['text'] if b else ''
        if a and b and old_text==new_text:continue
        kind='表说明修改' if a and b else ('表说明新增' if b else '表说明删除')
        remaining.append(TableRowChange(key,old_text,new_text,kind,'annotation',tuple(x for x in [a,b] if x)))
    return remaining
