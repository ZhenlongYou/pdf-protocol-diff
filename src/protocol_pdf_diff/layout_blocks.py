"""将 PDF 坐标词、OCR 和表格摘要转换为不可变文档块证据。

本模块刻意不改变 ``PageText.text`` 或比较器的阅读顺序。它只保留可审计的
版面事实，供后续可选解析后端与当前 pdfplumber/Tesseract 路径公平对照。
"""

from __future__ import annotations

import math  # 坐标必须是有限实数，避免 NaN/Infinity 破坏稳定排序。
from collections.abc import (  # 接受 pdfplumber 返回的映射对象，同时避免依赖其私有类型。
    Iterable,
    Mapping,
)
from dataclasses import replace  # 冻结 dataclass 需要 replace 才能安全重写阅读序号。

from .models import DocumentBlock, DocumentBlockKind, TableVisual
from .text_utils import normalize_line

# 相差不超过 3pt 的词通常属于同一条 PDF 文字基线；该值与既有阅读顺序检查保持一致。
_WORD_LINE_TOP_TOLERANCE = 3.0


def extract_pdfplumber_text_blocks(
    page: object,
    page_number: int,
) -> tuple[tuple[DocumentBlock, ...], list[str]]:
    """从过滤后的 pdfplumber 页面生成稳定排序的原生文本行块。

    ``extract_words`` 参数遵循 pdfplumber 官方文档：
    https://github.com/jsvine/pdfplumber#extracting-text 。禁用文本流重排，
    让块的顺序只由可检查的 ``top``/``x0`` 坐标决定。
    """

    # 公共便捷入口只负责一次提取/校验，纯构块函数可供调用方复用同一观测。
    words, warnings, _coordinate_error = extract_pdfplumber_coordinate_words(
        page,
        page_number,
    )
    # 已校验坐标直接转换为块，避免再次调用页面的 extract_words。
    return build_pdfplumber_text_blocks(words, page_number), warnings


def extract_pdfplumber_coordinate_words(
    page: object,
    page_number: int,
) -> tuple[list[dict[str, float | str]], list[str], str | None]:
    """一次提取并校验坐标词，供块生成和阅读顺序风险检查共同复用。"""

    # 警告要随提取结果返回，以便无坐标证据时报告能够提示人工复核。
    warnings: list[str] = []
    try:
        # 通过公开 pdfplumber API 取得单词和页面坐标，不能依赖内部 chars 排序。
        raw_words = page.extract_words(
            keep_blank_chars=False,
            use_text_flow=False,
            extra_attrs=["size", "fontname"],
        ) or []
    except Exception as exc:
        # 同时返回布局检查可复用的错误文字，避免第二次调用页面方法。
        coordinate_error = f"无法提取坐标词: {exc}"
        return [], [f"第 {page_number} 页文档块坐标词提取失败: {exc}"], coordinate_error
    if not isinstance(raw_words, list):
        # 非列表返回不符合 pdfplumber 公开契约，风险检查也必须降级而非再次尝试。
        return [], [f"第 {page_number} 页文档块坐标词格式无效，已跳过。"], "坐标词格式无效"

    # 仅保存经文本、范围和有限数值校验后的坐标词。
    valid_words: list[dict[str, float | str]] = []
    # 相同文字和相同边界的重复词只保留一次，防止重叠 XObject 虚增证据。
    seen_words: set[tuple[str, float, float, float, float]] = set()
    for raw_word in raw_words:
        # 单词校验与诊断集中在 helper 中，主循环只处理可用证据。
        word, warning = _validated_coordinate_word(raw_word)
        if warning is not None:
            # 每个被跳过的词都留下有限、无文本内容的诊断，而非泄漏源文档段落。
            warnings.append(f"第 {page_number} 页{warning}")
        if word is None:
            # 空白、错误或退化边界不能形成可审计块。
            continue
        # 精确 bbox 和文本共同定义一条可去重的物理词证据。
        word_key = (
            str(word["text"]),
            float(word["x0"]),
            float(word["x1"]),
            float(word["top"]),
            float(word["bottom"]),
        )
        if word_key in seen_words:
            # 完全重复的坐标词不代表另一段内容，跳过并让审计者知道原因。
            warnings.append(f"第 {page_number} 页跳过重复坐标词。")
            continue
        # 新的物理词才加入同页阅读顺序候选。
        seen_words.add(word_key)
        valid_words.append(word)
    # 成功读取的页面即使没有有效词也不视为 API 失败，由质量层按证据不足降级。
    return valid_words, warnings, None


def build_pdfplumber_text_blocks(
    words: list[dict[str, float | str]],
    page_number: int,
) -> tuple[DocumentBlock, ...]:
    """从一次已校验的坐标词观测生成按几何排序的原生文本行块。"""

    # 原地排序前先复制列表，防止调用方复用同一坐标观测时顺序被意外修改。
    valid_words = list(words)
    # 先按垂直、水平和文本键排序，避免底层字典迭代顺序影响输出。
    valid_words.sort(
        key=lambda word: (
            float(word["top"]),
            float(word["x0"]),
            float(word["x1"]),
            str(word["text"]),
        )
    )
    # 临近 top 的词归为同一物理文字行，再在行内按 x0 排序。
    word_lines = _group_coordinate_words_into_lines(valid_words)
    # 每条物理行转换成一个 TEXT 证据块，初始序号已经连续。
    blocks = tuple(
        _text_block_from_word_line(word_line, page_number, reading_order)
        for reading_order, word_line in enumerate(word_lines)
    )
    # 返回的顺序与序号均由本函数固定，方便后续追加其他证据后统一重编号。
    return reassign_block_reading_order(blocks)


def page_bounds(page: object) -> tuple[float, float, float, float] | None:
    """返回页面或裁剪页面的有效边界，供整页 OCR 块使用。"""

    # pdfplumber 的裁剪页会带 bbox；没有时再退回完整页面宽高。
    raw_bbox = getattr(page, "bbox", None)
    if isinstance(raw_bbox, tuple | list) and len(raw_bbox) == 4:
        # 统一通过几何校验，拒绝反向或无穷坐标。
        return _validated_bbox(raw_bbox)
    # 原始页面通常用零原点和 width/height 表示完整范围。
    return _validated_bbox((0.0, 0.0, getattr(page, "width", None), getattr(page, "height", None)))


def make_ocr_document_block(
    page: object,
    page_number: int,
    text: str,
) -> tuple[DocumentBlock | None, str | None]:
    """为成功 OCR 的整页文字生成 tesseract 来源块，绝不伪造置信度。"""

    # OCR 成功后才允许建立块；纯空白文本不应成为虚假的 OCR 证据。
    normalized_text = _normalized_nonempty_text(text)
    if not normalized_text:
        # 调用方据此保留 OCR 事实但不创建空内容块。
        return None, f"第 {page_number} 页 OCR 文档块文本为空，已跳过。"
    # 页边界给整页 OCR 提供可复核的覆盖范围。
    bbox = page_bounds(page)
    if bbox is None:
        # 缺少几何证据时不能构造虚假的 OCR 坐标。
        return None, f"第 {page_number} 页 OCR 文档块页面边界无效，已跳过。"
    # Tesseract 的字符串 API 没有可靠统一置信度，因此字段必须保持 None。
    return (
        DocumentBlock(
            page_number=page_number,
            bbox=bbox,
            kind=DocumentBlockKind.OCR,
            text=normalized_text,
            reading_order=0,
            source_engine="tesseract",
            confidence=None,
        ),
        None,
    )


def make_table_document_blocks(
    table_visuals: Iterable[TableVisual],
    page_number: int,
) -> tuple[tuple[DocumentBlock, ...], list[str]]:
    """把已接受的 ``TableVisual`` 转为带原始 bbox 的表格证据块。"""

    # 表格诊断与原生坐标诊断分开保存，便于报告定位视觉证据缺口。
    warnings: list[str] = []
    # 表格块按检测顺序保留，不因标题或表格行文字而猜测其业务顺序。
    blocks: list[DocumentBlock] = []
    for visual in table_visuals:
        if visual.page_number != page_number:
            # 跨页 visual 不属于当前 PageText，防止坐标和页码失配。
            continue
        # TableVisual 的 bbox 是截图/识别已经采用的边界，块必须复用同一事实。
        bbox = _validated_bbox(visual.bbox)
        if bbox is None:
            # 无效表格边界不能进入坐标 IR，但原有表格比较行为不受影响。
            warnings.append(f"第 {page_number} 页表格 {visual.table_number} 文档块边界无效，已跳过。")
            continue
        # 行级摘要保留换行，既能复核表格内容也不重写现有 PageText.text。
        row_text = "\n".join(
            line
            for raw_line in visual.row_texts
            if (line := _normalized_nonempty_text(raw_line))
        )
        # 接受的空表格仍可提供截图边界事实，因此允许 text 为空。
        blocks.append(
            DocumentBlock(
                page_number=page_number,
                bbox=bbox,
                kind=DocumentBlockKind.TABLE,
                text=row_text,
                reading_order=len(blocks),
                source_engine="pdfplumber",
                confidence=None,
            )
        )
    # 该子集的序号也保持连续；全页合并后调用方会再次统一编号。
    return reassign_block_reading_order(blocks), warnings


def reassign_block_reading_order(
    blocks: Iterable[DocumentBlock],
) -> tuple[DocumentBlock, ...]:
    """按页面几何稳定排序全部证据，并将页内序号重写为 0..N-1。"""

    # 页码、top、x0 是跨来源的主要阅读顺序；其余边界和来源键显式打破重叠平局。
    ordered_blocks = sorted(blocks, key=_document_block_geometry_sort_key)
    # replace 保持 DocumentBlock 的 frozen 合同，同时使新增/删除证据后不留下序号缺口。
    return tuple(
        replace(block, reading_order=reading_order)
        for reading_order, block in enumerate(ordered_blocks)
    )


def _document_block_geometry_sort_key(block: DocumentBlock) -> tuple[object, ...]:
    """返回跨类型块的确定性几何排序键，重叠块不依赖收集先后。"""

    # 同一 bbox 的 TEXT、TABLE、OCR 固定按该优先级排列，便于人工解释重叠证据。
    kind_rank = {
        DocumentBlockKind.TEXT: 0,
        DocumentBlockKind.TABLE: 1,
        DocumentBlockKind.OCR: 2,
    }[block.kind]
    # bbox 后续边、引擎、文字和旧序号只在重叠时用作稳定 tie-break，主序始终是页面坐标。
    return (
        block.page_number,
        block.bbox[1],
        block.bbox[0],
        block.bbox[3],
        block.bbox[2],
        kind_rank,
        block.source_engine,
        block.text,
        block.reading_order,
    )


def _validated_coordinate_word(
    raw_word: object,
) -> tuple[dict[str, float | str] | None, str | None]:
    """校验一条 pdfplumber 词对象，并返回无源文本泄漏的失败原因。"""

    if not isinstance(raw_word, Mapping):
        # 只有映射对象具备公开 extract_words 的字段语义。
        return None, "跳过格式错误的坐标词。"
    # 文字必须是字符串，None 或任意对象的 repr 都不能被当成文档内容。
    raw_text = raw_word.get("text")
    if not isinstance(raw_text, str):
        return None, "跳过缺少文本的坐标词。"
    # 统一空白后再决定是否为可见文字，避免空格词制造空块。
    text = _normalized_nonempty_text(raw_text)
    if not text:
        return None, "跳过空白坐标词。"
    # 四个页面坐标必须同时存在且能安全转换为有限浮点数。
    bbox = _validated_bbox((raw_word.get("x0"), raw_word.get("top"), raw_word.get("x1"), raw_word.get("bottom")))
    if bbox is None:
        return None, "跳过坐标无效的坐标词。"
    # 字号变化是区分正文基符号与视觉下标的公开几何证据；旧测试夹具或
    # 异常后端没有 size 时仍保留 bbox 高度回退，不因此丢弃整个坐标词。
    validated_word: dict[str, float | str] = {
        "text": text,
        "x0": bbox[0],
        "top": bbox[1],
        "x1": bbox[2],
        "bottom": bbox[3],
    }
    raw_size = raw_word.get("size")
    if not isinstance(raw_size, bool):
        try:
            size = float(raw_size)
        except (TypeError, ValueError):
            size = 0.0
        if math.isfinite(size) and size > 0:
            validated_word["size"] = size
    # 字体名只作为同一文档内的版式证据，不解释具体厂商前缀或伪造粗体语义。
    raw_fontname = raw_word.get("fontname")
    if isinstance(raw_fontname, str):
        fontname = raw_fontname.strip()
        if fontname:
            validated_word["fontname"] = fontname
    return validated_word, None


def _validated_bbox(
    raw_bbox: Iterable[object],
) -> tuple[float, float, float, float] | None:
    """把一组候选坐标转换为有限且有面积的统一 bbox。"""

    # 先物化 iterable，避免生成器被多次读取而产生不稳定的校验结果。
    values = tuple(raw_bbox)
    if len(values) != 4:
        # bbox 必须恰好有左右和上下四个边界。
        return None
    if any(isinstance(value, bool) for value in values):
        # 布尔值虽然可转 float，却不是可信的 PDF 坐标。
        return None
    try:
        # 坐标统一为 x0、top、x1、bottom，符合 pdfplumber 的 page 坐标契约。
        x0, top, x1, bottom = (float(value) for value in values)
    except (TypeError, ValueError):
        # 文本或缺失字段不能作为坐标证据。
        return None
    if not all(math.isfinite(value) for value in (x0, top, x1, bottom)):
        # NaN/Infinity 无法排序，也不能映射到可审计页面区域。
        return None
    if x1 <= x0 or bottom <= top:
        # 零面积或反向边界不是可见文字/表格/OCR 区域。
        return None
    # 保留原始坐标方向，不交换反向边界以免悄悄掩盖上游解析错误。
    return x0, top, x1, bottom


def _group_coordinate_words_into_lines(
    words: list[dict[str, float | str]],
) -> list[list[dict[str, float | str]]]:
    """将已排序的坐标词按相邻 top 归并为稳定的物理行。"""

    # 输出列表的顺序就是垂直阅读顺序，后续不再依赖 pdfplumber 返回顺序。
    lines: list[list[dict[str, float | str]]] = []
    for word in words:
        if not lines:
            # 第一条词自然开启第一页的第一条物理行。
            lines.append([word])
            continue
        # 以当前行首 top 作为基准，避免链式漂移把相邻两行错误合并。
        line_top = float(lines[-1][0]["top"])
        if abs(float(word["top"]) - line_top) <= _WORD_LINE_TOP_TOLERANCE:
            # 同一基线的词暂存，随后按 x0 建立左到右顺序。
            lines[-1].append(word)
            continue
        # 新的 top 位置代表下一条物理行。
        lines.append([word])
    for line in lines:
        # 词的同 top 并列情况也用 x1/text 作为确定性 tie-breaker。
        line.sort(
            key=lambda word: (
                float(word["x0"]),
                float(word["x1"]),
                str(word["text"]),
            )
        )
    # 每个元素都是至少一条有效词，因此没有空行需要返回给调用方。
    return lines


def _text_block_from_word_line(
    word_line: list[dict[str, float | str]],
    page_number: int,
    reading_order: int,
) -> DocumentBlock:
    """把已按 x0 排序的一行词构造成一个原生文本证据块。"""

    # 行文字按视觉左右顺序拼接，保留 PDF 词边界而不做语义改写。
    text = " ".join(str(word["text"]) for word in word_line)
    # 行 bbox 取所有词的外接矩形，供报告或后续版面引擎回查。
    bbox = (
        min(float(word["x0"]) for word in word_line),
        min(float(word["top"]) for word in word_line),
        max(float(word["x1"]) for word in word_line),
        max(float(word["bottom"]) for word in word_line),
    )
    # 原生坐标词来自 pdfplumber，不应把未提供的置信度伪造成数字。
    return DocumentBlock(
        page_number=page_number,
        bbox=bbox,
        kind=DocumentBlockKind.TEXT,
        text=text,
        reading_order=reading_order,
        source_engine="pdfplumber",
        confidence=None,
        font_names=tuple(
            sorted(
                {
                    str(word["fontname"])
                    for word in word_line
                    if isinstance(word.get("fontname"), str)
                    and str(word["fontname"]).strip()
                }
            )
        ),
        word_boxes=tuple(
            (
                str(word["text"]),
                float(word["x0"]),
                float(word["top"]),
                float(word["x1"]),
                float(word["bottom"]),
            )
            for word in word_line
        ),
        word_styles=tuple(
            (str(word.get("fontname", "")), float(word.get("size", 0) or 0))
            for word in word_line
        ),
    )


def _normalized_nonempty_text(value: str) -> str:
    """合并一段文字内部空白，并把纯空白统一为无内容。"""

    # normalize_line 是比较层已采用的空白规则，块证据复用它以降低无意义差异。
    return normalize_line(value)
