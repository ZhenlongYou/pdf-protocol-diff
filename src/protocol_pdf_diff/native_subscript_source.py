"""Bind local subscript movement to native TextMap glyph occurrences."""
from collections import Counter
from bisect import bisect_left, bisect_right
import math
from .source_char_evidence import source_char_map_for_final_text


def _key(w):
    return (w['text'], *(float(w[k]) for k in ('x0','top','x1','bottom')))


def repair_native_subscripts(text, page, words):
    """A missing exact current-view mapping leaves the input untouched."""
    from .pdf_extract import _body_words_form_visual_subscript, _word_effective_size, _indexed_visual_word_lines, _words_to_visual_line
    try:
        tm=page.get_textmap(x_tolerance=1,y_tolerance=3)
        mapping=source_char_map_for_final_text(text,tm)
        if not mapping or len({g[5] for g in mapping})!=len(mapping):return text
        # Exact same-position native overprint is not independent source text.
        if len({tuple(g[1:5]) for g in mapping})!=len(mapping):return text
        positions=[i for i,c in enumerate(text) if not c.isspace()]
        if len(positions)!=len(mapping):return text
        native=[]
        for value,g in tm.tuples:
            for c in value:
                if not c.isspace():native.append((c,g))
        identity={id(g):rank for rank,(c,g) in enumerate(native)}
        source=page.extract_words(keep_blank_chars=False,use_text_flow=False,extra_attrs=['size','fontname'],return_chars=True)
        by_key={}
        for w in source:
            key=_key(w)
            if key in by_key:return text
            chars=[g for g in w['chars'] if not g['text'].isspace()]
            if ''.join(g['text'] for g in chars)!=w['text']:return text
            ranks=[identity.get(id(g)) for g in chars]
            if any(i is None for i in ranks):return text
            if ranks!=list(range(ranks[0],ranks[0]+len(ranks))):return text
            if ''.join(mapping[i][0] for i in ranks)!=w['text']:return text
            by_key[key]=ranks
        keys=[_key(w) for w in words]
        if len(set(keys))!=len(keys) or set(keys)!=set(by_key):return text
        owned=[i for k in keys for i in by_key[k]]
        if len(owned)!=len(mapping) or set(owned)!=set(range(len(mapping))):return text
        ordered=sorted(range(len(words)),key=lambda i:float(words[i]['x0']))
        xs=[float(words[i]['x0']) for i in ordered];edges=[]
        for i,w in enumerate(words):
            size=_word_effective_size(w);right=float(w['x1'])
            lo=math.nextafter(right+math.nextafter(-size*.20,-math.inf),-math.inf)
            hi=math.nextafter(right+math.nextafter(size*.35,math.inf),math.inf)
            for at in range(bisect_left(xs,lo),bisect_right(xs,hi)):
                j=ordered[at]
                if i!=j and _body_words_form_visual_subscript(w,words[j]):edges.append((i,j))
        degrees=Counter(i for e in edges for i in e)
        lines=_indexed_visual_word_lines(words);line_of={i:n for n,line in enumerate(lines) for i in line}
        line_keys=[''.join(_words_to_visual_line([words[i] for i in line]).split()) for line in lines]
        repeated=Counter(line_keys)
        deletions=set();insertions={}
        for i,j in edges:
            if degrees[i]!=1 or degrees[j]!=1:continue
            # New scope is repeated physical baselines only. Unique rows retain
            # their existing complete-line repair, avoiding partial interference.
            if repeated[line_keys[line_of[i]]] < 2:continue
            if line_of.get(j)!=line_of.get(i,-2)+1:continue
            if not (words[i]['text'].isascii() and words[j]['text'].isascii()):continue
            a=[positions[k] for k in by_key[keys[i]]];b=[positions[k] for k in by_key[keys[j]]]
            # Unsupported reverse native order cannot borrow another occurrence.
            if a[-1]>=b[0]:continue
            row_ranks={rank for n in (line_of[i],line_of[j]) for k in lines[n] for rank in by_key[keys[k]]}
            if any(rank not in row_ranks for rank in range(by_key[keys[i]][-1]+1,by_key[keys[j]][0])):
                continue  # Do not move a suffix across another physical body row.
            deletions.update(b);insertions[a[-1]]=''.join(text[k] for k in b)
            if all(text[k].isspace() for k in range(a[-1]+1,b[0])):
                deletions.update(range(a[-1]+1,b[0]))
        if not insertions:return text
        result=''.join(('' if i in deletions else c)+insertions.get(i,'') for i,c in enumerate(text))
        return result if Counter(c for c in result if not c.isspace())==Counter(c for c in text if not c.isspace()) else text
    except (AttributeError,KeyError,TypeError,ValueError,OverflowError,IndexError):
        return text
