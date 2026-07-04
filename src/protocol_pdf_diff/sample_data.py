"""Small built-in sample PDFs for validation and first-run demos.

The PDF writer is deliberately minimal and ASCII-only. It avoids adding a heavy
PDF-generation dependency just for tests. Real user input is still expected to
be normal protocol PDFs supplied through ``main.py`` parameters or CLI flags.
"""

from __future__ import annotations

from pathlib import Path


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


def write_demo_pdfs(target_dir: str | Path) -> tuple[Path, Path]:
    """Create old/new demo PDFs and return their paths."""

    directory = Path(target_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    old_path = directory / "old_protocol_demo.pdf"
    new_path = directory / "new_protocol_demo.pdf"
    write_simple_text_pdf(old_path, OLD_SAMPLE_LINES)
    write_simple_text_pdf(new_path, NEW_SAMPLE_LINES)
    return old_path, new_path


def write_simple_text_pdf(path: str | Path, lines: list[str]) -> Path:
    """Write a simple one-page text PDF.

    The generated file is sufficient for pypdf extraction tests. It is not meant
    to be a general PDF authoring tool and intentionally supports only ASCII
    text, Helvetica, and a single page.
    """

    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    escaped_lines = [_pdf_escape(line) for line in lines]
    text_commands = ["BT", "/F1 12 Tf", "72 740 Td", "16 TL"]
    for index, line in enumerate(escaped_lines):
        if index:
            text_commands.append("T*")
        text_commands.append(f"({line}) Tj")
    text_commands.append("ET")
    stream = "\n".join(text_commands).encode("latin-1")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for object_number, payload in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{object_number} 0 obj\n".encode("ascii")
        pdf += payload + b"\nendobj\n"

    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        b"trailer\n"
        + f"<< /Root 1 0 R /Size {len(objects) + 1} >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    output_path.write_bytes(pdf)
    return output_path


def _pdf_escape(value: str) -> str:
    """Escape text for a literal PDF string."""

    ascii_value = value.encode("latin-1", errors="replace").decode("latin-1")
    return ascii_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
