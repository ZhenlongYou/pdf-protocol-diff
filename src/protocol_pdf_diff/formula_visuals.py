"""Extract conservative, screenshot-backed displayed-formula evidence."""

from __future__ import annotations

import base64
import io
import re
from collections import Counter
from statistics import median

from .models import FormulaVisual
from .text_utils import normalize_line, readable_symbol_font_glyphs


_FORMULA_NUMBER_RE = re.compile(r"\(\d+(?:[-–.]\d+)+\)")
_RELATION_RE = re.compile(r"[=<>\u2264\u2265\u2248\u2260\uf03c\uf03e\uf0a3]")
_FORMULA_SCREENSHOT_RESOLUTION = 144
_FORMULA_PADDING_X = 8.0
_FORMULA_PADDING_Y = 7.0


def extract_formula_visuals(
    page: object,
    words: list[dict[str, float | str]],
    page_number: int,
    *,
    excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[list[FormulaVisual], list[str]]:
    """Return numbered displayed equations whose geometry is independently visible.

    The semantic string is intentionally narrower than mathematical OCR.  It
    rebuilds only size/position-proven superscripts and subscripts; the clean
    source crop remains authoritative for radicals, fractions, stacked limits,
    and vector-drawn operators.
    """

    visuals: list[FormulaVisual] = []
    warnings: list[str] = []
    valid_words = _validated_words(words)
    if not valid_words:
        return visuals, warnings
    page_bbox = _page_bbox(page)
    for label in valid_words:
        formula_number = normalize_line(str(label["text"]))
        if _FORMULA_NUMBER_RE.fullmatch(formula_number) is None:
            continue
        main_words = _formula_main_line(valid_words, label, page_bbox)
        if not main_words:
            continue
        semantic_text, source_text, attached_scripts = _formula_semantic_text(
            valid_words,
            main_words,
            label,
        )
        if not _looks_like_display_formula(semantic_text):
            continue
        used_words = [*main_words, *attached_scripts, label]
        bbox = _words_bbox(used_words)
        if any(_bbox_center_is_inside(bbox, excluded) for excluded in excluded_bboxes):
            continue  # 表格内公式由已有表格截图承载，避免重复卡片。
        image, image_status = _formula_image(page, bbox)
        if image is None:
            warnings.append(
                f"第 {page_number} 页公式 {formula_number} 源截图失败: {image_status}"
            )
            image_data_uri = ""
            image_dhash = ""
        else:
            image_data_uri = _image_to_data_uri(image)
            image_dhash = _difference_hash(image)
        visuals.append(
            FormulaVisual(
                page_number=page_number,
                formula_number=readable_symbol_font_glyphs(formula_number),
                bbox=bbox,
                image_data_uri=image_data_uri,
                source_text=readable_symbol_font_glyphs(
                    f"{source_text} {formula_number}"
                ),
                semantic_text=readable_symbol_font_glyphs(semantic_text),
                script_count=len(attached_scripts),
                image_dhash=image_dhash,
            )
        )
    return visuals, warnings


def _validated_words(
    words: list[dict[str, float | str]],
) -> list[dict[str, float | str]]:
    """Keep only finite, non-degenerate word geometry used by this module."""

    valid: list[dict[str, float | str]] = []
    for word in words:
        try:
            text = normalize_line(str(word.get("text", "")))
            x0 = float(word["x0"])
            x1 = float(word["x1"])
            top = float(word["top"])
            bottom = float(word["bottom"])
            size = float(word["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or x1 <= x0 or bottom <= top or size <= 0:
            continue
        valid.append(
            {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
                "size": size,
            }
        )
    return valid


def _page_bbox(page: object) -> tuple[float, float, float, float]:
    """Return the current pdfplumber page bounds as finite floats."""

    width = float(getattr(page, "width", 0.0) or 0.0)
    height = float(getattr(page, "height", 0.0) or 0.0)
    raw = getattr(page, "bbox", None) or (0.0, 0.0, width, height)
    return tuple(float(value) for value in raw)  # type: ignore[return-value]


def _formula_main_line(
    words: list[dict[str, float | str]],
    label: dict[str, float | str],
    page_bbox: tuple[float, float, float, float],
) -> list[dict[str, float | str]]:
    """Choose the relation-bearing baseline immediately left of an equation number."""

    label_size = float(label["size"])
    label_center = (float(label["top"]) + float(label["bottom"])) / 2.0
    label_left = float(label["x0"])
    content_left = page_bbox[0] + max(36.0, (page_bbox[2] - page_bbox[0]) * 0.06)
    eligible = [
        word
        for word in words
        if content_left <= float(word["x0"])
        and float(word["x1"]) < label_left
        and 0.74 <= float(word["size"]) / label_size <= 1.26
        and abs(
            (float(word["top"]) + float(word["bottom"])) / 2.0
            - label_center
        )
        <= max(6.0, label_size * 0.52)
    ]
    groups: list[list[dict[str, float | str]]] = []
    for word in sorted(eligible, key=lambda item: (float(item["top"]), float(item["x0"]))):
        if not groups or abs(float(word["top"]) - float(groups[-1][0]["top"])) > 2.5:
            groups.append([word])
        else:
            groups[-1].append(word)
    candidates: list[list[dict[str, float | str]]] = []
    for group in groups:
        group.sort(key=lambda item: (float(item["x0"]), float(item["x1"])))
        text = " ".join(str(word["text"]) for word in group)
        if (
            len(group) >= 3
            and _RELATION_RE.search(text)
            and re.search(r"[A-Za-zͰ-Ͽ-]", text)
            and re.search(r"\d", text)
        ):
            candidates.append(group)
    if not candidates:
        return []
    return min(
        candidates,
        key=lambda group: (
            abs(
                median(
                    (float(word["top"]) + float(word["bottom"])) / 2.0
                    for word in group
                )
                - label_center
            ),
            label_left - max(float(word["x1"]) for word in group),
        ),
    )


def _formula_semantic_text(
    words: list[dict[str, float | str]],
    main_words: list[dict[str, float | str]],
    label: dict[str, float | str],
) -> tuple[str, str, list[dict[str, float | str]]]:
    """Attach only adjacent smaller raised/lowered tokens to baseline words."""

    main_size = median(float(word["size"]) for word in main_words)
    main_center = median(
        (float(word["top"]) + float(word["bottom"])) / 2.0
        for word in main_words
    )
    left = min(float(word["x0"]) for word in main_words)
    right = float(label["x0"])
    candidates = [
        word
        for word in words
        if word not in main_words
        and word is not label
        and left <= float(word["x0"]) < right
        and 0.50 <= float(word["size"]) / main_size <= 0.90
        and main_center - main_size <= (float(word["top"]) + float(word["bottom"])) / 2.0 <= main_center + main_size
    ]
    attachments: dict[int, list[tuple[str, dict[str, float | str]]]] = {}
    attached: list[dict[str, float | str]] = []
    for script in sorted(candidates, key=lambda item: (float(item["x0"]), float(item["top"]))):
        script_center = (float(script["top"]) + float(script["bottom"])) / 2.0
        if script_center >= main_center + main_size * 0.18:
            kind = "sub"
        elif script_center <= main_center - main_size * 0.18:
            kind = "sup"
        else:
            continue
        possible = [
            (
                abs(float(script["x0"]) - float(base["x1"])),
                -index,
                index,
            )
            for index, base in enumerate(main_words)
            if -main_size * 0.20
            <= float(script["x0"]) - float(base["x1"])
            <= main_size * 0.60
        ]
        if not possible:
            continue
        _distance, _reverse_index, base_index = min(possible)
        attachments.setdefault(base_index, []).append((kind, script))
        attached.append(script)

    semantic_parts: list[str] = []
    for index, base in enumerate(main_words):
        text = readable_symbol_font_glyphs(str(base["text"]))
        for kind, script in sorted(
            attachments.get(index, []),
            key=lambda item: float(item[1]["x0"]),
        ):
            marker = "_" if kind == "sub" else "^"
            text += f"{marker}{{{readable_symbol_font_glyphs(str(script['text']))}}}"
        semantic_parts.append(text)
    source_words = sorted([*main_words, *attached], key=lambda item: (float(item["x0"]), float(item["top"])))
    source_text = " ".join(str(word["text"]) for word in source_words)
    return " ".join(semantic_parts), source_text, attached


def _looks_like_display_formula(value: str) -> bool:
    """Reject prose references that merely end with an equation number."""

    text = readable_symbol_font_glyphs(value)
    return bool(
        _RELATION_RE.search(text)
        and re.search(r"\d", text)
        and re.search(r"[A-Za-zͰ-Ͽ]", text)
    )


def _words_bbox(
    words: list[dict[str, float | str]],
) -> tuple[float, float, float, float]:
    """Return an enclosing PDF-point rectangle for the formula evidence."""

    return (
        min(float(word["x0"]) for word in words),
        min(float(word["top"]) for word in words),
        max(float(word["x1"]) for word in words),
        max(float(word["bottom"]) for word in words),
    )


def _bbox_center_is_inside(
    bbox: tuple[float, float, float, float],
    container: tuple[float, float, float, float],
) -> bool:
    """Return whether the candidate center falls in an accepted table crop."""

    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    return container[0] <= center_x <= container[2] and container[1] <= center_y <= container[3]


def _formula_image(
    page: object,
    bbox: tuple[float, float, float, float],
) -> tuple[object | None, str]:
    """Render a clean source crop without detector rectangles or OCR overlays."""

    page_left, page_top, page_right, page_bottom = _page_bbox(page)
    crop_bbox = (
        max(page_left, bbox[0] - _FORMULA_PADDING_X),
        max(page_top, bbox[1] - _FORMULA_PADDING_Y),
        min(page_right, bbox[2] + _FORMULA_PADDING_X),
        min(page_bottom, bbox[3] + _FORMULA_PADDING_Y),
    )
    stream = getattr(getattr(page, "pdf", None), "stream", None)
    page_index = int(getattr(page, "page_number", 0) or 0) - 1
    if stream is None or page_index < 0:
        return None, "页面缺少可重放的 PDF 快照流"
    try:
        import pypdfium2
    except ModuleNotFoundError:
        return None, "pypdfium2 未安装"

    # pdfplumber ``CroppedPage.to_image`` 会为每个小公式保留整页渲染缓存，
    # 双文档比较时会把常驻内存推高到进程限制。直接用项目已锁定的
    # PDFium 对 clip 点阵化，并显式关闭 bitmap/page/document，只保留最终小图。
    original_position: int | None = None
    document = None
    pdfium_page = None
    bitmap = None
    try:
        if hasattr(stream, "tell"):
            original_position = int(stream.tell())
        document = pypdfium2.PdfDocument(stream)
        pdfium_page = document[page_index]
        crop = (
            crop_bbox[0] - page_left,
            page_bottom - crop_bbox[3],
            page_right - crop_bbox[2],
            crop_bbox[1] - page_top,
        )  # PDFium 的 crop 顺序是左、下、右、上；pdfplumber bbox 从顶部计 y。
        bitmap = pdfium_page.render(
            scale=_FORMULA_SCREENSHOT_RESOLUTION / 72.0,
            crop=crop,
            limit_image_cache=True,
        )
        image = bitmap.to_pil().convert("RGB").copy()
    except Exception as exc:
        return None, str(exc)
    finally:
        if bitmap is not None:
            bitmap.close()
        if pdfium_page is not None:
            pdfium_page.close()
        if document is not None:
            document.close()
        if original_position is not None:
            try:
                stream.seek(original_position)
            except Exception:
                pass  # 渲染已结束；后续 pdfplumber 读取自己会定位，不伪造截图失败。
    return image, ""


def _image_to_data_uri(image: object) -> str:
    """Encode a formula crop as a compact, offline JPEG data URI."""

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _difference_hash(image: object) -> str:
    """Return a deterministic 64-bit difference hash for visual review pairing."""

    grayscale = image.convert("L").resize((9, 8))
    flattened = getattr(grayscale, "get_flattened_data", None)
    pixels = list(flattened() if flattened is not None else grayscale.getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


def normalized_formula_key(value: str) -> str:
    """Build a typography-tolerant key while retaining every formula character."""

    decoded = readable_symbol_font_glyphs(value)
    decoded = decoded.translate(str.maketrans({"−": "-", "–": "-", "—": "-"}))
    return "".join(character for character in decoded if not character.isspace())


def formula_character_multiset(value: str) -> Counter[str]:
    """Expose a lossless test/debug signature without treating it as equality."""

    return Counter(character for character in value if not character.isspace())
