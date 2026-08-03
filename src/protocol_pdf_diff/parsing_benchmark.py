"""提供只评估 PDF 抽取结果的隐私安全解析基准。

公开 runner 读取 JSON manifest、调用现有 ``extract_pdf_text``，再用同一份
extraction 做 self-compare 取得质量状态。模块不会写报告，也不会在 summary
中保留全文、case ID、文件名/路径、输入哈希或临时文件位置。
"""

from __future__ import annotations

import json  # manifest 与 summary 都使用标准 JSON 数据结构，便于 CI 消费。
from pathlib import Path, PureWindowsPath  # 同时识别当前系统与 Windows 绝对路径。
import shutil  # 受控 OCR fixture 只在真实 Tesseract 引擎可用时生成并设为 required。
from tempfile import TemporaryDirectory  # 受控 PDF/manifest 运行结束后必须自动清理。
from time import perf_counter  # 记录每个抽取 case 的端到端耗时秒数。
from typing import Any  # manifest 来自不可信 JSON，需要逐层收窄动态类型。

from .compare import compare_extractions  # self-compare 只用于取得现有可靠性判定。
from .models import DiffOptions  # OCR 语言通过公开选项传入质量/provenance 链路。
from .pdf_extract import extract_pdf_text  # 基准必须调用生产公开抽取入口。


_STATES = frozenset({"reliable", "degraded", "indeterminate"})  # 与 quality.py 的公开状态保持一致。
_TOP_LEVEL_KEYS = frozenset({"schema_version", "cases"})  # 顶层未知字段通常意味着拼写错误，必须拒绝。
_CASE_KEYS = frozenset(
    {"id", "required", "description", "document", "ocr_language", "expect"}
)  # case 只描述单份 PDF 抽取，不接受 pair/report 选项。
_DOCUMENT_KEYS = frozenset({"path", "start_page", "end_page"})  # 页窗沿用公开 extractor 的 1-based inclusive 语义。
_EXPECT_KEYS = frozenset(
    {"state", "states", "must_extract", "must_not_extract", "ordered_extract", "ocr_used"}
)  # expectations 只覆盖抽取事实与现有质量状态。


def validate_parsing_benchmark_manifest(manifest: object) -> list[str]:
    """返回 parsing benchmark manifest 中发现的全部 schema 错误。

    Args:
        manifest: ``json.loads`` 产生的任意 Python 对象。

    Returns:
        按遍历顺序排列的错误文本；合法 manifest 返回空列表。错误文本不会
        回显文档绝对路径。

    Side effects:
        无；本函数不读取 PDF，也不修改传入对象。
    """

    # 非 object 顶层无法安全枚举字段，因此返回唯一可确定的形状错误。
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    failures: list[str] = []
    # 严格拒绝未知顶层字段，避免 ``casez`` 等拼写错误被静默忽略。
    unknown_top_level = sorted(set(manifest) - _TOP_LEVEL_KEYS)
    if unknown_top_level:
        failures.append("unsupported top-level keys: " + ", ".join(unknown_top_level))
    # JSON bool 在 Python 中是 int 子类，必须单独排除 True 冒充版本 1。
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        failures.append("schema_version must be exactly 1")
    # cases 不是数组时无法继续逐项校验，但仍保留前面已发现的错误。
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        failures.append("cases must be a JSON array")
        return failures
    # 空数组不能证明解析器行为，因此作为 schema 错误拒绝。
    if not cases:
        failures.append("cases must contain at least one case")
    # ID 在同一 manifest 内唯一，确保 summary 可稳定关联具体 case。
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(case, dict):
            failures.append(f"{location} must be a JSON object")
            continue
        # case 层未知字段同样视为潜在拼写错误。
        unknown_case_keys = sorted(set(case) - _CASE_KEYS)
        if unknown_case_keys:
            failures.append(
                f"{location} has unsupported keys: {', '.join(unknown_case_keys)}"
            )
        # 空白 ID 不能充当机器可读主键。
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            failures.append(f"{location}.id must be a non-empty string")
        elif case_id in seen_ids:
            failures.append(f"{location}.id duplicates an earlier case id")
        else:
            seen_ids.add(case_id)
        # required 必须显式给出布尔值，避免字符串 ``false`` 被当成真值。
        required = case.get("required")
        if not isinstance(required, bool):
            failures.append(f"{location}.required must be a boolean")
        # description 可省略；存在时必须保持为人类可读字符串。
        description = case.get("description")
        if description is not None and not isinstance(description, str):
            failures.append(f"{location}.description must be a string")
        # OCR 语言可省略；空值或复合容器不能传给 Tesseract。
        ocr_language = case.get("ocr_language")
        if ocr_language is not None and (
            not isinstance(ocr_language, str) or not ocr_language.strip()
        ):
            failures.append(f"{location}.ocr_language must be a non-empty string")
        # document 与 expect 是执行基准所需的两个结构化子对象。
        _validate_document(case.get("document"), f"{location}.document", failures)
        _validate_expect(case.get("expect"), f"{location}.expect", failures)
    # 返回全部已发现错误；调用者决定是否将其包装成 manifest failure summary。
    return failures


def _validate_document(document: object, location: str, failures: list[str]) -> None:
    """把单个 document 描述的全部 schema 错误追加到共享列表。"""

    # 非 object 时没有可继续检查的安全字段。
    if not isinstance(document, dict):
        failures.append(f"{location} must be a JSON object")
        return
    # 只允许 path 与 inclusive 页窗，拒绝近似拼写。
    unknown_keys = sorted(set(document) - _DOCUMENT_KEYS)
    if unknown_keys:
        failures.append(f"{location} has unsupported keys: {', '.join(unknown_keys)}")
    # path 必须是相对 corpus root 的非空 PDF 路径。
    path_value = document.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        failures.append(f"{location}.path must be a non-empty safe relative path")
    else:
        # 同时检查 POSIX 和 Windows 路径语义，避免跨平台 manifest 绕过绝对路径门禁。
        native_path = Path(path_value)
        windows_path = PureWindowsPath(path_value)
        if (
            native_path.is_absolute()
            or windows_path.is_absolute()
            or ".." in native_path.parts
            or ".." in windows_path.parts
        ):
            failures.append(f"{location}.path must be a safe relative path")
        # 后缀比较大小写不敏感，但不回显潜在敏感输入路径。
        if native_path.suffix.casefold() != ".pdf":
            failures.append(f"{location}.path must name a PDF file")
    # 两端页码都使用用户可见的 1-based 正整数。
    for page_key in ("start_page", "end_page"):
        page = document.get(page_key)
        if page is not None and (
            not isinstance(page, int) or isinstance(page, bool) or page < 1
        ):
            failures.append(f"{location}.{page_key} must be a positive integer")
    # 仅当两端类型均有效时判断闭区间顺序，避免重复噪声错误。
    start_page = document.get("start_page")
    end_page = document.get("end_page")
    if (
        isinstance(start_page, int)
        and not isinstance(start_page, bool)
        and isinstance(end_page, int)
        and not isinstance(end_page, bool)
        and start_page > end_page
    ):
        failures.append(f"{location}.end_page must be >= start_page")


def _validate_expect(expect: object, location: str, failures: list[str]) -> None:
    """把质量、OCR 与 literal anchor expectations 的全部错误追加到列表。"""

    # expect 必须显式为 object；空 object 表示只要求抽取成功。
    if not isinstance(expect, dict):
        failures.append(f"{location} must be a JSON object")
        return
    # 未知 expectation 可能误以为受到基准保护，必须明确拒绝。
    unknown_keys = sorted(set(expect) - _EXPECT_KEYS)
    if unknown_keys:
        failures.append(f"{location} has unsupported keys: {', '.join(unknown_keys)}")
    # state 只允许 quality.py 已公开的三个值。
    state = expect.get("state")
    if state is not None and (not isinstance(state, str) or state not in _STATES):
        failures.append(
            f"{location}.state must be reliable, degraded, or indeterminate"
        )
    # states 必须非空、取值合法且无重复，才能表达清晰的环境性候选集合。
    states = expect.get("states")
    if states is not None and (
        not isinstance(states, list)
        or not states
        or any(not isinstance(value, str) or value not in _STATES for value in states)
        or len(set(states)) != len(states)
    ):
        failures.append(
            f"{location}.states must be a non-empty array of unique quality states"
        )
    # 单值与候选集合语义冲突，哪怕其中一个值本身无效也要同时报告。
    if state is not None and states is not None:
        failures.append(f"{location} cannot define both state and states")
    # 三种 anchor 数组都允许为空，但元素必须是非空 literal 字符串。
    for anchor_key in ("must_extract", "must_not_extract", "ordered_extract"):
        anchors = expect.get(anchor_key, [])
        if not isinstance(anchors, list) or any(
            not isinstance(anchor, str) or not anchor.strip() for anchor in anchors
        ):
            failures.append(
                f"{location}.{anchor_key} must be an array of non-empty strings"
            )
    # ocr_used 只有三态中的“缺省不检查”和显式 true/false，不接受真值字符串。
    ocr_used = expect.get("ocr_used")
    if ocr_used is not None and not isinstance(ocr_used, bool):
        failures.append(f"{location}.ocr_used must be a boolean")


def run_parsing_benchmark(
    manifest_path: str | Path,
    *,
    corpus_root: str | Path | None = None,
) -> dict[str, Any]:
    """运行 extraction-only manifest 并返回可 JSON 序列化的隐私安全 summary。

    Args:
        manifest_path: UTF-8 JSON manifest 的文件路径。
        corpus_root: 可选 PDF 根目录；省略时使用 manifest 所在目录。

    Returns:
        只包含不透明 ``case_index``、状态、有限失败原因、质量状态和聚合
        抽取指标的 dictionary；不会包含全文、原始 case ID、文件名、resolved
        corpus root、hash 或报告路径。本机按 manifest 中的输入顺序关联 case。

    Side effects:
        只读取 manifest/PDF，并执行现有解析器可能触发的内存 OCR；不生成
        持久报告或修改输入文件。
    """

    # manifest 路径只用于本地读取，发生错误时 summary 绝不能回显文件名。
    source = Path(manifest_path).expanduser().resolve()
    try:
        # UTF-8 明确约束保证不同 CI 主机读取同一份 manifest 得到相同字符。
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        # 异常原文常含 manifest 文件名或目录；只保留稳定类别而不回显详情。
        return _manifest_failure_summary([f"cannot read manifest: {type(exc).__name__}"])

    # schema 错误一次全部返回，避免维护者逐次修一个字段。
    failures = validate_parsing_benchmark_manifest(manifest)
    if failures:
        return _manifest_failure_summary(failures)

    # 显式 root 优先；默认使用 manifest 同目录，且绝不把 resolved 值写入输出。
    root = (
        Path(corpus_root).expanduser().resolve()
        if corpus_root is not None
        else source.parent
    )
    # 每个 case 都独立捕获错误，单份损坏 PDF 不应隐藏其余基准结果。
    case_results = [
        _run_case_safely(case, root, case_index=index)
        for index, case in enumerate(manifest["cases"], start=1)
    ]
    # 固定三个计数键，方便 CLI 和 CI 不依赖 case 是否出现某种状态。
    counts = {
        status: sum(result["status"] == status for result in case_results)
        for status in ("pass", "fail", "skip")
    }
    # 只要一个 case 失败整体即失败；全 skip 保持 skip，避免误报验证成功。
    status = "fail" if counts["fail"] else ("pass" if counts["pass"] else "skip")
    # 顶层只保留协议版本、聚合状态/计数和严格受限的 case summaries。
    return {
        "schema_version": 1,
        "status": status,
        "counts": counts,
        "cases": case_results,
    }


def run_controlled_parsing_benchmark() -> dict[str, Any]:
    """生成受控 PDF fixtures，经正常 manifest runner 运行后返回 summary。

    Returns:
        与 :func:`run_parsing_benchmark` 相同的 JSON 可序列化、隐私安全
        summary。若缺少 Tesseract，只有 English raster case 为 skip。

    Privacy:
        fixtures 与 manifest 全部位于临时目录；summary 不包含该目录、PDF
        全文、输入哈希或报告路径。

    Side effects:
        临时创建五份 PDF 和一份 manifest；上下文退出时无论成功失败均清理。
        Tesseract 可用时会真实执行一次 English OCR。
    """

    # 固定前缀便于人工排障残留，但上下文管理器保证正常/异常路径都清理。
    with TemporaryDirectory(prefix="pdf_parsing_benchmark_") as temp_dir:
        root = Path(temp_dir)
        # 原生线性文本保留数字、十进制与工程单位 literal。
        _write_positioned_text_pdf(
            root / "native-linear.pdf",
            [
                (72, 740, "1 Native Linear Extraction"),
                (72, 710, "Receiver voltage shall remain 3.3 V."),
                (72, 680, "Timing tolerance shall not exceed 12.5 ps."),
                (72, 650, "Unit literal 250 mV must remain available."),
            ],
        )
        # 两组纯正文使用不同 x 坐标形成 positioned two-column 页面；数值双栏不属于轻量重排支持范围。
        _write_positioned_text_pdf(
            root / "positioned-two-column.pdf",
            [
                (54, 740, "LEFT COLUMN BEGIN"),
                (330, 740, "RIGHT COLUMN BEGIN"),
                (54, 700, "Left prose anchor begins"),
                (330, 700, "Right prose anchor begins"),
                (54, 660, "LEFT COLUMN END"),
                (330, 660, "RIGHT COLUMN END"),
            ],
        )
        # 公式 fixture 使用 ASCII 运算符，避免字体替换掩盖解析器行为。
        _write_positioned_text_pdf(
            root / "formula.pdf",
            [
                (72, 740, "1 Formula Preservation"),
                (72, 700, "Vout = Vin * (1 + R2/R1)"),
                (72, 660, "BER <= 1e-12"),
            ],
        )
        # 表头和数据以坐标对齐但不画任何边框，构成 borderless table。
        _write_positioned_text_pdf(
            root / "borderless-table.pdf",
            [
                (54, 740, "Parameter"),
                (230, 740, "Minimum"),
                (340, 740, "Maximum"),
                (470, 740, "Units"),
                (54, 700, "Input Voltage"),
                (230, 700, "0.8"),
                (340, 700, "1.2"),
                (470, 700, "V"),
                (54, 660, "Timing Window"),
                (230, 660, "20"),
                (340, 660, "35"),
                (470, 660, "ps"),
            ],
        )
        # 引擎存在时才生成 raster 文件；否则 runner 按 optional missing 正常 skip。
        tesseract_available = shutil.which("tesseract") is not None
        if tesseract_available:
            _write_english_raster_pdf(root / "english-raster.pdf")

        # 所有 fixtures 都通过与真实 corpus 相同的严格 manifest 和 runner。
        manifest = _controlled_manifest(tesseract_available=tesseract_available)
        manifest_path = root / "controlled-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 在临时目录仍存在时完成读取；返回值不持有任何文件对象或本地路径。
        return run_parsing_benchmark(manifest_path, corpus_root=root)


def _controlled_manifest(*, tesseract_available: bool) -> dict[str, Any]:
    """构造五类受控 extraction cases，OCR required 性由真实引擎决定。"""

    # manifest 只使用公开 schema，避免 controlled 路径获得生产 corpus 没有的特权。
    return {
        "schema_version": 1,
        "cases": [
            {
                "id": "controlled-native-linear",
                "required": True,
                "description": "Native selectable text with numeric and unit literals.",
                "document": {"path": "native-linear.pdf"},
                "expect": {
                    "must_extract": ["3.3 V", "12.5 ps", "250 mV"],
                    "must_not_extract": ["PRIVATE"],
                    "ordered_extract": ["3.3 V", "12.5 ps", "250 mV"],
                    "ocr_used": False,
                },
            },
            {
                "id": "controlled-positioned-two-column",
                "required": True,
                "description": "Positioned pure-prose columns must preserve left anchors before right anchors.",
                "document": {"path": "positioned-two-column.pdf"},
                "expect": {
                    "must_extract": ["Left prose anchor", "Right prose anchor"],
                    "ordered_extract": [
                        "LEFT COLUMN BEGIN",
                        "Left prose anchor",
                        "LEFT COLUMN END",
                        "RIGHT COLUMN BEGIN",
                        "Right prose anchor",
                        "RIGHT COLUMN END",
                    ],
                    "ocr_used": False,
                },
            },
            {
                "id": "controlled-formula",
                "required": True,
                "description": "Formula and inequality operators remain literal.",
                "document": {"path": "formula.pdf"},
                "expect": {
                    "must_extract": [
                        "Vout = Vin * (1 + R2/R1)",
                        "BER <= 1e-12",
                    ],
                    "ordered_extract": [
                        "Vout = Vin * (1 + R2/R1)",
                        "BER <= 1e-12",
                    ],
                    "ocr_used": False,
                },
            },
            {
                "id": "controlled-borderless-table",
                "required": True,
                "description": "Coordinate-aligned table text without drawn borders.",
                "document": {"path": "borderless-table.pdf"},
                "expect": {
                    "must_extract": [
                        "Parameter",
                        "Minimum",
                        "Maximum",
                        "Units",
                        "Input Voltage",
                        "0.8",
                        "1.2",
                        "V",
                        "Timing Window",
                        "20",
                        "35",
                        "ps",
                    ],
                    "ordered_extract": [
                        "Parameter",
                        "Minimum",
                        "Maximum",
                        "Units",
                        "Input Voltage",
                        "0.8",
                        "1.2",
                        "V",
                        "Timing Window",
                        "20",
                        "35",
                        "ps",
                    ],
                    "ocr_used": False,
                },
            },
            {
                "id": "controlled-english-raster-ocr",
                "required": tesseract_available,
                "description": "Pillow bundled-font raster requiring real English OCR.",
                "document": {"path": "english-raster.pdf"},
                "ocr_language": "eng",
                "expect": {
                    "states": ["degraded", "indeterminate"],
                    "must_extract": [
                        "SCANNED RECEIVER",
                        "VOLTAGE LIMIT",
                        "3.3 V",
                        "TIMING WINDOW",
                        "25 PS",
                    ],
                    "ordered_extract": [
                        "SCANNED RECEIVER",
                        "VOLTAGE LIMIT",
                        "3.3 V",
                        "TIMING WINDOW",
                        "25 PS",
                    ],
                    "ocr_used": True,
                },
            },
        ],
    }

def _write_positioned_text_pdf(
    path: Path,
    text_items: list[tuple[int, int, str]],
) -> None:
    """写入一页 ASCII positioned-text PDF；输入坐标单位为 PDF point。

    输出仅供临时受控 fixtures 使用；函数会创建父目录并覆盖目标临时文件，
    不返回文本或路径到 benchmark summary。
    """

    # 每个文本项独立设置文本矩阵，确保 x/y 坐标能构成双栏和无框表格。
    commands = [
        f"BT /F1 14 Tf 1 0 0 1 {x} {y} Tm ({_escape_pdf_text(text)}) Tj ET"
        for x, y, text in text_items
    ]
    # Latin-1 覆盖 controlled fixtures 的全部 ASCII 字符，避免字体编码分支。
    stream = "\n".join(commands).encode("latin-1")
    # 最小对象图包含 catalog、pages、page、Helvetica font 和内容流。
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    # PDF header 包含二进制注释，防止某些读取器把文件误当纯文本。
    payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    # 顺序写入对象并记录 byte offsets，供 xref 精确定位。
    for object_number, object_payload in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{object_number} 0 obj\n".encode("ascii")
        payload += object_payload + b"\nendobj\n"
    # xref 起点是所有对象结束后的当前字节长度。
    xref_offset = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    payload += b"0000000000 65535 f \n"
    # 每个普通对象使用固定十位 offset，符合经典 PDF xref 格式。
    for offset in offsets[1:]:
        payload += f"{offset:010d} 00000 n \n".encode("ascii")
    # trailer 指回 catalog，并记录完整对象数。
    payload += (
        b"trailer\n"
        + f"<< /Root 1 0 R /Size {len(objects) + 1} >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    # 父目录和 PDF 都位于 TemporaryDirectory，写入不会污染仓库。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_english_raster_pdf(path: Path) -> None:
    """用 Pillow bundled font 写入一页纯 raster English PDF，供真实 OCR。"""

    # 延迟导入使没有 Pillow 的错误仅在 controlled OCR fixture 中清晰暴露。
    from PIL import Image, ImageDraw, ImageFont

    # 高对比大画布为 Tesseract 提供稳定字符，同时确保页面没有 selectable text。
    image = Image.new("RGB", (1600, 1100), "white")
    draw = ImageDraw.Draw(image)
    # load_default 使用 Pillow 随包字体，不依赖主机字体目录或下载资源。
    font = ImageFont.load_default(size=58)
    # 四行英文覆盖标题、数值、单位和终止标记，字符数高于 OCR 最低门槛。
    raster_text = (
        "SCANNED RECEIVER REQUIREMENT\n"
        "VOLTAGE LIMIT 3.3 V\n"
        "TIMING WINDOW 25 PS\n"
        "END OF OCR CONTROL SAMPLE"
    )
    draw.multiline_text(
        (110, 170),
        raster_text,
        fill="black",
        font=font,
        spacing=38,
    )
    # Pillow 直接把整页图像封装为 PDF，文件中不产生隐藏文字层。
    image.save(path, format="PDF", resolution=150.0)


def _escape_pdf_text(value: str) -> str:
    """转义 controlled ASCII 文本中的 PDF literal-string 保留字符。"""

    # 反斜杠必须最先转义，再处理圆括号，避免新增 escape 被重复处理。
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _run_case_safely(
    case: dict[str, Any],
    corpus_root: Path,
    *,
    case_index: int,
) -> dict[str, Any]:
    """执行一个 case，并把异常转成不含 corpus 绝对路径的有限失败 summary。"""

    try:
        # 正常执行结果已经受固定字段白名单约束，可直接返回顶层汇总。
        return _run_case(case, corpus_root, case_index=case_index)
    except Exception as exc:  # 损坏 PDF 或 OCR 失败必须成为 case 失败而非 CLI traceback。
        # PDF 库异常通常含源文件名；summary 只能保留稳定异常类别，排障回到本机
        # manifest 的同一 case_index，不把任何输入标识复制到持久 JSON。
        return _case_summary(
            case_index=case_index,
            status="fail",
            failures=[f"case execution error: {type(exc).__name__}"],
        )


def _run_case(
    case: dict[str, Any],
    corpus_root: Path,
    *,
    case_index: int,
) -> dict[str, Any]:
    """通过生产抽取器执行一个已验证 case，并计算受限指标。"""

    # 相对路径在 resolve 后还要检查 containment，抵御符号链接或 ``..`` 越界。
    document = case["document"]
    pdf_path = _corpus_pdf_path(corpus_root, document["path"])
    if not pdf_path.is_file():
        # required 决定缺失私有 corpus 是 CI 失败还是环境性 skip。
        required = bool(case.get("required", False))
        # 不能把私有相对文件名写进 stdout 或可归档 summary。
        failure = f"missing {'required' if required else 'optional'} PDF"
        return _case_summary(
            case_index=case_index,
            status="fail" if required else "skip",
            failures=[failure] if required else [],
        )

    # 计时覆盖完整抽取和可靠性评估，但不含 manifest 解析等固定开销。
    started = perf_counter()
    extraction = extract_pdf_text(
        pdf_path,
        start_page=document.get("start_page"),
        end_page=document.get("end_page"),
        ocr_language=case.get("ocr_language"),
    )
    # 按规格使用公开 self-compare 取得质量状态，不复制 quality.py 私有规则。
    comparison = compare_extractions(
        extraction,
        extraction,
        DiffOptions(ocr_language=case.get("ocr_language")),
    )
    # anchor 搜索使用原始抽取文本的 literal casefold 视图，不做语义归一化。
    searchable = "\n".join(page.text for page in extraction.pages).casefold()
    # OCR 页只记录 1-based 页码，不保留 OCR 全文或中间图像。
    ocr_pages = [page.page_number for page in extraction.pages if page.ocr_used]
    # 逐类追加期望失败，所有判断都基于同一次生产抽取结果。
    failures = _anchor_failures(case.get("expect", {}), searchable)
    # 质量状态断言与 anchors 一样只追加失败，不改变生产解析行为。
    failures.extend(_state_failures(case.get("expect", {}), comparison.assessment.state.value))
    # OCR 布尔事实表示所选页窗内是否至少一页真正走了 Tesseract。
    failures.extend(_ocr_failures(case.get("expect", {}), bool(ocr_pages)))
    # 字符数按抽取后逐页文本直接求和，换页连接符不计入正文指标。
    character_count = sum(len(page.text) for page in extraction.pages)
    # 单调时钟差值是浮点秒数，适合趋势门禁而不暴露文件系统元数据。
    elapsed_seconds = perf_counter() - started
    # 固定字段 summary 是本模块最重要的隐私边界。
    return _case_summary(
        case_index=case_index,
        status="fail" if failures else "pass",
        failures=failures,
        state=comparison.assessment.state.value,
        page_count=len(extraction.pages),
        ocr_pages=ocr_pages,
        warning_count=len(extraction.warnings),
        character_count=character_count,
        elapsed_seconds=elapsed_seconds,
    )


def _anchor_failures(expect: dict[str, Any], searchable: str) -> list[str]:
    """按 literal、大小写不敏感规则返回缺失、禁用和顺序锚点失败。"""

    failures: list[str] = []
    # must_extract 中每个锚点都必须至少出现一次。
    for index, anchor in enumerate(expect.get("must_extract", [])):
        if anchor.casefold() not in searchable:
            # 锚点可能是私有源文档片段；summary 仅保留断言类别和稳定索引。
            failures.append(f"must_extract anchor[{index}] absent")
    # must_not_extract 任一锚点出现都表示抽取器保留了应过滤内容。
    for index, anchor in enumerate(expect.get("must_not_extract", [])):
        if anchor.casefold() in searchable:
            # 失败信息不能反向把禁止匹配的私有文字写入 CI 或持久 JSON。
            failures.append(f"must_not_extract anchor[{index}] present")
    # ordered_extract 使用从左到右游标，要求各 literal 锚点单调出现。
    cursor = 0
    for index, anchor in enumerate(expect.get("ordered_extract", [])):
        position = searchable.find(anchor.casefold(), cursor)
        if position < 0:
            # 顺序异常同样只指向 manifest 中的稳定索引，避免文本泄露。
            failures.append(f"ordered_extract anchor[{index}] absent or out of order")
            break
        # 下一锚点从当前 literal 的下一个起始字符继续，要求起点严格递增
        # 但允许 ``AB`` 后匹配其尾部 ``B`` 这类重叠且位置合法的锚点。
        cursor = position + 1
    # 一次返回全部可独立判断的 anchor 错误，便于定位综合退化。
    return failures


def _state_failures(expect: dict[str, Any], actual_state: str) -> list[str]:
    """返回单一或候选可靠性状态断言的失败文本。"""

    failures: list[str] = []
    # state 表达严格唯一预期，适用于稳定原生文本 fixtures。
    expected_state = expect.get("state")
    if expected_state is not None and actual_state != expected_state:
        failures.append(f"state expected {expected_state!r}, got {actual_state!r}")
    # states 表达依赖 OCR 环境时允许的显式候选集合。
    expected_states = expect.get("states")
    if expected_states is not None and actual_state not in expected_states:
        failures.append(f"state expected one of {expected_states!r}, got {actual_state!r}")
    # 返回有限断言文本，不附带 quality reasons 中可能出现的源文件上下文。
    return failures


def _ocr_failures(expect: dict[str, Any], actual_ocr_used: bool) -> list[str]:
    """返回页窗内“是否实际 OCR”与 manifest 显式期望不一致的失败。"""

    # 缺省表示不对运行环境的 OCR 可用性做断言。
    expected_ocr_used = expect.get("ocr_used")
    if expected_ocr_used is None or expected_ocr_used == actual_ocr_used:
        return []
    # 只输出布尔事实，不输出 OCR 文本、图片或本地引擎路径。
    return [f"ocr_used expected {expected_ocr_used}, got {actual_ocr_used}"]


def _corpus_pdf_path(corpus_root: Path, relative_path: str) -> Path:
    """解析受 containment 保护的 corpus PDF 路径，失败信息不回显绝对路径。"""

    # root 与 candidate 都规范化后再比较，覆盖 ``..`` 和符号链接逃逸。
    root = corpus_root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("PDF path escapes <corpus-root>")
    # 调用者只拿路径做读取，summary 从不持久化该对象。
    return candidate


def _case_summary(
    *,
    case_index: int,
    status: str,
    failures: list[str],
    state: str | None = None,
    page_count: int = 0,
    ocr_pages: list[int] | None = None,
    warning_count: int = 0,
    character_count: int = 0,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    """构造唯一允许持久化的 case 字段集合，作为 summary 隐私白名单。"""

    # case_index 是输入数组 1-based 序号（0 仅代表 manifest 本身），不携带 ID
    # 或文件名语义。显式白名单避免未来内部对象被 ``asdict`` 意外写入 JSON。
    return {
        "case_index": case_index,
        "status": status,
        "failures": failures,
        "state": state,
        "page_count": page_count,
        "ocr_pages": list(ocr_pages or []),
        "warning_count": warning_count,
        "character_count": character_count,
        "elapsed_seconds": elapsed_seconds,
    }


def _manifest_failure_summary(failures: list[str]) -> dict[str, Any]:
    """把 manifest 读取/schema 错误转换成与普通 case 相同形状的 summary。"""

    # manifest 失败计作一个失败 case，使 CLI 退出码逻辑无需特殊分支。
    return {
        "schema_version": 1,
        "status": "fail",
        "counts": {"pass": 0, "fail": 1, "skip": 0},
        "cases": [
            _case_summary(
                case_index=0,
                status="fail",
                failures=failures,
            )
        ],
    }
