"""Conditional full-document table projection; original result is never mutated."""

import csv
import hashlib
import html
import json
import math
import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path

from .comparison_session import comparison_session
from .progress import ProgressEvent, notify_progress, progress_session

_ACTIVE = ContextVar("table_view_transaction", default=None)


@dataclass
class DualResult:
    original: object
    candidate: object | None
    rows: list
    reason: str


@dataclass
class ReportOutcome:
    selected_result: object
    outputs: dict
    selection_reason: str


def begin_extraction(path):
    state = _ACTIVE.get()
    if state is not None:
        state["current_name"] = Path(path).name


def _complete_numeric_literal_key(value):
    """Only the sign spelling of a complete decimal/scientific literal.

    This is typed numeric equivalence, not font/glyph identity. No whitespace,
    ranges, placeholders, fractions, expressions or private glyphs are accepted.
    """
    if not isinstance(value, str) or not re.fullmatch(
        r"[+\-‐]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+\-‐]?[0-9]+)?", value
    ):
        return None
    return value.replace("‐", "-")


def _supported_three_cells(cells):
    return (
        len(cells) == 3 and all(isinstance(c, str) for c in cells)
        and cells[0].isascii() and cells[2].isascii()
        and (cells[1].isascii() or _complete_numeric_literal_key(cells[1]) is not None)
    )


def _same_three_cells(old, new):
    # The caller already proves one unique row per side in the same table group.
    # Identity and units remain exact; only the complete Setting can vary.
    if not _supported_three_cells(old) or not _supported_three_cells(new):
        return False
    if old == new:
        return True
    # Literal shape alone does not distinguish versions/encoding identifiers.
    # Only these explicit physical Units establish a numeric measurement role.
    physical_units = {"ns/mm", "ps/mm", "V", "mV"}
    return (old[0] == new[0] and old[2] == new[2] and old[2] in physical_units
            and _complete_numeric_literal_key(old[1]) is not None
            and _complete_numeric_literal_key(old[1]) == _complete_numeric_literal_key(new[1]))


def capture_rows(page, table, rows, observed, complete):
    state = _ACTIVE.get()
    if state is None or observed is None:
        return
    from . import pdf_extract as e
    from .physical_native_evidence import native_page_evidence

    try:
        name = state.get("current_name")
        if not name:
            return
        headers = [
            i for i, r in enumerate(rows) if r == ["Parameter", "Setting", "Units"]
        ]
        if len(headers) != 1:
            return
        h = headers[0]
        bounds = [tuple(r.cells) for r in table.rows]
        lines, *_ = e._table_lines_from_rows_with_data_evidence(
            rows, 1, cell_word_rows=observed, cell_bounds_rows=bounds
        )
        _, chars = native_page_evidence(page)

        def valid(b):
            return (
                b
                and len(b) == 4
                and all(
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                    for v in b
                )
                and b[0] < b[2]
                and b[1] < b[3]
            )

        def inside(c, b):
            return b[0] <= c[2] < c[4] <= b[2] and b[1] <= c[3] < c[5] <= b[3]

        if not chars or len(bounds[h]) != 3 or not all(valid(b) for b in bounds[h]):
            return
        for i in range(h + 1, len(rows)):
            raw, bs, box = rows[i], bounds[i], table.rows[i].bbox
            if (
                len(raw) != 3
                or len(bs) != 3
                or not all(isinstance(c, str) and len(c.splitlines()) <= 1 for c in raw)
                or not raw[0]
                or not raw[1]
            ):
                continue
            if not _supported_three_cells(raw):
                continue
            if not valid(box) or not all(valid(b) for b in bs):
                continue
            if any(
                (bs[j][0], bs[j][2]) != (bounds[h][j][0], bounds[h][j][2])
                for j in range(3)
            ) or any(bs[j][2] > bs[j + 1][0] for j in range(2)):
                continue
            if any(
                j != i
                and valid(other.bbox)
                and min(box[2], other.bbox[2]) > max(box[0], other.bbox[0])
                and min(box[3], other.bbox[3]) > max(box[1], other.bbox[1])
                for j, other in enumerate(table.rows)
            ):
                continue
            hits = [
                c
                for c in chars
                if box[0] <= (c[2] + c[4]) / 2 <= box[2]
                and box[1] <= (c[3] + c[5]) / 2 <= box[3]
            ]
            if not hits or any(
                c[7] != 1
                or unicodedata.category(c[1]) == "Co"
                or sum(inside(c, b) for b in bs) != 1
                for c in hits
            ):
                continue
            if any(
                "".join(c[1] for c in hits if inside(c, b)) != "".join(raw[j].split())
                for j, b in enumerate(bs)
            ):
                continue
            serialized = (
                "表格行: T1 | Parameter="
                + raw[0]
                + " | Setting="
                + raw[1]
                + " | Units="
                + raw[2]
            )
            if lines.count(serialized) != 1:
                continue
            state["rows"].append(
                {
                    "name": name,
                    "page": page.page_number,
                    "cells": list(raw),
                    "cell_bboxes": bs,
                    "bbox": box,
                    "native_chars": hits,
                    "proposed": not complete,
                }
            )
    except (AttributeError, TypeError, ValueError, IndexError):
        return


def capture_page(page, name, number, words, covered, table_lines, warnings, error, ocr):
    state = _ACTIVE.get()
    if state is None or ocr:
        return
    rows = [
        r
        for r in state["rows"]
        if r["name"] == name and r["page"] == number and r["proposed"]
    ]
    if not rows:
        return
    from . import pdf_extract as e

    text = e._extract_text_without_proven_table_bboxes(
        page,
        words,
        tuple(covered) + tuple(r["bbox"] for r in rows),
        coordinate_warnings=warnings,
        coordinate_error=error,
    )
    if text is not None:
        state["pages"][(name, number)] = e._combine_text_and_table_lines(
            e._clean_extracted_page_text(text), table_lines
        )


def capture_extractions(old, new):
    state = _ACTIVE.get()
    if state is not None:
        state["extractions"] = old, new


def _mapping(result):
    return [
        (
            getattr(c.old_section, "section_id", None),
            getattr(c.new_section, "section_id", None),
            getattr(c.old_section, "start_page", None),
            getattr(c.old_section, "end_page", None),
            getattr(c.new_section, "start_page", None),
            getattr(c.new_section, "end_page", None),
        )
        for c in result.changes
    ]


def _visual_inputs(extractions, result):
    from .visual_watchdog import (
        _provable_exact_text_pairs,
        _reader_visible_semantic_change_pages,
        _semantic_evidence_bboxes,
    )
    return (_provable_exact_text_pairs(*extractions),
            _reader_visible_semantic_change_pages(result),
            _semantic_evidence_bboxes(result,side='old'),
            _semantic_evidence_bboxes(result,side='new'))


def capture_visual_inputs(old, new, result):
    state=_ACTIVE.get()
    if state is not None:
        from copy import deepcopy
        # Record the actual pre-watchdog semantic view, before prose/formula postprocessing.
        state['visual_inputs']=deepcopy(_visual_inputs((old,new),result))


def _candidate_visual_evidence(original,candidate,sources,projected,options,recorded_inputs):
    from dataclasses import fields

    from .models import VisualWatchdogAudit
    from .visual_watchdog import detect_visual_review_items
    if not options.visual_watchdog:
        return [], ('视觉漏检哨兵已关闭；本次结果不能证明没有像素层变化。',), VisualWatchdogAudit(enabled=False,attempted=False,backend_available=None,eligible_page_pair_count=0,checked_page_pair_count=0,failed_page_pair_count=0,ambiguous_page_count=0,excluded_region_count=0,complete=False,source_hashes_match=None)
    audit=getattr(original.provenance,'visual_watchdog_audit',None)
    try:
        same_sources = len(sources) == len(projected) == 2 and all(
            a.source_sha256 and a.source_sha256 == b.source_sha256
            and a.pdf_path == b.pdf_path and len(a.pages) == len(b.pages)
            and all(type(p) is type(q) and all(
                getattr(p, f.name) == getattr(q, f.name)
                for f in fields(p) if f.name not in ('text', 'source_char_map')
            ) for p, q in zip(a.pages, b.pages))
            for a, b in zip(sources, projected)
        )
    except (AttributeError, TypeError):
        # Unknown page providers cannot authorize reuse; obtain fresh evidence.
        same_sources = False
    if audit is not None and audit.enabled and same_sources and recorded_inputs is not None and recorded_inputs==_visual_inputs(projected,candidate):
        return list(original.visual_review_items),tuple(original.visual_review_warnings),audit
    items,warnings,audit=detect_visual_review_items(*projected,semantic_result=candidate)
    return items,tuple(warnings),audit


@comparison_session
@progress_session
def run_diff_transaction(old_pdf, new_pdf, options, *, progress_observer=None):
    from .compare import compare_extractions, run_diff
    from .prose_source_visuals import build_prose_source_visuals
    from .visual_ownership import build_visual_owned_spans

    state = {"rows": [], "pages": {}, "extractions": None}
    token = _ACTIVE.set(state)
    try:
        original = run_diff(
            old_pdf, new_pdf, options, progress_observer=progress_observer
        )
    finally:
        _ACTIVE.reset(token)
    if not state["pages"] or not state["extractions"]:
        return DualResult(original, None, [], "no eligible projection")
    old, new = state["extractions"]
    # Whole-page native projection changes cannot retain a stale character map.
    projected = tuple(
        replace(
            ex,
            pages=[
                replace(
                    p,
                    text=state["pages"][(ex.pdf_path.name, p.page_number)],
                    source_char_map=(),
                )
                if (ex.pdf_path.name, p.page_number) in state["pages"]
                else p
                for p in ex.pages
            ],
        )
        for ex in (old, new)
    )
    notify_progress(progress_observer, ProgressEvent(stage="match_diff", detail="完整候选视图比较"))
    candidate = compare_extractions(
        *projected,
        options,
        source_typography_receipts=(
            original.old_superscript_receipts,
            original.new_superscript_receipts,
        ),
    )
    # Equality of complete section pairing, not only matched changed cards.
    section_map = lambda r: [
        [
            (s.section_id, s.start_page, s.end_page, s.number_path, s.title)
            for s in getattr(r, k)
        ]
        for k in ("old_sections", "new_sections")
    ]
    if section_map(original) != section_map(candidate) or not set(
        _mapping(candidate)
    ).issubset(set(_mapping(original))):
        return DualResult(original, None, [], "section mapping changed")
    if (
        original.old_table_visuals != candidate.old_table_visuals
        or original.new_table_visuals != candidate.new_table_visuals
    ):
        return DualResult(original, None, [], "complete table source changed")
    from .reporting import _build_table_changes, _paired_table_visuals

    groups_for = lambda r: _paired_table_visuals(
        r.old_table_visuals,
        r.new_table_visuals,
        old_sections=r.old_sections,
        new_sections=r.new_sections,
    )
    original_groups, candidate_groups = groups_for(original), groups_for(candidate)
    if original_groups != candidate_groups or _build_table_changes(
        original, table_groups=original_groups
    ) != _build_table_changes(candidate, table_groups=candidate_groups):
        return DualResult(
            original, None, [], "table groups or complete table changes changed"
        )
    notify_progress(progress_observer, ProgressEvent(stage="visual_evidence", detail="候选视图视觉证据"))
    visual_items, visual_warnings, visual_audit = _candidate_visual_evidence(original,candidate,(old,new),projected,options,state.get('visual_inputs'))
    from .compare import _assessment_with_visual_review
    # Rebuild text-dependent source visuals; reuse only pixel review with unchanged section mapping.
    groups, warnings = build_prose_source_visuals(candidate, *projected)
    from .formula_source_review import build_formula_source_reviews

    # The candidate has its own PageText offsets. Original formula receipts cannot
    # authorize this view, and compare_extractions does not build these receipts.
    formula_reviews = build_formula_source_reviews(candidate, *projected)
    candidate = replace(
        candidate,
        formula_source_reviews=formula_reviews,
        formula_source_context=tuple(
            (str(path), ex.source_sha256, page)
            for side, path, ex in (
                ("old", candidate.old_pdf, projected[0]),
                ("new", candidate.new_pdf, projected[1]),
            )
            for page in ex.pages
            if any(getattr(r, side)["page"] == page.page_number for r in formula_reviews)
        ),
        prose_source_visuals=groups,
        visual_owned_spans=build_visual_owned_spans(candidate, *projected, groups),
        visual_review_items=visual_items,
        visual_review_warnings=visual_warnings,
        provenance=replace(candidate.provenance,visual_watchdog_audit=visual_audit) if candidate.provenance is not None else None,
        warnings=[*candidate.warnings,*visual_warnings,*warnings],
        assessment=_assessment_with_visual_review(candidate.assessment,visual_review_count=len(visual_items),audit=visual_audit),
    )
    return DualResult(original, candidate, state["rows"], "pending final receipts")


def _source_row_matches(record, table):
    """Bind receipt to one actual source row and the actual screenshot extent."""
    from .physical_native_evidence import _valid_box

    try:
        cells = record["cells"]
        bounds = record["cell_bboxes"]
        box = record["bbox"]
        chars = record["native_chars"]
        if (
            not _supported_three_cells(cells)
            or len(bounds) != 3
            or not _valid_box(box)
            or not _valid_box(table.bbox)
            or not all(_valid_box(b) for b in bounds)
        ):
            return False
        matches = [
            bs
            for raw, bs in zip(
                table.raw_source_cells, table.raw_cell_bounds, strict=True
            )
            if tuple(raw) == tuple(cells)
            and len(bs) == 3
            and all(b is not None for b in bs)
            and tuple(tuple(b) for b in bs) == tuple(tuple(b) for b in bounds)
        ]
        if len(matches) != 1:
            return False
        expected = (
            min(b[0] for b in bounds),
            min(b[1] for b in bounds),
            max(b[2] for b in bounds),
            max(b[3] for b in bounds),
        )
        if tuple(box) != expected or not (
            table.bbox[0] <= box[0] < box[2] <= table.bbox[2]
            and table.bbox[1] <= box[1] < box[3] <= table.bbox[3]
        ):
            return False

        def inside(c, b):
            return b[0] <= c[2] < c[4] <= b[2] and b[1] <= c[3] < c[5] <= b[3]

        if not chars or any(
            len(c) != 8
            or type(c[0]) is not int
            or c[0] < 0
            or not isinstance(c[1], str)
            or len(c[1]) != 1
            or not (c[1].isascii() or (c[1] == "‐" and inside(c, bounds[1])))
            or c[1].isspace()
            or not _valid_box(c[2:6])
            or c[7] != 1
            or sum(inside(c, b) for b in bounds) != 1
            for c in chars
        ):
            return False
        if [c[0] for c in chars] != sorted({c[0] for c in chars}) or len(
            {c[6] for c in chars}
        ) != len(chars):
            return False
        return all(
            "".join(c[1] for c in chars if inside(c, b)) == "".join(raw.split())
            for raw, b in zip(cells, bounds, strict=True)
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def _write_receipts(bundle, directory):
    from .physical_table_rows import _image_is_decodable
    from .reporting import _paired_table_visuals

    rows = bundle.rows
    required = [r for r in rows if r["proposed"]]
    original = bundle.original
    groups = _paired_table_visuals(
        original.old_table_visuals,
        original.new_table_visuals,
        old_sections=original.old_sections,
        new_sections=original.new_sections,
    )
    if original.old_pdf.name == original.new_pdf.name:
        return False

    def owner(record):
        side = (
            "old"
            if record["name"] == original.old_pdf.name
            else "new"
            if record["name"] == original.new_pdf.name
            else None
        )
        if side is None:
            return None
        found = [
            (i, t)
            for i, g in enumerate(groups)
            for t in getattr(g, side + "_tables")
            if t.page_number == record["page"] and _source_row_matches(record, t)
        ]
        return (side, *found[0]) if len(found) == 1 else None

    owners = [owner(r) for r in rows]
    payload = []
    for row in required:
        own = owner(row)
        if own is None:
            return False
        paired = [
            (r, o)
            for r, o in zip(rows, owners)
            if o is not None and o[1] == own[1] and r["cells"][0] == row["cells"][0]
        ]
        if (
            len(paired) != 2
            or {o[0] for _, o in paired} != {"old", "new"}
            or not _same_three_cells(paired[0][0]["cells"], paired[1][0]["cells"])
        ):
            return False
        for r, o in paired:
            if not _image_is_decodable(o[2].image_data_uri):
                return False
            record = dict(r, image=o[2].image_data_uri, group_index=o[1])
            if record not in payload:
                payload.append(record)
    if not payload:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "three_cell_records.csv"
    fields = ["name", "page", "cells", "cell_bboxes", "bbox", "native_chars"]
    try:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in payload:
                writer.writerow(
                    {k: json.dumps(r[k], ensure_ascii=False) for k in fields}
                )
        with path.open(encoding="utf-8") as f:
            back = [{k: json.loads(v) for k, v in r.items()} for r in csv.DictReader(f)]
        expected = json.loads(json.dumps([{k: r[k] for k in fields} for r in payload]))
        if back != expected:
            return False
        text = "\n".join(
            r["name"]
            + " "
            + str(r["page"])
            + "\n"
            + "\n".join(
                k + ": " + v
                for k, v in zip(("Parameter", "Setting", "Units"), r["cells"])
            )
            for r in payload
        )
        fragment = (
            "<details><summary>原物理三格（未证明整表逻辑对应）</summary>"
            + "".join(
                "<p>"
                + html.escape(r["name"])
                + " "
                + str(r["page"])
                + "</p><table><tr>"
                + "".join(
                    '<td><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
                    + html.escape(c)
                    + "</pre></td>"
                    for c in r["cells"]
                )
                + '</tr></table><img style="max-width:100%" src="'
                + r["image"]
                + '">'
                for r in payload
            )
            + "</details>"
        )
        return payload, text, fragment, path
    except (OSError, ValueError, TypeError):
        return False


def _write_reports_staged(bundle, output_dir, options):
    from .reporting import write_reports

    if not isinstance(bundle, DualResult):
        return ReportOutcome(
            bundle, write_reports(bundle, output_dir, options), "ordinary result"
        )
    import tempfile

    with tempfile.TemporaryDirectory(
        prefix="table-receipts-",
        dir=Path(output_dir).mkdir(parents=True, exist_ok=True) or output_dir,
    ) as tmp:
        receipts = (
            _write_receipts(bundle, Path(tmp))
            if bundle.candidate is not None
            else False
        )
        selected = bundle.candidate if receipts else bundle.original
        outputs = write_reports(selected, output_dir, options)
        if receipts:
            payload, text, fragment, csvpath = receipts
            try:
                destination = outputs["json"].parent / "three_cell_records.csv"
                destination.write_bytes(csvpath.read_bytes())
                if destination.read_bytes() != csvpath.read_bytes():
                    raise OSError("receipt copy mismatch")
                for kind in ["markdown", "text"]:
                    if kind in outputs:
                        with outputs[kind].open("a", encoding="utf-8") as f:
                            f.write(
                                "\n\n原物理三格（未证明整表逻辑对应）\n" + text + "\n"
                            )
                hp = outputs["html"]
                s = hp.read_text(encoding="utf-8")
                hp.write_text(
                    s.replace("</body>", fragment + "</body>"), encoding="utf-8"
                )
                if fragment not in hp.read_text(encoding="utf-8"):
                    raise OSError("missing HTML receipt")
                jp = outputs["json"]
                data = json.loads(jp.read_text(encoding="utf-8"))
                data[
                    "original_changes_audit"
                ] = []  # Filled below from the original public serialization.
                from .reporting import _change_to_dict

                data["original_changes_audit"] = [
                    _change_to_dict(c) for c in bundle.original.changes
                ]
                data["three_cell_records"] = [
                    {
                        **{k: v for k, v in r.items() if k != "image"},
                        "image_sha256": hashlib.sha256(r["image"].encode()).hexdigest(),
                    }
                    for r in payload
                ]
                data["table_projection_selection"] = "candidate"
                jp.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, ValueError, TypeError):
                # The isolated publisher requires report_dir directly below staging.
                # Remove rejected draft from that publication root before rebuilding.
                rejected = outputs["json"].parent
                rejected.rename(Path(tmp) / "rejected-draft")
                selected = bundle.original
                outputs = write_reports(selected, output_dir, options)
                return ReportOutcome(
                    selected, outputs, "final receipt failed; whole original result"
                )
        return ReportOutcome(
            selected,
            outputs,
            "candidate with final receipts" if receipts else "whole original result",
        )


def write_reports_transaction(bundle, output_dir, options):
    """Build only in owned staging; publish once into an exclusive direct child."""
    import os
    import tempfile

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".table-view-owned-", dir=root) as owned:
        outcome = _write_reports_staged(bundle, Path(owned), options)
        source = outcome.outputs["report_dir"].resolve()
        if source.parent != Path(owned).resolve() or source.is_symlink():
            raise ValueError("transaction report escaped owned staging")
        relative = {}
        for key, value in outcome.outputs.items():
            value = Path(value)
            if value.is_symlink():
                raise ValueError("symlink report output")
            relative[key] = value.resolve().relative_to(source)
        # This empty destination is created by this transaction, never reused.
        destination = Path(
            tempfile.mkdtemp(prefix=f"{source.name}_", dir=root)
        )
        try:
            os.replace(source, destination)
        except OSError:
            # Only remove our own empty reservation; staging is cleaned by its owner.
            destination.rmdir()
            raise
        return ReportOutcome(
            outcome.selected_result,
            {key: destination / path for key, path in relative.items()},
            outcome.selection_reason,
        )


def report_outcome(result, output_dir, options, *, writer=None):
    """Keep injected writers' ordinary-result and dictionary contracts."""
    from .reporting import write_reports

    if writer is not None and writer is not write_reports:
        original = result.original if isinstance(result, DualResult) else result
        return ReportOutcome(
            original,
            writer(original, output_dir, options),
            "injected writer: original view",
        )
    return write_reports_transaction(result, output_dir, options)
