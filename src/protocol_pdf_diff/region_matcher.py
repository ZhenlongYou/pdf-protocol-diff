"""Match layout-aware PDF regions and produce reportable changes."""

from __future__ import annotations  # 让类型注解不强制提前求值，减少导入副作用。

import base64  # 把区域截图编码成 HTML 可内嵌的 data URI。
import difflib  # 使用稳定的序列相似度匹配旧/新区块。
import io  # 在内存中转换 PNG，避免产生临时文件。
from dataclasses import dataclass  # 用轻量 delta 保存匹配结果，截图渲染延后到截断之后。
from pathlib import Path  # 统一处理 PDF 路径。

from .layout_extractor import extract_layout_regions  # 抽取 typed layout regions。
from .models import DiffOptions, LayoutRegion, RegionChange  # 使用统一比较配置和报告模型。
from .text_utils import compact_inline, normalize_for_similarity  # 复用文本规整口径。

_VISUAL_REGION_TYPES = {"table", "formula", "figure"}  # 这些区域需要截图辅助复核。
_MAX_LAYOUT_CHANGES = 120  # 防止复杂 PDF 生成过大的 HTML 报告。


@dataclass(frozen=True)
class _RegionDelta:
    """One matched layout-region delta before expensive screenshot rendering."""

    change_type: str  # added、deleted 或 modified。
    old_region: LayoutRegion | None  # 删除或修改时的旧区域。
    new_region: LayoutRegion | None  # 新增或修改时的新区域。
    similarity: float  # 区域匹配相似度。


def run_layout_diff(
    old_pdf: str | Path,
    new_pdf: str | Path,
    options: DiffOptions,
) -> tuple[list[RegionChange], list[str]]:
    """Run layout-aware region extraction and matching."""

    old_layout = extract_layout_regions(old_pdf, options.old_start_page, options.old_end_page)  # 抽取旧 PDF 区域。
    new_layout = extract_layout_regions(new_pdf, options.new_start_page, options.new_end_page)  # 抽取新 PDF 区域。
    warnings = [*old_layout.warnings, *new_layout.warnings]  # 合并非致命抽取警告。
    old_visual_regions = [region for region in old_layout.regions if region.region_type in _VISUAL_REGION_TYPES]  # 正文/标题继续由章节 diff 负责，layout 只报告视觉区域。
    new_visual_regions = [region for region in new_layout.regions if region.region_type in _VISUAL_REGION_TYPES]  # 视觉区域包括表格、公式和图片。
    deltas = _match_regions(old_visual_regions, new_visual_regions, options)  # 先只匹配区域，不渲染截图。
    if len(deltas) > _MAX_LAYOUT_CHANGES:  # 过多 layout 变化会让 HTML 难以打开。
        warnings.append(f"layout-aware 区域差异较多，仅展示前 {_MAX_LAYOUT_CHANGES} 条；正文差异仍完整参与章节比较。")  # 明确说明截断范围。
        deltas = deltas[:_MAX_LAYOUT_CHANGES]  # 截断后再渲染截图，避免 noisy PDF 上提前消耗大量时间和内存。
    changes = [
        _region_change(delta.change_type, delta.old_region, delta.new_region, delta.similarity, Path(old_pdf), Path(new_pdf))
        for delta in deltas
    ]  # 只为最终展示的区域生成截图和热图。
    return changes, warnings  # 返回区域变化和警告。


def _match_regions(
    old_regions: list[LayoutRegion],
    new_regions: list[LayoutRegion],
    options: DiffOptions,
) -> list[_RegionDelta]:
    """Pair old/new regions by type, context, and content similarity."""

    matched_old: set[int] = set()  # 保存已经配对的旧区域索引。
    deltas: list[_RegionDelta] = []  # 保存轻量匹配结果，暂不渲染截图。
    for new_index, new_region in enumerate(new_regions):  # 按新版阅读顺序处理区域。
        old_index, similarity = _best_old_match(new_region, old_regions, matched_old)  # 寻找最相近旧区域。
        if old_index is None or similarity < _match_threshold(new_region.region_type):  # 找不到可靠配对就是新增。
            deltas.append(_RegionDelta("added", None, new_region, 0.0))  # 记录新增区域。
            continue  # 处理下一个新区域。
        matched_old.add(old_index)  # 标记旧区域已被使用。
        old_region = old_regions[old_index]  # 取出匹配旧区域。
        if similarity < options.unchanged_similarity:  # 低于未变化阈值才展示修改。
            deltas.append(_RegionDelta("modified", old_region, new_region, similarity))  # 记录修改区域。

    for old_index, old_region in enumerate(old_regions):  # 扫描未匹配旧区域。
        if old_index in matched_old:  # 已匹配区域不再处理。
            continue  # 跳过。
        deltas.append(_RegionDelta("deleted", old_region, None, 0.0))  # 记录删除区域。
    return sorted(deltas, key=_region_delta_sort_key)  # 按文档阅读顺序输出。


def _best_old_match(
    new_region: LayoutRegion,
    old_regions: list[LayoutRegion],
    matched_old: set[int],
) -> tuple[int | None, float]:
    """Return the best unmatched old region for one new region."""

    best_index: int | None = None  # 当前最佳旧区域索引。
    best_score = 0.0  # 当前最高相似度。
    for old_index, old_region in enumerate(old_regions):  # 遍历所有旧区域候选。
        if old_index in matched_old:  # 已经配对过的旧区域不能重复使用。
            continue  # 跳过。
        if old_region.region_type != new_region.region_type:  # 不同类型不能互相配对。
            continue  # 跳过。
        score = _region_similarity(old_region, new_region)  # 计算综合相似度。
        if score > best_score:  # 找到更好候选。
            best_score = score  # 更新最高分。
            best_index = old_index  # 更新最佳索引。
    return best_index, best_score  # 返回最佳候选。


def _region_similarity(old_region: LayoutRegion, new_region: LayoutRegion) -> float:
    """Return a blended similarity score for two regions of the same type."""

    old_text = normalize_for_similarity(old_region.text or old_region.section_context)  # 旧区域文本归一化。
    new_text = normalize_for_similarity(new_region.text or new_region.section_context)  # 新区域文本归一化。
    text_score = _sequence_ratio(old_text, new_text) if old_text or new_text else 0.0  # 文本相似度是主要身份信号。
    context_score = _sequence_ratio(  # 最近标题上下文辅助处理跨页漂移。
        normalize_for_similarity(old_region.section_context),
        normalize_for_similarity(new_region.section_context),
    )
    order_score = _relative_order_score(old_region, new_region)  # 页码和阅读顺序接近程度。
    if old_region.region_type in _VISUAL_REGION_TYPES:  # 视觉对象文本可能很少。
        return text_score * 0.62 + context_score * 0.20 + order_score * 0.18  # 视觉区域更依赖上下文和位置。
    return text_score * 0.82 + context_score * 0.10 + order_score * 0.08  # 正文区域主要依赖文本。


def _sequence_ratio(old_text: str, new_text: str) -> float:
    """Return difflib similarity for two normalized strings."""

    if not old_text and not new_text:  # 双空文本视作相同。
        return 1.0  # 返回完全相同。
    return difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).ratio()  # 关闭 autojunk 避免协议重复词被忽略。


def _relative_order_score(old_region: LayoutRegion, new_region: LayoutRegion) -> float:
    """Return a loose score for page/order proximity."""

    page_gap = abs(old_region.page_number - new_region.page_number)  # 计算页码差。
    order_gap = abs(old_region.order_index - new_region.order_index)  # 计算阅读顺序差。
    page_score = max(0.0, 1.0 - page_gap / 8.0)  # 允许插页导致的几页漂移。
    order_score = max(0.0, 1.0 - order_gap / 80.0)  # 允许段落插入导致的顺序漂移。
    return page_score * 0.55 + order_score * 0.45  # 合成位置分。


def _match_threshold(region_type: str) -> float:
    """Return minimum score required to pair two regions."""

    if region_type in _VISUAL_REGION_TYPES:  # 表格/公式可能只有短 caption。
        return 0.48  # 视觉区域阈值稍低，避免插页后误报新增/删除。
    if region_type == "heading":  # 标题较短，需要更高阈值。
        return 0.70  # 标题配对阈值。
    return 0.62  # 正文段落默认阈值。


def _region_change(
    change_type: str,
    old_region: LayoutRegion | None,
    new_region: LayoutRegion | None,
    similarity: float,
    old_pdf: Path,
    new_pdf: Path,
) -> RegionChange:
    """Build a RegionChange and attach screenshots for visual regions."""

    region = new_region or old_region  # 取存在的一侧判断类型。
    region_type = region.region_type if region else "paragraph"  # 理论上总有一侧区域。
    if region_type not in _VISUAL_REGION_TYPES:  # 正文和标题不需要截图。
        return RegionChange(change_type, region_type, old_region, new_region, similarity)  # 直接返回文本区域变化。
    old_uri, old_image = _render_region_image(old_pdf, old_region)  # 渲染旧区域截图。
    new_uri, new_image = _render_region_image(new_pdf, new_region)  # 渲染新区域截图。
    diff_uri = _diff_image_uri(old_image, new_image) if old_image is not None and new_image is not None else ""  # 双方都有截图才生成热图。
    return RegionChange(  # 返回带截图的视觉区域变化。
        change_type=change_type,  # 新增、删除或修改。
        region_type=region_type,  # 区域类型。
        old_region=old_region,  # 旧区域。
        new_region=new_region,  # 新区域。
        similarity=similarity,  # 综合相似度。
        old_image_data_uri=old_uri,  # 旧截图 data URI。
        new_image_data_uri=new_uri,  # 新截图 data URI。
        diff_image_data_uri=diff_uri,  # 差异热图 data URI。
    )


def _render_region_image(
    pdf_path: Path,
    region: LayoutRegion | None,
) -> tuple[str, object | None]:
    """Render one PDF region to a PNG data URI and a PIL image."""

    if region is None:  # 新增或删除只有一侧区域。
        return "", None  # 没有截图。
    try:  # PyMuPDF 和 Pillow 都是可选运行依赖，失败时不阻断文字报告。
        import fitz  # type: ignore[import-not-found]  # 用于渲染 PDF 区域。
        from PIL import Image  # type: ignore[import-not-found]  # 用于生成差异热图。
    except ModuleNotFoundError:  # 缺依赖时返回空截图。
        return "", None  # 上层仍能展示文本元数据。
    document = fitz.open(str(pdf_path))  # 打开对应 PDF。
    try:  # 确保文档句柄关闭。
        page = document.load_page(region.page_number - 1)  # 定位源页。
        clip = _clip_rect(fitz, page, region.bbox, padding=6.0)  # 构造裁剪矩形。
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), clip=clip, alpha=False)  # 渲染高清截图。
        image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")  # 转成 PIL RGB 图。
    except Exception:  # 单个区域截图失败不能影响报告生成。
        return "", None  # 返回空截图。
    finally:
        document.close()  # 关闭 PDF。
    return _image_to_data_uri(image), image  # 返回 HTML data URI 和 PIL 对象。


def _clip_rect(
    fitz_module: object,
    page: object,
    bbox: tuple[float, float, float, float],
    padding: float,
) -> object:
    """Return a page-clamped PyMuPDF Rect for a region bbox."""

    page_rect = page.rect  # 获取页面矩形。
    left = max(float(page_rect.x0), bbox[0] - padding)  # 左边不越界。
    top = max(float(page_rect.y0), bbox[1] - padding)  # 上边不越界。
    right = min(float(page_rect.x1), bbox[2] + padding)  # 右边不越界。
    bottom = min(float(page_rect.y1), bbox[3] + padding)  # 下边不越界。
    return fitz_module.Rect(left, top, right, bottom)  # 构造 PyMuPDF 裁剪矩形。


def _diff_image_uri(old_image: object, new_image: object) -> str:
    """Return a PNG data URI showing visual pixel differences."""

    try:  # Pillow 差异生成可能因异常图像尺寸失败。
        from PIL import ImageChops, ImageOps  # type: ignore[import-not-found]  # ImageChops 计算像素差。
        old_rgb = old_image.convert("RGB")  # 旧图统一 RGB。
        new_rgb = new_image.convert("RGB").resize(old_rgb.size)  # 新图缩放到旧图尺寸。
        diff = ImageChops.difference(old_rgb, new_rgb)  # 计算绝对像素差。
        diff = ImageOps.autocontrast(diff)  # 自动拉伸差异，方便肉眼查看。
        return _image_to_data_uri(diff)  # 返回热图 data URI。
    except Exception:  # 差异图失败时保留旧/新截图即可。
        return ""  # 返回空热图。


def _image_to_data_uri(image: object) -> str:
    """Encode a PIL image as a PNG data URI."""

    buffer = io.BytesIO()  # 创建内存缓冲区。
    image.save(buffer, format="PNG", optimize=True)  # 保存为 PNG，避免 JPEG 模糊表格线。
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")  # base64 编码为 ASCII。
    return f"data:image/png;base64,{payload}"  # 生成 HTML 可直接使用的 data URI。


def _region_delta_sort_key(delta: _RegionDelta) -> tuple[int, float, float, int]:
    """Sort lightweight region deltas before screenshot rendering."""

    region = delta.new_region or delta.old_region  # 新增/修改优先用新版位置，删除用旧版位置。
    if region is None:  # 防御性处理空变化。
        return (0, 0.0, 0.0, 0)  # 放到最前。
    change_rank = {"modified": 0, "added": 1, "deleted": 2}.get(delta.change_type, 9)  # 同位置下修改优先。
    return (region.page_number, region.bbox[1], region.bbox[0], change_rank)  # 按页码和坐标排序。


def _region_change_sort_key(change: RegionChange) -> tuple[int, float, float, int]:
    """Sort region changes by the surviving page location."""

    region = change.new_region or change.old_region  # 新增/修改优先用新版位置，删除用旧版位置。
    if region is None:  # 防御性处理空变化。
        return (0, 0.0, 0.0, 0)  # 放到最前。
    change_rank = {"modified": 0, "added": 1, "deleted": 2}.get(change.change_type, 9)  # 同位置下修改优先。
    return (region.page_number, region.bbox[1], region.bbox[0], change_rank)  # 按页码和坐标排序。
