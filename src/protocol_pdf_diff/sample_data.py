"""Built-in sample PDFs for validation and first-run demos.

The demo intentionally uses multiple pages, repeated headers/footers, page-count
drift, changed clauses, and an added section. That makes the first-run report
useful for judging the comparison behavior instead of only proving that the
program can open a PDF. The PDF writer itself stays minimal and ASCII-only so
tests do not need another authoring dependency.
"""

from __future__ import annotations

from pathlib import Path

OLD_DEMO_PAGES = [
    [
        "RINY Protocol Demo Agreement",
        "1 Scope",
        "This agreement applies to prototype devices only.",
        "Common safety clause applies to all devices.",
        "Reviewers shall verify every requirement against the source PDF.",
        "Scope decisions shall identify the applicable device class and revision.",
        "Confidential - Page 1 of 4",
    ],
    [
        "RINY Protocol Demo Agreement",
        "1.1 Delivery",
        "Supplier shall deliver samples within 20 working days.",
        "Supplier shall provide weekly status updates.",
        "Delivery evidence shall identify the responsible owner and due date.",
        "Confidential - Page 2 of 4",
    ],
    [
        "RINY Protocol Demo Agreement",
        "2 Technical Requirements",
        "The operating voltage range is 3.0 V to 3.6 V.",
        "The product shall pass the basic reliability test.",
        "Every electrical result shall retain units, limits, and test conditions.",
        "2.1 Security",
        "All debug ports may remain enabled for engineering samples.",
        "Confidential - Page 3 of 4",
    ],
    [
        "RINY Protocol Demo Agreement",
        "3 Acceptance",
        "Buyer shall complete acceptance within 5 working days.",
        "Acceptance records shall be archived by both parties.",
        "Reviewers shall record the source page for every accepted requirement.",
        "Confidential - Page 4 of 4",
    ],
]

NEW_DEMO_PAGES = [
    [
        "RINY Protocol Demo Agreement",
        "1 Scope",
        "This agreement applies to prototype and pilot-run devices.",
        "Common safety clause applies to all devices.",
        "Reviewers shall verify every requirement against the source PDF.",
        "Scope decisions shall identify the applicable device class and revision.",
        "Confidential - Page 1 of 5",
    ],
    [
        "RINY Protocol Demo Agreement",
        "1.1 Delivery",
        "Supplier shall deliver samples within 15 working days.",
        "Supplier shall provide weekly status updates.",
        "Supplier shall provide a delivery risk notice for delays over 2 days.",
        "Delivery evidence shall identify the responsible owner and due date.",
        "Confidential - Page 2 of 5",
    ],
    [
        "RINY Protocol Demo Agreement",
        "2 Technical Requirements",
        "The operating voltage range is 2.8 V to 3.6 V.",
        "The product shall pass the enhanced reliability test.",
        "Every electrical result shall retain units, limits, and test conditions.",
        "2.1 Security",
        "All debug ports shall be disabled before shipment.",
        "Confidential - Page 3 of 5",
    ],
    [
        "RINY Protocol Demo Agreement",
        "2.2 Documentation",
        "Supplier shall provide test logs before shipment.",
        "Supplier shall provide firmware version traceability.",
        "Documentation shall identify the source clause, revision, owner, and approval status.",
        "Confidential - Page 4 of 5",
    ],
    [
        "RINY Protocol Demo Agreement",
        "3 Acceptance",
        "Buyer shall complete acceptance within 7 working days and issue a signed acceptance record.",
        "Acceptance records shall include firmware traceability and issue owner sign-off.",
        "Reviewers shall record the source page for every accepted requirement.",
        "Confidential - Page 5 of 5",
    ],
]


def write_demo_pdfs(target_dir: str | Path) -> tuple[Path, Path]:
    """Create multi-page old/new demo PDFs and return their paths.

    The new demo has one extra page, so unchanged or modified later sections move
    to a different physical page. Repeated headers/footers exercise furniture
    filtering, and different decorative marks simulate visual-only changes that
    should not affect the text diff.
    """

    directory = Path(target_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    old_path = directory / "old_protocol_demo.pdf"
    new_path = directory / "new_protocol_demo.pdf"
    write_multipage_text_pdf(old_path, OLD_DEMO_PAGES, decorative_marks={3: "old"})
    write_multipage_text_pdf(new_path, NEW_DEMO_PAGES, decorative_marks={4: "new"})
    return old_path, new_path


def write_simple_text_pdf(path: str | Path, lines: list[str]) -> Path:
    """Write a simple one-page text PDF.

    The generated file is sufficient for pypdf extraction tests. It is not meant
    to be a general PDF authoring tool and intentionally supports only ASCII
    text, Helvetica, and a single page.
    """

    return write_multipage_text_pdf(path, [lines])


def write_multipage_text_pdf(
    path: str | Path,
    pages: list[list[str]],
    decorative_marks: dict[int, str] | None = None,
    footer_lines: dict[int, list[str]] | None = None,
    header_lines: dict[int, list[str]] | None = None,
    body_origins: dict[int, tuple[float, float]] | None = None,
) -> Path:
    """Write a lightweight multi-page PDF for regression tests.

    Args:
        path: Output PDF path.
        pages: One list of text lines per page. The helper is ASCII-only because
            it writes minimal PDF streams directly.
        decorative_marks: Optional page-number-to-variant map. A mark draws
            vector graphics without adding text, which lets tests prove that
            visual-only differences do not appear in text-based protocol diffs.
        footer_lines: Optional page-number-to-lines map rendered in the bottom
            margin. It lets regression fixtures exercise coordinate-proven
            running-furniture filtering through the real PDF entry point.
        header_lines: Optional page-number-to-lines map rendered in the top
            margin for the same coordinate-proven running-header checks.
        body_origins: Optional page-number-to-``(x, y)`` source coordinates for
            controlled layout-reflow tests. Normal content starts at
            ``(72, 740)``.

    Returns:
        The resolved output path.
    """

    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    decorative_marks = decorative_marks or {}
    footer_lines = footer_lines or {}
    header_lines = header_lines or {}
    body_origins = body_origins or {}

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_object_numbers: list[int] = []
    for page_number, lines in enumerate(pages, start=1):
        stream = _page_stream(
            lines,
            decorative_marks.get(page_number),
            footer_lines.get(page_number, []),
            header_lines.get(page_number, []),
            body_origins.get(page_number, (72.0, 740.0)),
        )
        page_object_number = len(objects) + 1
        content_object_number = page_object_number + 1
        page_object_numbers.append(page_object_number)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + f"{content_object_number} 0 R".encode("ascii")
            + b" >>"
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    kids = " ".join(f"{number} 0 R" for number in page_object_numbers)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_numbers)} >>".encode(
        "ascii"
    )

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


def _page_stream(
    lines: list[str],
    decorative_mark: str | None,
    footer_lines: list[str],
    header_lines: list[str],
    body_origin: tuple[float, float],
) -> bytes:
    """Build one PDF page content stream."""

    commands: list[str] = []
    if decorative_mark:
        commands.extend(_decorative_mark_commands(decorative_mark))
    if header_lines:
        escaped_header = [_pdf_escape(line) for line in header_lines]
        commands.extend(["BT", "/F1 9 Tf", "72 775 Td", "11 TL"])
        for index, line in enumerate(escaped_header):
            if index:
                commands.append("T*")
            commands.append(f"({line}) Tj")
        commands.append("ET")
    escaped_lines = [_pdf_escape(line) for line in lines]
    body_x, body_y = body_origin
    commands.extend(["BT", "/F1 12 Tf", f"{body_x:g} {body_y:g} Td", "16 TL"])
    for index, line in enumerate(escaped_lines):
        if index:
            commands.append("T*")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    if footer_lines:
        escaped_footer = [_pdf_escape(line) for line in footer_lines]
        commands.extend(["BT", "/F1 9 Tf", "72 68 Td", "12 TL"])
        for index, line in enumerate(escaped_footer):
            if index:
                commands.append("T*")
            commands.append(f"({line}) Tj")
        commands.append("ET")
    return "\n".join(commands).encode("latin-1")


def _decorative_mark_commands(variant: str) -> list[str]:
    """Return vector drawing commands that simulate a non-text image change."""

    if variant == "new":
        return ["q", "0.1 0.4 0.8 rg", "420 620 95 44 re f", "Q"]
    if variant == "small-new":
        return ["q", "0.1 0.4 0.8 rg", "420 620 10 20 re f", "Q"]
    if variant == "small-inline":
        return ["q", "0.1 0.4 0.8 rg", "200 626 10 8 re f", "Q"]
    if variant == "footer-small":
        return ["q", "0.1 0.4 0.8 rg", "260 25 10 20 re f", "Q"]
    if variant == "header-small":
        return ["q", "0.1 0.4 0.8 rg", "550 760 10 20 re f", "Q"]
    return ["q", "0.8 0.2 0.1 rg", "410 615 80 55 re f", "Q"]


def _pdf_escape(value: str) -> str:
    """Escape text for a literal PDF string."""

    ascii_value = value.encode("latin-1", errors="replace").decode("latin-1")
    return ascii_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
