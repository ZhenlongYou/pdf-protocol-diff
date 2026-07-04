"""Small built-in sample PDFs for validation and first-run demos.

The PDF writer is deliberately minimal and ASCII-only. It avoids adding a heavy
PDF-generation dependency just for tests. Real user input is still expected to
be normal protocol PDFs supplied through ``main.py`` parameters or CLI flags.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path


# Codex说明(自动生成)： 计算并保存 OLD_SAMPLE_LINES，供后续语句继续读取或更新。
OLD_SAMPLE_LINES = [
    "1 Scope",
    "This agreement applies to prototype devices only.",
    "1.1 Delivery",
    "Supplier shall deliver samples within 20 working days.",
    "2 Technical Requirements",
    "The operating voltage range is 3.0 V to 3.6 V.",
    "The product shall pass the basic reliability test.",
    "3 Acceptance",
    "Buyer shall complete acceptance within 5 working days.",
]

# Codex说明(自动生成)： 计算并保存 NEW_SAMPLE_LINES，供后续语句继续读取或更新。
NEW_SAMPLE_LINES = [
    "1 Scope",
    "This agreement applies to prototype and pilot-run devices.",
    "1.1 Delivery",
    "Supplier shall deliver samples within 15 working days.",
    "Supplier shall provide a delivery risk notice for delays over 2 days.",
    "2 Technical Requirements",
    "The operating voltage range is 2.8 V to 3.6 V.",
    "The product shall pass the enhanced reliability test.",
    "2.1 Security",
    "All debug ports shall be disabled before shipment.",
    "3 Acceptance",
    "Buyer shall complete acceptance within 7 working days.",
]


# Codex说明(自动生成)： 定义函数 write_demo_pdfs，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def write_demo_pdfs(target_dir: str | Path) -> tuple[Path, Path]:
    """Create old/new demo PDFs and return their paths."""

    # Codex说明(自动生成)： 计算并保存 directory，供后续语句继续读取或更新。
    directory = Path(target_dir).expanduser().resolve()
    # Codex说明(自动生成)： 调用 directory.mkdir，执行当前流程需要的具体操作或副作用。
    directory.mkdir(parents=True, exist_ok=True)
    # Codex说明(自动生成)： 计算并保存 old_path，供后续语句继续读取或更新。
    old_path = directory / "old_protocol_demo.pdf"
    # Codex说明(自动生成)： 计算并保存 new_path，供后续语句继续读取或更新。
    new_path = directory / "new_protocol_demo.pdf"
    # Codex说明(自动生成)： 调用 write_simple_text_pdf，执行当前流程需要的具体操作或副作用。
    write_simple_text_pdf(old_path, OLD_SAMPLE_LINES)
    # Codex说明(自动生成)： 调用 write_simple_text_pdf，执行当前流程需要的具体操作或副作用。
    write_simple_text_pdf(new_path, NEW_SAMPLE_LINES)
    # Codex说明(自动生成)： 返回 (old_path, new_path)，让调用方取得本函数的处理结果。
    return old_path, new_path


# Codex说明(自动生成)： 定义函数 write_simple_text_pdf，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def write_simple_text_pdf(path: str | Path, lines: list[str]) -> Path:
    """Write a simple one-page text PDF.

    The generated file is sufficient for pypdf extraction tests. It is not meant
    to be a general PDF authoring tool and intentionally supports only ASCII
    text, Helvetica, and a single page.
    """

    # Codex说明(自动生成)： 计算并保存 output_path，供后续语句继续读取或更新。
    output_path = Path(path).expanduser().resolve()
    # Codex说明(自动生成)： 调用 output_path.parent.mkdir，执行当前流程需要的具体操作或副作用。
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Codex说明(自动生成)： 计算并保存 escaped_lines，供后续语句继续读取或更新。
    escaped_lines = [_pdf_escape(line) for line in lines]
    # Codex说明(自动生成)： 计算并保存 text_commands，供后续语句继续读取或更新。
    text_commands = ["BT", "/F1 12 Tf", "72 740 Td", "16 TL"]
    # Codex说明(自动生成)： 遍历 enumerate(escaped_lines) 中的 (index, line)，逐项执行循环体逻辑。
    for index, line in enumerate(escaped_lines):
        # Codex说明(自动生成)： 检查条件 index，根据结果选择后续执行路径。
        if index:
            # Codex说明(自动生成)： 调用 text_commands.append 更新列表或集合，把当前步骤产生的数据加入结果。
            text_commands.append("T*")
        # Codex说明(自动生成)： 调用 text_commands.append 更新列表或集合，把当前步骤产生的数据加入结果。
        text_commands.append(f"({line}) Tj")
    # Codex说明(自动生成)： 调用 text_commands.append 更新列表或集合，把当前步骤产生的数据加入结果。
    text_commands.append("ET")
    # Codex说明(自动生成)： 计算并保存 stream，供后续语句继续读取或更新。
    stream = "\n".join(text_commands).encode("latin-1")

    # Codex说明(自动生成)： 声明并保存 objects，同时保留类型信息方便维护和静态检查。
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    # Codex说明(自动生成)： 计算并保存 pdf，供后续语句继续读取或更新。
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    # Codex说明(自动生成)： 计算并保存 offsets，供后续语句继续读取或更新。
    offsets = [0]
    # Codex说明(自动生成)： 遍历 enumerate(objects, start=1) 中的 (object_number, payload)，逐项执行循环体逻辑。
    for object_number, payload in enumerate(objects, start=1):
        # Codex说明(自动生成)： 调用 offsets.append 更新列表或集合，把当前步骤产生的数据加入结果。
        offsets.append(len(pdf))
        # Codex说明(自动生成)： 基于旧值更新 pdf，累积当前循环或处理步骤的结果。
        pdf += f"{object_number} 0 obj\n".encode("ascii")
        # Codex说明(自动生成)： 基于旧值更新 pdf，累积当前循环或处理步骤的结果。
        pdf += payload + b"\nendobj\n"

    # Codex说明(自动生成)： 计算并保存 xref_offset，供后续语句继续读取或更新。
    xref_offset = len(pdf)
    # Codex说明(自动生成)： 基于旧值更新 pdf，累积当前循环或处理步骤的结果。
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    # Codex说明(自动生成)： 基于旧值更新 pdf，累积当前循环或处理步骤的结果。
    pdf += b"0000000000 65535 f \n"
    # Codex说明(自动生成)： 遍历 offsets[1:] 中的 offset，逐项执行循环体逻辑。
    for offset in offsets[1:]:
        # Codex说明(自动生成)： 基于旧值更新 pdf，累积当前循环或处理步骤的结果。
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    # Codex说明(自动生成)： 基于旧值更新 pdf，累积当前循环或处理步骤的结果。
    pdf += (
        b"trailer\n"
        + f"<< /Root 1 0 R /Size {len(objects) + 1} >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    # Codex说明(自动生成)： 调用 output_path.write_bytes 写出文件或数据，保存当前处理结果。
    output_path.write_bytes(pdf)
    # Codex说明(自动生成)： 返回 output_path，让调用方取得本函数的处理结果。
    return output_path


# Codex说明(自动生成)： 定义函数 _pdf_escape，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _pdf_escape(value: str) -> str:
    """Escape text for a literal PDF string."""

    # Codex说明(自动生成)： 计算并保存 ascii_value，供后续语句继续读取或更新。
    ascii_value = value.encode("latin-1", errors="replace").decode("latin-1")
    # Codex说明(自动生成)： 返回 ascii_value.replace('\\', '\\\\').replace('(', '\\(').r...，让调用方取得本函数的处理结果。
    return ascii_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
