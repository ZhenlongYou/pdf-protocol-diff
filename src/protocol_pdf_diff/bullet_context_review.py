"""Reader-only partial proof; unchanged fragment never proves suffix scope."""
from dataclasses import replace
from hashlib import sha256
import json
import re
from types import SimpleNamespace
from .bullet_boundary_evidence import boundary_partial_candidate_indices, _owners, _flatten
from .models import SnippetPair

REASON = '上下文归属待核实：前置片段在双侧相同项目下已完整承接；后续原文的适用项目/条件尚未证明相同。'


def _source_context(section, suffix):
    if section is None or not section.body or not section.page_bodies:
        return None
    page_bodies = list(section.page_bodies)
    if any(not isinstance(n, int) or n < section.start_page or n > section.end_page or not text for n, text in page_bodies):
        return None
    if len({n for n, _ in page_bodies}) != len(page_bodies):
        return None
    if _flatten(' '.join(text for _, text in page_bodies)) != _flatten(section.body):
        return None
    hits = [(n,text) for n,text in page_bodies if _flatten(text).count(suffix)]
    if len(hits) != 1 or _flatten(hits[0][1]).count(suffix) != 1:
        return None
    page, text = hits[0]
    lines = text.splitlines()
    # The bounded candidate requires the full source suffix on one raw line.
    starts = [i for i,line in enumerate(lines) if line.startswith(suffix)]
    if len(starts) != 1:
        return None
    pos=starts[0]
    heads=[i for i,line in enumerate(lines[:pos]) if re.match(r'^\s*[•●▪]\s+\S',line)]
    if not heads:
        return None
    begin=heads[-1]
    end=next((i for i in range(pos+1,len(lines)) if re.match(r'^\s*[•●▪]\s+\S',lines[i])),len(lines))
    return {'page':page,'section_id':section.section_id,'text':suffix,
            'nearby_bullet':lines[begin], 'prefix_context':'\n'.join(lines[begin:pos]),
            'source_context':'\n'.join(lines[begin:end]),
            'scope_status':'unknown; nearby bullet is context, not asserted ownership'}


def partial_bullet_context_review(change, raw):
    """Keep original audit immutable; move uncertainty to explicit neutral review."""
    if raw.old_section is None or raw.new_section is None or change.context_review_records:
        return change
    adds=raw.audit_added_snippets if raw.audit_added_snippets is not None else raw.added_snippets
    pairs=raw.audit_replaced_snippets if raw.audit_replaced_snippets is not None else raw.replaced_snippets
    candidates=[SimpleNamespace(kind='added',text=t,pair=None) for t in adds]
    candidates += [SimpleNamespace(kind='replaced',text='',pair=p) for p in pairs]
    indices=boundary_partial_candidate_indices(raw.old_section.body,raw.new_section.body,candidates)
    if not indices:
        return change
    remove_adds=[];remove_pairs=[];records=[];neutral=[]
    for index in sorted(indices):
        if index < len(adds):
            continue
        pair=candidates[index].pair
        fragment=pair.old[:-len(pair.new)-1]
        if change.added_snippets.count(fragment)!=1 or change.replaced_snippets.count(pair)!=1:
            continue
        old=_source_context(raw.old_section,pair.new);new=_source_context(raw.new_section,pair.new)
        if old is None or new is None:
            continue
        owners=_owners(raw.old_section.body,fragment)
        record={'kind':'bullet_boundary_scope_unknown','reason':REASON,'old':old,'new':new,
                'proved_fragment':fragment,'owner_prefix_receipts':owners,
                'raw_added_index':adds.index(fragment),'raw_replaced_index':index-len(adds),
                'raw_pair':{'old':pair.old,'new':pair.new},'raw_added':fragment}
        record['id']=sha256(json.dumps(record,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
        records.append(record);remove_adds.append(fragment);remove_pairs.append(pair)
        neutral.append(SnippetPair(old['source_context'],new['source_context']))
    if not records:
        return change
    # Applied after other reader cleaners: neutral records cannot be consumed by
    # text-only cleanup or snippet truncation. Original result.changes is intact.
    return replace(change,
        added_snippets=[x for x in change.added_snippets if x not in remove_adds],
        replaced_snippets=[x for x in change.replaced_snippets if x not in remove_pairs],
        review_replaced_snippets=[*change.review_replaced_snippets,*neutral],
        context_review_records=records,
        review_reason='; '.join(x for x in [change.review_reason,REASON] if x),
        audit_added_snippets=list(adds),
        audit_replaced_snippets=list(pairs))
