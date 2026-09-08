"""Reader navigation over existing facts; never a second comparison engine.

Text windows retain every changed token and its nearby literal context. Source
navigation requires an exact, unique physical word span in the supplied crops.
Uncertain/missing source evidence stays visible without a guessed destination.
"""
from __future__ import annotations

import difflib
import html
import json
import re
from collections.abc import Callable


def difference_windows(old: str, new: str, tokenize: Callable, context: int = 8):
    """Keep the complete supplied context; punctuation cannot prove condition scope.

    Compact changed fragments are shown separately. A preceding sentence or an
    abbreviation such as Fig. B may govern the value, so never cut it away.
    """
    return [(old,new)]


def literal_tokens(text):
    # Deliberately case/symbol preserving: navigation is stricter than comparison.
    return re.findall(r'\w+|[^\w\s]', text, flags=re.UNICODE)


def locate_source(text, visuals, prefix, section_text=None, required_range=None):
    """Unique exact physical span only; duplicate crops of one span count once."""
    needle = literal_tokens(text)
    if not needle:
        return None
    # The screenshot budget may omit pages. A phrase unique in the selected
    # crops is not necessarily the changed occurrence in the complete section.
    section_tokens = literal_tokens(section_text or '')
    if sum(section_tokens[i:i+len(needle)] == needle
           for i in range(len(section_tokens)-len(needle)+1)) != 1:
        return None
    matches = {}
    for index, visual in enumerate(visuals):
        if not visual.source_view_box:
            continue
        tokens, identities = [], []
        for word in visual.source_words:
            if not word[4]:
                tokens.append(None)
                identities.append(None)
                continue
            for token in literal_tokens(word[4]):
                tokens.append(token)
                identities.append((visual.page_number, *word))
        for start in range(len(tokens)-len(needle)+1):
            if tokens[start:start+len(needle)] != needle:
                continue
            span = tuple(dict.fromkeys(identities[start:start+len(needle)]))
            xs = [w[1] for w in span]; ys = [w[2] for w in span]
            rights = [w[3] for w in span]; bottoms = [w[4] for w in span]
            target = {'id': f'{prefix}-{index}', 'box': [min(xs),min(ys),max(rights),max(bottoms)], 'page': visual.page_number}
            matches.setdefault(span, target)
    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1 or required_range is None:
        return None
    # Display the whole condition, but navigation may use a shorter *unique*
    # observed span containing every changed token. Never cross a source break.
    start, end = required_range
    for padding in (4, 2, 1, 0):
        a, b = max(0,start-padding), min(len(needle),end+padding)
        if b-a < 3 or (a == 0 and b == len(needle)):
            continue
        found = locate_source(' '.join(needle[a:b]), visuals, prefix, section_text)
        if found:
            return found
    return None


def changed_ranges(old, new):
    """Literal token spans for navigation only; all changed symbols are retained."""
    a, b = literal_tokens(old), literal_tokens(new)
    ops = [op for op in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op[0] != 'equal']
    if not ops:
        return None, None
    return (min(op[1] for op in ops),max(op[2] for op in ops)), (min(op[3] for op in ops),max(op[4] for op in ops))


def delta_spans(old: str, new: str):
    """Compare literal written numbers atomically; navigation uses word evidence.

    Signs, decimal digits and exponents stay with their number on both sides.
    These are text edits, not inferred parameter ownership or engineering facts.
    """
    number = r'(?<![\w.])[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?(?![\w.])'
    pattern = number + r'|\w+|[^\w\s]'
    parts = [list(re.finditer(pattern,text)) for text in (old,new)]
    literal = [list(re.finditer(r'\w+|[^\w\s]',text)) for text in (old,new)]
    matcher = difflib.SequenceMatcher(None,[m.group() for m in parts[0]], [m.group() for m in parts[1]],autojunk=False)
    for tag,a,b,c,d in matcher.get_opcodes():
        if tag == 'equal':
            continue
        spans = []
        for side,(start,end) in enumerate(((a,b),(c,d))):
            tokens = parts[side]
            lo = tokens[start].start() if start < len(tokens) else len((old,new)[side])
            hi = tokens[end-1].end() if end > start else lo
            first = next((i for i,m in enumerate(literal[side]) if m.end()>lo),len(literal[side]))
            last = (max(i for i,m in enumerate(literal[side]) if m.start()<hi)+1) if hi>lo else first
            spans.append((lo,hi,first,last))
        yield tuple(spans)


def source_button(targets, label='定位对应原文'):
    if not any(targets.values()):
        return '<span class="focus-unresolved">截图定位未确定；请结合下方原文核对。</span>'
    encoded = html.escape(json.dumps(targets, ensure_ascii=False), quote=True)
    return f'<button type="button" class="source-focus-button" data-focus-targets="{encoded}">{html.escape(label)}</button>'


def render_change_focus(change, group, prefix, tokenize, inline, glyph_note=None, format_context=None, allow_deltas=None, escape_text=html.escape):
    """Lead with exact changes; do not promote review pairs to red/green facts."""
    old_visuals = group.old_visuals if group else ()
    new_visuals = group.new_visuals if group else ()
    entries = []
    for pair in change.replaced_snippets:
        entries.extend((old,new,change.change_type == 'review','文字变化')
                       for old,new in difference_windows(pair.old,pair.new,tokenize))
    for pair in change.review_replaced_snippets:
        entries.append((pair.old,pair.new,True,'对应关系待核实'))
    entries.extend(('',text,change.change_type == 'review','新版原文' if change.change_type == 'review' else '新增内容') for text in change.added_snippets)
    entries.extend((text,'',change.change_type == 'review','旧版原文' if change.change_type == 'review' else '删除内容') for text in change.removed_snippets)
    rows = []
    for index,(old,new,review,label) in enumerate(entries,1):
        note = glyph_note(old,new) if glyph_note else ''
        review = review or bool(note)
        old_html,new_html = (format_context(old,new,review) if format_context else
                             ((html.escape(old),html.escape(new)) if review or not old or not new else inline(old,new)))
        # A neutral review target identifies the source excerpt, not a proven change.
        targets = {'old': locate_source(old,old_visuals,prefix+'-old',change.old_section.body if change.old_section else ''),
                   'new': locate_source(new,new_visuals,prefix+'-new',change.new_section.body if change.new_section else '')}
        button = source_button(targets)
        deltas = []
        if old and new and not review and (allow_deltas is None or allow_deltas(old,new)):
            for (lo,hi,a,b),(ln,hn,c,d) in delta_spans(old,new):
                before, after = old[lo:hi], new[ln:hn]
                local_targets = {
                    'old': locate_source(old,old_visuals,prefix+'-old',change.old_section.body if change.old_section else '',(a,b)),
                    'new': locate_source(new,new_visuals,prefix+'-new',change.new_section.body if change.new_section else '',(c,d)),
                }
                uncertain_glyph = bool(re.search(r'[\ue000-\uf8ff]',before+after))
                left_tag, right_tag = ('span','span') if uncertain_glyph else ('del','ins')
                detail_label = '字符待核实' if uncertain_glyph else '文字片段变化'
                glyph_help = '<span class="focus-unresolved"> 字体编码未证实，不能据此确认技术变化。</span>' if uncertain_glyph else ''
                deltas.append(f'<li><span class="focus-delta-label">{detail_label} {len(deltas)+1}</span> <{left_tag}>{escape_text(before) or "∅"}</{left_tag}> → <{right_tag}>{escape_text(after) or "∅"}</{right_tag}>{glyph_help} '+source_button(local_targets)+'</li>')
        delta_html = '<ul class="focus-deltas">'+''.join(deltas)+'</ul>' if deltas else ''
        if deltas:
            button = ''
        rows.append(f'<article class="focus-fact" data-review="{str(review).lower()}"><h4>{index}. {"待核实 · " if review else ""}{label}</h4>'
                    f'{delta_html}<div class="focus-values"><div><b>旧版 · 完整上下文</b> <div class="focus-context">{old_html or "此条无旧版片段"}</div></div><div><b>新版 · 完整上下文</b> <div class="focus-context">{new_html or "此条无新版片段"}</div></div></div>'
                    f'{"<p class=\"focus-unresolved\">"+html.escape(note)+"</p>" if note else ""}{button}</article>')
    if not rows:
        return ''
    visible = ''.join(rows[:5])
    if len(rows)>5:
        visible += f'<details class="focus-more"><summary>其余 {len(rows)-5} 条变化明细（全部保留）</summary>{"".join(rows[5:])}</details>'
    omission = (f'<p class="focus-unresolved">原比较结果另有 {change.omitted_snippet_count} 条片段未展示；此明细不是完整清单，可提高每章展示数量后重新生成。</p>' if change.omitted_snippet_count else '')
    return (f'<div class="reader-focus"><h4>先看具体变化 · {len(rows)} 条明细</h4><p class="focus-help">直接列出原文变化及附近上下文；保留完整片段的条件与单位，完整文字可在下方展开；有来源截图时提供定位。待核实条目不作确定变化标色。</p>'
            +visible+omission+'<div class="focus-preview" aria-live="polite"></div></div>')


FOCUS_CSS = r'''
.reader-focus { margin: 14px 0 20px; padding: 16px; background: #f8fafc; border: 1px solid var(--line); border-radius: 12px; }
.reader-focus > h4 { margin: 0 0 6px; font-size: 18px; }
.focus-help, .focus-unresolved { color: var(--muted); font-size: 13px; line-height: 1.6; }
.reader-guide { padding: 12px 16px; border-left: 3px solid #557b9d; background: #f2f6fa; line-height: 1.7; }
.focus-fact { margin: 12px 0; padding: 12px; background: white; border: 1px solid var(--line); border-radius: 8px; }
.focus-fact > h4 { margin: 0 0 8px; }
.focus-deltas { padding-left: 20px; line-height: 1.8; overflow-wrap: anywhere; }
.focus-deltas del { background: #ffe2e5; color: #8e2430; }
.focus-deltas ins { background: #d5f5e9; color: #075d49; text-decoration: none; font-weight: 600; }
.focus-delta-label { color: var(--muted); font-size: 12px; margin-right: 6px; }
.focus-values, .focus-preview-grid { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 14px; }
.focus-values > div { min-width: 0; }
.focus-values b { font-size: 12px; color: var(--muted); }
.focus-values .focus-context { margin: 6px 0; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.focus-values del { color: #8e2430; background: #ffe2e5; text-decoration: line-through; }
.focus-values ins { color: #075d49; background: #d5f5e9; text-decoration: none; font-weight: 600; }
.source-focus-button { cursor: pointer; background: #eef5ff; color: #224a7b; border: 1px solid #bbcee5; border-radius: 5px; padding: 6px 10px; margin: 6px 5px 3px 0; font: inherit; font-size: 13px; }
.source-focus-button[aria-pressed="true"] { background: #d6e7fb; outline: 2px solid #547aa9; }
.focus-more > summary { cursor: pointer; padding: 10px 0; color: #224a7b; }
.focus-preview:empty { display: none; }
.focus-preview { margin-top: 12px; padding: 12px; border: 2px solid #b78223; background: white; border-radius: 8px; }
.focus-preview h4 { margin: 0 0 8px; }
.focus-preview figure { margin: 0; min-width: 0; }
.focus-preview svg { display: block; max-width: 100%; height: auto; }
.focus-preview .focus-caption { color: var(--muted); font-size: 13px; margin: 6px 0; }
.visual-focus-actions { margin: 10px 0; }
@media (max-width: 760px) { .focus-values, .focus-preview-grid { grid-template-columns: minmax(0,1fr); } }
'''

FOCUS_SCRIPT = r'''
<script>
(function () {
'use strict';
const NS = 'http://www.w3.org/2000/svg';
function svgElement(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k,v]) => node.setAttribute(k,String(v)));
  return node;
}
document.addEventListener('click', function(event) {
  const link = event.target.closest('a[href^="#"]');
  if (link) {
    const target = document.getElementById(link.getAttribute('href').slice(1));
    for (let node=target; node; node=node.parentElement) if (node.tagName==='DETAILS') node.open=true;
  }
  const button = event.target.closest('.source-focus-button');
  if (!button) return;
  const card = button.closest('.change-card, .table-visual-card');
  const panel = card && card.querySelector('.focus-preview');
  if (!panel) return;
  const targets = JSON.parse(button.dataset.focusTargets);
  panel.replaceChildren();
  const point = button.closest('li')?.querySelector('.focus-delta-label')?.textContent || button.textContent;
  const heading = document.createElement('h4'); heading.textContent = point + ' · 原文局部（琥珀框仅用于定位）'; panel.append(heading);
  const grid = document.createElement('div'); grid.className='focus-preview-grid'; panel.append(grid);
  ['old','new'].forEach(side => {
    const figure = document.createElement('figure'); grid.append(figure);
    const caption = document.createElement('div'); caption.className='focus-caption'; figure.append(caption);
    const target = targets[side];
    const source = target && document.getElementById(target.id);
    const img = source && source.querySelector('img');
    const view = source && JSON.parse(source.dataset.sourceView || 'null');
    caption.textContent = (side==='old'?'旧版':'新版') + (target?' · PDF 第 '+target.page+' 页':' · 此条无可靠定位');
    if (!img || !img.complete || !img.naturalWidth || !view) {
      const note=document.createElement('p'); note.textContent='此侧未提供可核验的截图定位，请查看完整证据。'; figure.append(note); return;
    }
    const sx=img.naturalWidth/(view[2]-view[0]), sy=img.naturalHeight/(view[3]-view[1]);
    const b=target.box;
    const x=Math.max(0,(b[0]-view[0])*sx), y=Math.max(0,(b[1]-view[1])*sy);
    const right=Math.min(img.naturalWidth,(b[2]-view[0])*sx), bottom=Math.min(img.naturalHeight,(b[3]-view[1])*sy);
    if (![x,y,right,bottom,sx,sy].every(Number.isFinite) || right<=x || bottom<=y) return;
    const top=Math.max(0,y-32), end=Math.min(img.naturalHeight,bottom+32), height=end-top;
    // Full-width band preserves row/paragraph context and never enlarges pixels.
    const svg=svgElement('svg',{viewBox:`0 ${top} ${img.naturalWidth} ${height}`,width:img.naturalWidth,height,role:'img','aria-label':caption.textContent+'定位区域'});
    svg.append(svgElement('image',{href:img.src,x:0,y:0,width:img.naturalWidth,height:img.naturalHeight}));
    svg.append(svgElement('rect',{x,y,width:right-x,height:bottom-y,fill:'none',stroke:'#b78223','stroke-width':2,'vector-effect':'non-scaling-stroke'}));
    figure.append(svg);
  });
  card.querySelectorAll('.source-focus-button').forEach(b => b.setAttribute('aria-pressed',b===button?'true':'false'));
  panel.scrollIntoView({block:'center',behavior:'auto'});
});
})();
</script>
'''
