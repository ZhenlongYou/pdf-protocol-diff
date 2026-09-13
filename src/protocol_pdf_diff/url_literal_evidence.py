"""Reader-only literal URL typography, bound to complete source lines."""
from dataclasses import replace
import math
import re
from collections import Counter


def _box(b):
    return len(b) == 4 and all(type(v) in (int,float) and math.isfinite(v) for v in b) and b[0] < b[2] and b[1] < b[3]


def _flat(s):
    return ' '.join(s.split())


def capture_url_receipts(page, annotations):
    """Exact glyph-center containment; no expanded annotation rectangles."""
    try:
        tm=page.get_textmap(x_tolerance=1,y_tolerance=3)
        if tm.as_string != ''.join(v for v,g in tm.tuples) or tm.line_dir_render != 'ttb' or tm.char_dir_render != 'ltr':
            return ()
        groups={}
        for a in annotations:
            uri=a.get('uri'); b=tuple(a[k] for k in ('x0','top','x1','bottom'))
            if not isinstance(uri,str) or not re.fullmatch(r'https?://[\x21-\x7e]+',uri) or not _box(b):
                continue
            groups.setdefault(uri,[]).append(b)
        tuples=list(tm.tuples); counts=Counter(id(g) for v,g in tuples if v and g is not None)
        offsets=[]; pos=0
        for v,g in tuples:
            offsets.append(pos);pos+=len(v)
        result=[]
        for uri,boxes in groups.items():
            selected=[]
            for i,(v,g) in enumerate(tuples):
                if not v or g is None:
                    continue
                b=tuple(g[k] for k in ('x0','top','x1','bottom'))
                if not _box(b):
                    continue
                cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2
                owners={u for u,bs in groups.items() if any(q[0]<=cx<=q[2] and q[1]<=cy<=q[3] for q in bs)}
                if uri in owners:
                    if owners!={uri} or len(v)!=1 or g.get('text')!=v or counts[id(g)]!=1:
                        selected=[];break
                    selected.append(i)
            if not selected:
                continue
            a,z=selected[0],selected[-1]; chosen=set(selected)
            # Nothing but layout whitespace may intervene in a literal URL.
            if any(v and (g is not None or not v.isspace()) for i,(v,g) in enumerate(tuples[a:z+1],a) if i not in chosen):
                continue
            literal=''.join(tuples[i][0] for i in selected)
            if literal.replace('‐','-')!=uri:
                continue
            start=offsets[a];end=offsets[z]+1;raw=tm.as_string[start:end]
            # Spaces within a line are not URL layout breaks.
            if any(c.isspace() and c!='\n' for c in raw):
                continue
            ls=tm.as_string.rfind('\n',0,start)+1
            le=tm.as_string.find('\n',end);le=len(tm.as_string) if le<0 else le
            witness=tm.as_string[ls:le]
            if tm.as_string.count(raw)!=1:
                continue
            result.append(dict(uri=uri,raw=raw,lines=witness,boxes=boxes,
                glyphs=tuple((tuples[i][0],*(tuples[i][1][k] for k in ('x0','top','x1','bottom'))) for i in selected)))
        return tuple(result)
    except (AttributeError,KeyError,TypeError,ValueError,OverflowError):
        return ()


def extraction_url_receipts(extraction):
    return tuple((p.page_number,r) for p in extraction.pages for r in getattr(p, "url_literal_receipts", ()))


def _valid(r):
    try:
        gs=r['glyphs']; bs=r['boxes']; raw=r['raw']
        return bool(gs and bs and all(_box(b) for b in bs)
            and raw.replace('\n','') == ''.join(g[0] for g in gs)
            and raw.replace('\n','').replace('‐','-') == r['uri']
            and len({tuple(g[1:]) for g in gs}) == len(gs)
            and all(len(g)==5 and len(g[0])==1 and _box(g[1:])
                and any(b[0]<=(g[1]+g[3])/2<=b[2] and b[1]<=(g[2]+g[4])/2<=b[3] for b in bs) for g in gs))
    except (KeyError,TypeError,ValueError):
        return False


def _bound(section,receipts):
    if section is None or not section.body or not section.page_bodies:
        return {}
    bodies=list(section.page_bodies)
    if sorted(p for p,b in bodies)!=list(range(section.start_page,section.end_page+1)) or len({p for p,b in bodies})!=len(bodies) or _flat(' '.join(b for p,b in bodies))!=_flat(section.body):
        return {}
    result={}
    for pn,r in receipts:
        if not _valid(r):
            continue
        matches=[b for p,b in bodies if p==pn]
        raw=_flat(r['raw']); witness=_flat(r['lines'])
        # Whitespace-flexible ordered matching is a duplicate veto only.
        pattern=r'\s*'.join('[‐-]' if c=='-' else re.escape(c) for c in r['uri'])
        if len(list(re.finditer(pattern,section.body)))!=1:
            continue
        if len(matches)!=1 or _flat(matches[0]).count(witness)!=1 or _flat(section.body).count(raw)!=1:
            continue
        result.setdefault(r['uri'],[]).append(r)
    return {u:rs[0] for u,rs in result.items() if len(rs)==1}


def project_url_change(change,old_receipts,new_receipts):
    """Normalize only evidenced hyphen characters; never rewrite raw audit."""
    if change.old_section is None or change.new_section is None or change.old_section.heading_path != change.new_section.heading_path:
        return change
    old=_bound(change.old_section,old_receipts);new=_bound(change.new_section,new_receipts)
    shared=[u for u in old.keys() & new.keys() if old[u]['raw'].replace('‐','-')==new[u]['raw'].replace('‐','-')]
    if not shared:
        return change
    def project(text,side):
        for u in shared:
            r=side[u]; needle=_flat(r['raw'])
            if text.count(needle)==1:
                # Require token endpoints, not a URL prefix inside another URL.
                start=text.index(needle);end=start+len(needle)
                if (start and not text[start-1].isspace()) or (end<len(text) and not text[end].isspace()):
                    continue
                text=text[:start]+needle.replace('‐','-')+text[end:]
        return text
    pairs=[]
    for p in change.replaced_snippets:
        a,b=project(p.old,old),project(p.new,new)
        if a!=b or (a==p.old and b==p.new):
            pairs.append(replace(p,old=a,new=b))
    projected=replace(change,replaced_snippets=pairs)
    if (pairs != change.replaced_snippets and not pairs and not change.added_snippets
            and not change.removed_snippets and not change.review_replaced_snippets
            and not change.context_review_records and not change.omitted_snippet_count):
        return None
    return projected
