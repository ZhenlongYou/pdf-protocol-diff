"""把有来源证据的 1↔2 页单元格拆分集中为复核，不宣称逻辑单元格已判等。

仅处理已配对的两列叙述表。原始格、词坐标、物理页边和全文守恒共同约束候选；
失败时原行差异原样返回。重复首行作为上下文保留，不凭词表猜测并删除未知表头。
"""
from collections import Counter
from difflib import SequenceMatcher
import math
import re

from .literal_atoms import literal_atoms
from .models import TableRowChange
from .table_codec import split_table_cells, split_table_field


def _space(text):
    return ' '.join(text.split())


def _box(value):
    return (isinstance(value,(tuple,list)) and len(value)==4
            and all(isinstance(v,(int,float)) and math.isfinite(v) for v in value)
            and value[0]<value[2] and value[1]<value[3])


def _verified_rows(table):
    """完整矩形两列，逐格源词与抽取值一致，展示行不丢字、不重排。"""
    rows,bounds=table.raw_source_cells,table.raw_cell_bounds
    if (len(rows)<2 or len(rows)!=len(bounds) or not _box(table.bbox)
            or not table.content_fully_represented or not table.row_alignment_reliable
            or not table.data_rows_fully_represented or not table.context_words):
        return None
    if any(len(w)!=5 or not isinstance(w[0],str) or not _box(w[1:]) for w in table.context_words):
        return None
    grid=None;last_bottom=None
    for cells,boxes in zip(rows,bounds):
        if (len(cells)!=2 or len(boxes)!=2 or any(not isinstance(c,str) or not c.strip() for c in cells)
                or any(not _box(b) for b in boxes)):
            return None
        a,b=boxes
        if (a[2]!=b[0] or a[1]!=b[1] or a[3]!=b[3]
                or (last_bottom is not None and a[1]!=last_bottom)):
            return None
        columns=(a[0],a[2],b[2])
        if grid is not None and columns!=grid:
            return None
        grid=columns;last_bottom=a[3]
        for cell,box in zip(cells,boxes):
            if not (table.bbox[0]<=box[0]<box[2]<=table.bbox[2] and table.bbox[1]<=box[1]<box[3]<=table.bbox[3]):
                return None
            words=[w for w in table.context_words if box[0]<=(w[1]+w[3])/2<box[2] and box[1]<=(w[2]+w[4])/2<box[3]]
            if not words or any(w[1]<box[0] or w[2]<box[1] or w[3]>box[2] or w[4]>box[3] for w in words):
                return None
            words.sort(key=lambda w:(w[2],w[1]))
            if ''.join(cell.split())!=''.join(''.join(w[0].split()) for w in words):
                return None
    projected=[]
    for row in table.row_texts:
        parts=split_table_cells(row)
        fields=[split_table_field(part) for part in parts[1:]]
        if parts[0]!=f'表格行: T{table.table_number}' or len(fields)!=2 or any(f is None for f in fields):
            return None
        projected.append(tuple(f[1] for f in fields))
    # 兼容已识别表头及历史转义换行；只接受原格的两种既有展示编码。
    def matches(candidate):
        return len(projected)==len(candidate) and all(
            all(_space(value) in {_space(raw),_space(raw.replace('\n','\\n'))} for value,raw in zip(values,cells))
            for values,cells in zip(projected,candidate))
    if not(matches(rows) or matches(rows[1:])):
        return None
    return [tuple(_space(c) for c in row) for row in rows]


def _single_anchored_change(old,new):
    """容许同文或一处局部文字改动；只建复核候选，绝不隐藏该改动。"""
    a,b=literal_atoms(old),literal_atoms(new)
    if a==b:
        return True
    edits=[op for op in SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op[0]!='equal']
    if len(edits)!=1:
        return False
    _,i,j,k,l=edits[0]
    left,right=a[max(0,i-8):i],a[j:j+8]
    if min(len(left),len(right))<4 or b[max(0,k-8):k]!=left or b[l:l+8]!=right:
        return False
    return all(sum(s[n:n+len(anchor)]==anchor for n in range(len(s)-len(anchor)+1))==1
               for s in (a,b) for anchor in (left,right))


def review_split_cells(changes,old_tables,new_tables,boundary_proven):
    """成功仅替换为一条含全部物理格的复核；其余情形完整保留原 changes。"""
    if not changes or sorted((len(old_tables),len(new_tables)))!=[1,2]:
        return changes
    single,split=(old_tables,new_tables) if len(old_tables)==1 else (new_tables,old_tables)
    first,second=split
    if second.page_number!=first.page_number+1 or not boundary_proven(first,second):
        return changes
    whole,a,b=(_verified_rows(t) for t in (single[0],first,second))
    if any(rows is None for rows in (whole,a,b)) or not(whole[0]==a[0]==b[0]):
        return changes
    # 两边列边界按表宽归一后必须一致，不能把列重排解释为续页。
    ratios=[(t.raw_cell_bounds[0][0][2]-t.bbox[0])/(t.bbox[2]-t.bbox[0]) for t in (*single,*split)]
    if max(ratios)-min(ratios)>1e-6:
        return changes
    before,after=a[-1],b[1]
    key=before[0]
    if (key!=after[0] or Counter(r[0] for r in whole[1:])[key]!=1
            or Counter(r[0] for r in [*a[1:],*b[1:]])[key]!=2
            or min(len(before[1]),len(after[1]))<20
            or re.search(r'[.!?。！？;；]$',before[1]) or not re.match(r'[a-z]',after[1])):
        return changes
    joined=[*a[1:-1],(key,before[1]+' '+after[1]),*b[2:]]
    index=len(a)-2
    if (len(joined)!=len(whole)-1 or joined[:index]!=whole[1:index+1]
            or joined[index+1:]!=whole[index+2:] or whole[index+1][0]!=key
            or not _single_anchored_change(whole[index+1][1],joined[index][1])):
        return changes
    receipts=[]
    def describe(tables,side):
        lines=[]
        for table in tables:
            lines.append(f'PDF 第 {table.page_number} 页，表 {table.table_number}')
            for i,(row,boxes) in enumerate(zip(table.raw_source_cells,table.raw_cell_bounds)):
                lines.append(' | '.join(_space(c) for c in row))
                receipts.append(dict(side=side,page=table.page_number,table_number=table.table_number,
                                     row_index=i,bbox=(boxes[0][0],boxes[0][1],boxes[-1][2],boxes[0][3]),raw_cells=row))
        return '\n'.join(lines)
    old_value,new_value=describe(old_tables,'old'),describe(new_tables,'new')
    return [TableRowChange('跨页单元格对应需复核：'+key,old_value,new_value,'需人工复核',
                           source_receipts=tuple(receipts))]
