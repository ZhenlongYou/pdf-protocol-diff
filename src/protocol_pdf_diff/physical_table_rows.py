"""Preserve physical source cells without asserting logical subrow alignment."""

from __future__ import annotations
import csv
import base64
import hashlib
import html
import io
import json
import re
from collections import Counter
from dataclasses import asdict


def capture_physical_rows(
    page, table, rows, word_rows, words, page_number, table_number
):
    from . import pdf_extract as e
    from .models import PhysicalTableRow

    if not e._table_data_cell_geometry_is_complete(table, rows, words, word_rows):
        return ()
    result = []
    for index, (raw, observed, geometry) in enumerate(
        zip(rows, word_rows, table.rows, strict=True)
    ):
        # Bounded to merged four-cell records; ordinary logical rows keep their
        # existing path. This never proves any Symbol-to-description subrow.
        if len(raw) != 4 or not all(
            isinstance(cell, str) and cell.strip() for cell in raw
        ):
            continue
        line_counts = tuple(len(cell.splitlines()) for cell in raw)
        # A single Symbol can head a complete physical record containing
        # minimum/maximum/step lines. Preserve all four cells as one record;
        # this grants no logical subrow pairing or Symbol-to-value inference.
        if not (min(line_counts[c] for c in (1, 2, 3)) >= 3
                or line_counts == (4, 1, 3, 3)):
            continue
        bounds = tuple(geometry.cells)
        if len(bounds) != 4 or any(b is None for b in bounds):
            continue
        box = tuple(geometry.bbox)
        if not e._table_row_bbox_matches_raw_cells(
            page, box, raw, cell_word_row=observed
        ):
            continue
        if not e._source_cells_match_observed_geometry(
            [e._table_cell_lines(c) for c in raw], observed
        ):
            continue
        cell_words = tuple(
            tuple(
                (
                    str(w["text"]),
                    float(w["x0"]),
                    float(w["top"]),
                    float(w["x1"]),
                    float(w["bottom"]),
                )
                for w in cell
            )
            for cell in observed
        )
        if any(not cell for cell in cell_words):
            continue
        payload = [page_number, table_number, index, raw, bounds, cell_words]
        identity = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False).encode()
        ).hexdigest()
        from .physical_native_evidence import capture_native_row
        result.append(capture_native_row(PhysicalTableRow(identity, tuple(raw), box, bounds, cell_words), page))
    return tuple(result)


def _key(row):
    # Preserve ordered lines in every cell, including Symbol. Only the known
    # parenthesized Note marker gap is cosmetic; no subrow inference is made.
    descriptor = re.sub(r"\(Note\s+(\d+)\)", r"(Note\1)", row.cells[0])
    return (descriptor, row.cells[1], row.cells[2], row.cells[3])


def _image_is_decodable(value):
    try:
        from PIL import Image

        prefix, encoded = value.split(",", 1)
        if prefix not in ("data:image/jpeg;base64", "data:image/png;base64"):
            return False
        with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as im:
            im.verify()
        return True
    except Exception:
        return False


def _valid_row(row, table):
    from .visual_ownership import _inside

    if len(row.cells) != 4 or len(row.cell_bboxes) != 4 or len(row.cell_words) != 4:
        return False
    if not _inside(("", *row.bbox), table.bbox):
        return False
    for cell, box, words in zip(
        row.cells, row.cell_bboxes, row.cell_words, strict=True
    ):
        if not cell or not words or not _inside(("", *box), row.bbox):
            return False
        if any(not _inside(word, box) for word in words):
            return False
        from .pdf_extract import (
            _source_cells_match_observed_geometry,
            _table_cell_lines,
        )

        observed = [
            dict(zip(("text", "x0", "top", "x1", "bottom"), word)) for word in words
        ]
        if not _source_cells_match_observed_geometry(
            [_table_cell_lines(cell)], [observed]
        ):
            return False
    return True


def render_physical_appendix(groups, *, displayed_source_keys=()):
    """Render physical-row records while emitting each source table image once.

    A physical row is still a separate audit record, but its screenshot is
    table-level evidence.  Repeating that image for every row made one source
    page appear many times in the reader report.  When the main/uncertainty
    table evidence already displays the source, the row record keeps the
    original cells and points back to that evidence instead of embedding a
    second copy.
    """
    sections, payload, receipts = [], [], set()
    displayed_source_keys = set(displayed_source_keys)
    emitted_images = {}
    for group in groups:
        if not group.old_tables or not group.new_tables:
            continue
        if all(
            t.row_alignment_reliable for t in (*group.old_tables, *group.new_tables)
        ):
            continue
        sides = [
            [(t, r) for t in tables for r in t.physical_rows if _valid_row(r, t)]
            for tables in (group.old_tables, group.new_tables)
        ]
        counts = [Counter(_key(r) for t, r in values) for values in sides]
        for old_table, old in sides[0]:
            key = _key(old)
            if counts[0][key] != 1 or counts[1][key] != 1:
                continue
            new_table, new = next((t, r) for t, r in sides[1] if _key(r) == key)
            if not (_valid_row(old, old_table) and _valid_row(new, new_table)):
                continue
            if not all(
                _image_is_decodable(t.image_data_uri) for t in (old_table, new_table)
            ):
                continue
            section_id = f"physical-table-row-{len(sections) + 1}"
            parts = [
                f'<section class="physical-table-row" id="{section_id}"><p>保留两版原始单元格及截图；各参数与数值的对应关系仍需核实，未认定表格内容一致。</p>'
            ]
            entry = {}
            for side, table, row in [("old", old_table, old), ("new", new_table, new)]:
                source_key = (side, table.page_number, table.table_number)
                parts.append(
                    '<div class="table-shot"><p>'
                    + ("旧版" if side == "old" else "新版")
                    + f' PDF {table.page_number}</p><table style="width:100%;table-layout:fixed"><tr>'
                )
                parts.extend(
                    '<td><pre style="white-space:pre-wrap;overflow-wrap:anywhere;min-width:0">'
                    + html.escape(cell)
                    + "</pre></td>"
                    for cell in row.cells
                )
                image_key = (
                    source_key
                    + (hashlib.sha256(table.image_data_uri.encode()).hexdigest(),)
                )
                if source_key in displayed_source_keys:
                    image_html = (
                        '<div class="table-shot-page table-shot-reused">'
                        '原页截图已在上方表格证据中展示；本条保留原始单元格文字。'
                        '</div>'
                    )
                elif image_key in emitted_images:
                    image_html = (
                        '<div class="table-shot-page table-shot-reused">'
                        f'截图已在 <a href="#{emitted_images[image_key]}">前一条记录</a> 展示；'
                        '本条保留原始单元格文字。'
                        '</div>'
                    )
                else:
                    emitted_images[image_key] = section_id
                    image_html = (
                        '<div class="table-shot-page"><img alt="原始表格截图" src="'
                        + table.image_data_uri
                        + '"></div>'
                    )
                parts.append('</tr></table>' + image_html + '</div>')
                entry[side] = {
                    "page": table.page_number,
                    **asdict(row),
                    "image_sha256": hashlib.sha256(
                        table.image_data_uri.encode()
                    ).hexdigest(),
                    "logical_alignment": False,
                }
            parts.append("</section>")
            rendered = "".join(parts)
            # Receipt only follows construction of the actual fragment which
            # is inserted in HTML and textual exports, not image metadata alone.
            for side, row in [("old", old), ("new", new)]:
                receipts.add((side, row.row_id))
            sections.append(rendered)
            payload.append(entry)
    fragment = "".join(sections)
    if fragment:
        fragment = (
            '<details class="physical-table-records"><summary>原始表格单元格与截图（对应待核实）</summary>'
            + fragment
            + "</details>"
        )
    return fragment, payload, receipts


def physical_text_export(payload, *, markdown=False):
    """Keep original cells in text exports without HTML or image payloads."""
    if not payload:
        return ""
    lines = ["", "", "表格原始单元格（跨行对应待核实）"]
    for entry in payload:
        for side, label in (("old", "旧版"), ("new", "新版")):
            row = entry[side]
            lines.append(f"{label} PDF 第 {row['page']} 页")
            for index, cell in enumerate(row["cells"], start=1):
                lines.append(f"原始单元格 {index}：")
                if markdown:
                    fence = "`" * max(
                        3,
                        1
                        + max(
                            (len(m.group()) for m in re.finditer(r"`+", cell)),
                            default=0,
                        ),
                    )
                    lines.extend((fence, cell, fence, ""))
                else:
                    lines.extend((cell, ""))
    return "\n".join(lines)


def authorized_spans(candidates, side, section_id, receipts):
    normal_key = side + ":" + section_id
    output = {
        text: list(spans) for text, spans in candidates.get(normal_key, {}).items()
    }
    for owner_side, row_id in receipts:
        if owner_side != side:
            continue
        key = "physical:" + row_id + ":" + normal_key
        for text, spans in candidates.get(key, {}).items():
            output.setdefault(text, []).extend(spans)
    from .physical_native_evidence import authorized_native_spans
    for text, spans in authorized_native_spans(candidates, side, section_id, receipts).items():
        output.setdefault(text, []).extend(spans)
    return output


def write_physical_csv(payload, path):
    """Only issue CSV receipts after exact on-disk round-trip verification."""
    fields = [
        "side",
        "page",
        "row_id",
        "cell_1",
        "cell_2",
        "cell_3",
        "cell_4",
        "bbox",
        "cell_bboxes",
        "cell_words",
        "image_sha256",
        "native_chars",
    ]
    expected = []
    for pair in payload:
        for side in ("old", "new"):
            row = pair[side]
            expected.append(
                [
                    side,
                    str(row["page"]),
                    row["row_id"],
                    *row["cells"],
                    json.dumps(row["bbox"]),
                    json.dumps(row["cell_bboxes"]),
                    json.dumps(row["cell_words"], ensure_ascii=False),
                    row["image_sha256"],
                    json.dumps(row.get("native_chars", ()), ensure_ascii=False),
                ]
            )
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(expected)
    return verify_physical_csv(path, fields, expected)


def verify_physical_csv(path, fields, expected):
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeError, csv.Error):
        return set()
    if rows != [fields, *expected]:
        return set()
    return {(row[0], row[2]) for row in expected}
