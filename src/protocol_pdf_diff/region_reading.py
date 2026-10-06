"""按全宽行分隔正文区域；复用已有双栏证据，不把数值表改成列优先。"""

from dataclasses import replace
from collections import Counter
import re
from .layout_blocks import _text_block_from_word_line  # 坐标/字体仍来自同一批原生词。


def column_region_blocks(page, words, page_number):
    """返回完整、逐词守恒的区域阅读计划；证据不足时返回空元组。

    跨栏标题或段落作为区域边界。每一区域独立经过现有强正文判定，
    未获准的区域保留逐行顺序。原始 blocks 不修改，比较视图另行保存。
    """
    from . import pdf_extract as e  # 运行时导入以复用既有几何规则，避免第二套阈值。
    lines = _source_word_lines(words, e)
    left_edge, right_edge = e._page_horizontal_bounds(page)
    width = right_edge - left_edge
    if not lines or width <= 0:
        return ()
    region_texts = {}  # 不同栏缝候选复用同一个纵向范围的强证据检查，避免重复解析。
    for fraction in e._COLUMN_GUTTER_FRACTIONS:
        gutter = left_edge + width * fraction
        minimum_gap = max(30.0, width * 0.08)
        output, pending = [], []
        changed = False

        def flush():
            nonlocal changed
            if not pending:
                return
            region_words = [word for line in pending for word in line]
            key = (id(pending[0]), id(pending[-1]))  # 范围内的物理行固定，不依赖候选栏缝。
            if key not in region_texts:
                region_texts[key] = e._high_confidence_column_major_text(page, region_words)
            expected = region_texts[key]
            left = [[w for w in line if float(w["x1"]) <= gutter] for line in pending]
            right = [[w for w in line if float(w["x0"]) >= gutter] for line in pending]
            candidate = [line for line in (*left, *right) if line]
            text = "\n".join(e._words_to_visual_line(line) for line in candidate)
            # 原有强证据必须支持同一栏缝与完整文本，不能只按词袋近似接受。
            if expected is not None and text == expected:
                output.extend(candidate)
                changed = True
            else:
                output.extend(pending)  # 参数表、短旁注、归属不明区仍按物理行读取。
            pending.clear()

        for line in lines:
            left = [w for w in line if float(w["x1"]) <= gutter]
            right = [w for w in line if float(w["x0"]) >= gutter]
            crosses = len(left) + len(right) != len(line)
            close_gap = bool(left and right and min(float(w["x0"]) for w in right)
                             - max(float(w["x1"]) for w in left) < minimum_gap)
            if crosses or close_gap:
                flush()
                output.append(line)  # 全宽标题/正文只能在两个区域之间，不并入单侧栏。
            else:
                pending.append(line)
        flush()
        if changed:
            # 输出来自原始词引用，每个词恰好保留一次；保留原字号、字体和坐标。
            return tuple(replace(_text_block_from_word_line(line, page_number, index),
                                 text=e._words_to_visual_line(line))
                         for index, line in enumerate(output))
    return _narrow_prose_blocks(page, words, page_number)


def _narrow_prose_blocks(page, words, page_number):
    """论文窄栏用持续栏缝与跨行段落证明顺序，不要求两栏行基线重合。

    参数表仍须按行读。这里要求两侧都有长正文和多行续句，再排除表头及
    实际网格；一两条图注、长参数名或重复数字不足以授权重排。
    """
    from . import pdf_extract as e
    lines = _source_word_lines(words, e)
    left_edge, right_edge = e._page_horizontal_bounds(page)
    width = right_edge-left_edge
    # 先复用已有坐标排除普通页。只有两侧均有足量文字的无穿越行才值得
    # 再以较细词间阈值取词，避免每个单栏页/短表格都增加一次解析。
    possible = False
    for fraction in e._COLUMN_GUTTER_FRACTIONS:
        gutter = left_edge+width*fraction
        uncrossed = [line for line in lines
                     if not any(float(w["x0"]) < gutter < float(w["x1"]) for w in line)]
        sides = ([[w for w in line if float(w["x1"]) <= gutter] for line in uncrossed],
                 [[w for w in line if float(w["x0"]) >= gutter] for line in uncrossed])
        if all(sum(bool(line) for line in side) >= 8
               and sum(len(str(w["text"])) for line in side for w in line) >= 300 for side in sides):
            possible = True
            break
    if not possible:
        return ()
    # 原生正文使用 1pt 词间阈值；默认 3pt 会把论文的小字号单词粘连。
    # 精细分词必须保持全部非空白字符及出现次数，不能补词或丢词。
    try:
        refined = page.extract_words(x_tolerance=1, keep_blank_chars=False, use_text_flow=False,
                                     extra_attrs=["size", "fontname"])
        signature = lambda values: Counter("".join("".join(str(w["text"]).split()) for w in values))
        if isinstance(refined, list) and signature(refined) == signature(words):
            words = refined
    except Exception:
        pass  # 不能细分时保留已有坐标，后面的正文证据仍须通过。
    lines = _source_word_lines(words, e)
    for fraction in e._COLUMN_GUTTER_FRACTIONS:
        gutter = left_edge+width*fraction
        output, pending, changed = [], [], False

        def flush():
            nonlocal changed
            if not pending:
                return
            flat = [w for line in pending for w in line]
            left = [w for w in flat if float(w["x1"]) <= gutter]
            right = [w for w in flat if float(w["x0"]) >= gutter]
            left_lines, right_lines = _source_word_lines(left, e), _source_word_lines(right, e)
            gap = min((float(w["x0"]) for w in right), default=gutter)-max((float(w["x1"]) for w in left), default=gutter)
            if (gap >= 8 and _continuous_column_paragraphs(left_lines, e)
                    and _continuous_column_paragraphs(right_lines, e)
                    and not _aligned_quantity_cells(left_lines + right_lines)
                    and not e._looks_like_borderless_table_header_pair(
                        e._words_to_visual_line(left_lines[0]), e._words_to_visual_line(right_lines[0]))
                    and not e._has_local_grid_evidence(page,
                        [min(float(w["top"]) for w in line) for line in pending],
                        gutter_x=gutter, minimum_gap=8)):
                output.extend((*left_lines, *right_lines))
                changed = True
            else:
                output.extend(pending)
            pending.clear()

        for line in lines:
            if any(float(w["x0"]) < gutter < float(w["x1"]) for w in line):
                flush()
                output.append(line)
            else:
                pending.append(line)
        flush()
        if changed:
            return tuple(replace(_text_block_from_word_line(line, page_number, i), text=e._words_to_visual_line(line))
                         for i, line in enumerate(output))
    return ()


def _source_word_lines(words, extractor):
    """复用行分组规则但保留原始字体/字号，避免新阅读视图丢失来源属性。"""
    def key(word):
        return (extractor.normalize_line(str(word["text"])), *(float(word[k]) for k in ("x0", "top", "x1", "bottom")))
    original = {key(word): word for word in words}
    return [[{**original.get(key(word), {}), **word} for word in line]
            for line in extractor._visual_word_lines(words)]


def _aligned_quantity_cells(lines):
    """三行以上在同一 x 位置重复出现的量值可能是数值列，不能转为正文栏。"""
    entries = []
    quantity = re.compile(r"^[+−-]?\d+(?:\.\d+)?(?:mV|V|mA|A|W|dB|ns|ps|mm|MHz|GHz)?$")
    unit = re.compile(r"^(?:mV|V|mA|A|W|dB|ns|ps|mm|MHz|GHz)[,.;]?$" )
    for line in lines:
        ordered = sorted(line, key=lambda w: float(w["x0"]))
        for i, word in enumerate(ordered):
            value = str(word["text"])
            if quantity.fullmatch(value) and (re.search(r"[A-Za-z]", value)
                    or (i+1 < len(ordered) and unit.fullmatch(str(ordered[i+1]["text"])))):
                entries.append((float(word["x0"]), float(word["top"])))
    return any(len({top for x, top in entries if abs(x-anchor) <= 2}) >= 3 for anchor, _top in entries)


def _continuous_column_paragraphs(lines, extractor):
    if len(lines) < 8:
        return False
    texts = [extractor._words_to_visual_line(line) for line in lines]
    prose = [extractor._looks_like_continuous_prose_line(t) for t in texts]
    if sum(prose) < 8 or sum(prose)/len(lines) < .6 or sum(len(t) for t in texts) < 300:
        return False
    left = min(float(w["x0"]) for line in lines for w in line)
    right = max(float(w["x1"]) for line in lines for w in line)
    chain = 0
    for index, (before, after) in enumerate(zip(lines, lines[1:])):
        advance = min(float(w["top"]) for w in after)-min(float(w["top"]) for w in before)
        continues = (prose[index] and prose[index+1] and 6 <= advance <= 22
                     and right-max(float(w["x1"]) for w in before) <= 18
                     and min(float(w["x0"]) for w in after)-left <= 12
                     and re.search(r"[.!?。！？:;]$", texts[index]) is None)
        chain = chain+1 if continues else 0
        if chain >= 2:
            return True
    return False


def verified_comparison_blocks(page, final_text):
    """表格剥离/OCR 等改变文本后，只接受仍与最终文字逐字对应的计划。"""
    blocks = getattr(page, "_comparison_region_blocks", ())
    if " ".join(final_text.split()) == " ".join(" ".join(b.text for b in blocks).split()):
        return blocks
    return ()  # 不能把过时坐标计划强加到改写后的文字流。
