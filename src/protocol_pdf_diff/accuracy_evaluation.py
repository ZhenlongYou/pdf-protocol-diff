"""Quantified gold-corpus evaluation through the production PDF diff pipeline.

The manifest describes manually verified change events.  The evaluator runs the
same ``run_diff`` and ``write_reports`` entry points used by the application,
matches expected events against complete JSON audit facts, and separately checks
whether each event obeys its reader-visibility contract.  Precision is reported
only when every executed case declares an exhaustive oracle.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import Any

from .compare import run_diff
from .models import DiffOptions, DiffResult
from .reporting import write_reports

_EVENT_KINDS = frozenset({"text", "table", "formula", "visual"})
_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "minimum_distinct_families", "cases"}
)
_CASE_KEYS = frozenset(
    {
        "id",
        "family",
        "required",
        "old",
        "new",
        "options",
        "oracle_complete",
        "visual_coverage_required",
        "expected_events",
    }
)
_DOCUMENT_KEYS = frozenset({"path", "start_page", "end_page"})
_OPTION_KEYS = frozenset({"layout_backend", "ocr_language", "min_section_match_similarity"})
_EVENT_KEYS = frozenset(
    {
        "id",
        "kind",
        "old",
        "new",
        "old_page",
        "new_page",
        "location",
        "occurrences",
        "critical",
        "reader_visible",
    }
)


@dataclass(frozen=True)
class _ReaderSurfaceEvidence:
    """Visible text plus independently rendered report cards for one surface."""

    full_text: str
    blocks: tuple[tuple[str, str], ...] = ()


def run_gold_accuracy_evaluation(
    manifest_path: str | Path,
    *,
    corpus_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run all gold cases and return privacy-safe aggregate recognition metrics."""

    source = Path(manifest_path).expanduser().resolve()
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _manifest_failure([f"cannot read manifest: {type(exc).__name__}"])
    failures = validate_gold_accuracy_manifest(manifest)
    if failures:
        return _manifest_failure(failures)
    root = (
        Path(corpus_root).expanduser().resolve()
        if corpus_root is not None
        else source.parent
    )
    case_results = [
        _run_case(case, root, case_index=index)
        for index, case in enumerate(manifest["cases"], start=1)
    ]
    minimum_distinct_families = manifest.get("minimum_distinct_families", 1)
    executed_families: set[str] = set()
    counted_source_pairs: set[tuple[str, str]] = set()
    for case, result in zip(manifest["cases"], case_results, strict=True):
        source_pair = result.get("_family_source_pair")
        if (
            "metrics" not in result
            or not isinstance(source_pair, tuple)
            or source_pair in counted_source_pairs
        ):
            continue
        counted_source_pairs.add(source_pair)
        executed_families.add(
            re.sub(
                r"\s+",
                " ",
                str(case.get("family", "unspecified")).strip().casefold(),
            )
        )
    for result in case_results:
        result.pop("_family_source_pair", None)  # 内部 SHA 对只用于去重，不写入隐私安全 summary。
    family_coverage = {
        "required": minimum_distinct_families,
        "executed": len(executed_families),
        "complete": len(executed_families) >= minimum_distinct_families,
    }
    if not family_coverage["complete"]:
        case_results.append(
            {
                "case_index": 0,
                "status": "fail",
                "failures": [
                    (
                        "cross-family coverage incomplete: executed "
                        f"{family_coverage['executed']}/{family_coverage['required']} "
                        "distinct document families"
                    )
                ],
            }
        )
    counts = {
        status: sum(case["status"] == status for case in case_results)
        for status in ("pass", "fail", "skip")
    }
    status = "fail" if counts["fail"] else ("pass" if counts["pass"] else "skip")
    return {
        "schema_version": 1,
        "status": status,
        "counts": counts,
        "family_coverage": family_coverage,
        "metrics": _aggregate_metrics(case_results),
        "cases": case_results,
    }


def validate_gold_accuracy_manifest(manifest: object) -> list[str]:
    """Return all schema failures without opening any PDF."""

    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    failures: list[str] = []
    unknown_top = sorted(set(manifest) - _TOP_LEVEL_KEYS)
    if unknown_top:
        failures.append("unsupported top-level keys: " + ", ".join(unknown_top))
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        failures.append("schema_version must be exactly 1")
    minimum_distinct_families = manifest.get("minimum_distinct_families", 1)
    if (
        not isinstance(minimum_distinct_families, int)
        or isinstance(minimum_distinct_families, bool)
        or minimum_distinct_families < 1
    ):
        failures.append("minimum_distinct_families must be a positive integer")
        minimum_distinct_families = 1
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        failures.append("cases must be a non-empty JSON array")
        return failures
    seen_case_ids: set[str] = set()
    for case_index, case in enumerate(cases):
        location = f"cases[{case_index}]"
        if not isinstance(case, dict):
            failures.append(f"{location} must be a JSON object")
            continue
        unknown_case = sorted(set(case) - _CASE_KEYS)
        if unknown_case:
            failures.append(f"{location} has unsupported keys: {', '.join(unknown_case)}")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            failures.append(f"{location}.id must be a non-empty string")
        elif case_id in seen_case_ids:
            failures.append(f"{location}.id duplicates an earlier case id")
        else:
            seen_case_ids.add(case_id)
        family = case.get("family")
        if family is not None and (
            not isinstance(family, str) or not family.strip()
        ):
            failures.append(f"{location}.family must be a non-empty string")
        if minimum_distinct_families > 1 and family is None:
            failures.append(
                f"{location}.family is required when minimum_distinct_families > 1"
            )
        if not isinstance(case.get("required"), bool):
            failures.append(f"{location}.required must be a boolean")
        _validate_document(case.get("old"), f"{location}.old", failures)
        _validate_document(case.get("new"), f"{location}.new", failures)
        _validate_options(case.get("options", {}), f"{location}.options", failures)
        if not isinstance(case.get("oracle_complete"), bool):
            failures.append(f"{location}.oracle_complete must be a boolean")
        visual_coverage_required = case.get("visual_coverage_required", True)
        if not isinstance(visual_coverage_required, bool):
            failures.append(f"{location}.visual_coverage_required must be a boolean")
        events = case.get("expected_events")
        if not isinstance(events, list) or not events:
            failures.append(f"{location}.expected_events must be a non-empty array")
            continue
        seen_event_ids: set[str] = set()
        for event_index, event in enumerate(events):
            _validate_expected_event(
                event,
                f"{location}.expected_events[{event_index}]",
                failures,
                seen_event_ids,
            )
        if visual_coverage_required is False and any(
            isinstance(event, dict) and event.get("kind") == "visual"
            for event in events
        ):
            failures.append(
                f"{location}.visual_coverage_required cannot be false when visual events are expected"
            )
    return failures


def _validate_document(document: object, location: str, failures: list[str]) -> None:
    if not isinstance(document, dict):
        failures.append(f"{location} must be a JSON object")
        return
    unknown = sorted(set(document) - _DOCUMENT_KEYS)
    if unknown:
        failures.append(f"{location} has unsupported keys: {', '.join(unknown)}")
    path_value = document.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        failures.append(f"{location}.path must be a non-empty safe relative PDF path")
    else:
        native = Path(path_value)
        windows = PureWindowsPath(path_value)
        if (
            native.is_absolute()
            or windows.is_absolute()
            or ".." in native.parts
            or ".." in windows.parts
            or native.suffix.casefold() != ".pdf"
        ):
            failures.append(f"{location}.path must be a non-empty safe relative PDF path")
    for key in ("start_page", "end_page"):
        value = document.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            failures.append(f"{location}.{key} must be a positive integer")
    start = document.get("start_page")
    end = document.get("end_page")
    if isinstance(start, int) and isinstance(end, int) and start > end:
        failures.append(f"{location}.end_page must be >= start_page")


def _validate_options(options: object, location: str, failures: list[str]) -> None:
    if not isinstance(options, dict):
        failures.append(f"{location} must be a JSON object")
        return
    unknown = sorted(set(options) - _OPTION_KEYS)
    if unknown:
        failures.append(f"{location} has unsupported keys: {', '.join(unknown)}")
    backend = options.get("layout_backend")
    if backend is not None and backend not in {"native", "auto", "docling"}:
        failures.append(f"{location}.layout_backend must be native, auto, or docling")
    ocr_language = options.get("ocr_language")
    if ocr_language is not None and (
        not isinstance(ocr_language, str) or not ocr_language.strip()
    ):
        failures.append(f"{location}.ocr_language must be a non-empty string")
    threshold = options.get("min_section_match_similarity")
    if threshold is not None and (
        isinstance(threshold, bool)
        or not isinstance(threshold, int | float)
        or not 0.0 < float(threshold) <= 1.0
    ):
        failures.append(f"{location}.min_section_match_similarity must be in (0, 1]")


def _validate_expected_event(
    event: object,
    location: str,
    failures: list[str],
    seen_ids: set[str],
) -> None:
    if not isinstance(event, dict):
        failures.append(f"{location} must be a JSON object")
        return
    unknown = sorted(set(event) - _EVENT_KEYS)
    if unknown:
        failures.append(f"{location} has unsupported keys: {', '.join(unknown)}")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        failures.append(f"{location}.id must be a non-empty string")
    elif event_id in seen_ids:
        failures.append(f"{location}.id duplicates an earlier event id")
    else:
        seen_ids.add(event_id)
    kind = event.get("kind")
    if kind not in _EVENT_KINDS:
        failures.append(f"{location}.kind must be text, table, formula, or visual")
    if not isinstance(event.get("critical"), bool):
        failures.append(f"{location}.critical must be a boolean")
    if not isinstance(event.get("reader_visible"), bool):
        failures.append(f"{location}.reader_visible must be a boolean")
    occurrences = event.get("occurrences", 1)
    if not isinstance(occurrences, int) or isinstance(occurrences, bool) or occurrences < 1:
        failures.append(f"{location}.occurrences must be a positive integer")
    event_location = event.get("location")
    if event_location is not None and (
        not isinstance(event_location, str) or not event_location.strip()
    ):
        failures.append(f"{location}.location must be a non-empty string")
    if kind == "visual":
        for key in ("old_page", "new_page"):
            value = event.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                failures.append(f"{location}.{key} must be a positive integer")
    else:
        if not any(
            isinstance(event.get(key), str) and event[key]
            for key in ("old", "new")
        ):
            failures.append(f"{location} requires a non-empty old or new literal")
        for key in ("old", "new"):
            value = event.get(key)
            if value is not None and not isinstance(value, str):
                failures.append(f"{location}.{key} must be a string")


def _run_case(case: dict[str, Any], root: Path, *, case_index: int) -> dict[str, Any]:
    old_path = root / case["old"]["path"]
    new_path = root / case["new"]["path"]
    missing = [path for path in (old_path, new_path) if not path.is_file()]
    if missing:
        return {
            "case_index": case_index,
            "status": "fail" if case["required"] else "skip",
            "failures": ["required input missing"] if case["required"] else [],
        }
    options_payload = case.get("options", {})
    options = DiffOptions(
        min_section_match_similarity=options_payload.get(
            "min_section_match_similarity",
            0.72,
        ),
        old_start_page=case["old"].get("start_page"),
        old_end_page=case["old"].get("end_page"),
        new_start_page=case["new"].get("start_page"),
        new_end_page=case["new"].get("end_page"),
        ocr_language=options_payload.get("ocr_language"),
        layout_backend=options_payload.get("layout_backend", "native"),
    )
    try:
        result = run_diff(old_path, new_path, options)
        family_source_pair = _gold_source_pair_key(result)
        with TemporaryDirectory(prefix="pdf_diff_gold_") as report_root:
            outputs = write_reports(result, report_root, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            reader_surfaces = {
                "markdown": _read_markdown_evidence(outputs["markdown"]),
                "text": _read_text_evidence(outputs["text"]),
                # Parse visible HTML incrementally and discard image data URIs
                # before the parser can buffer multi-megabyte attributes.
                "html": _read_visible_html_evidence(outputs["html"]),
            }
    except Exception as exc:
        return {
            "case_index": case_index,
            "status": "fail",
            "failures": [f"pipeline error: {type(exc).__name__}"],
        }
    actual_events = _actual_events(payload, reader_surfaces)
    expected_events = case["expected_events"]
    matched_expected, matched_actual, failures = _match_expected_events(
        expected_events,
        actual_events,
    )
    visual_audit = payload.get("provenance", {}).get("visual_watchdog_run")
    visual_coverage_required = case.get("visual_coverage_required", True)
    visual_coverage_complete = bool(
        isinstance(visual_audit, dict)
        and visual_audit.get("enabled")
        and visual_audit.get("complete")
    )
    if not isinstance(visual_audit, dict):
        if visual_coverage_required:
            failures.append("visual watchdog coverage audit missing")
    elif visual_coverage_required and not visual_coverage_complete:
        failures.append(
            "visual watchdog coverage incomplete: "
            f"checked {visual_audit.get('checked_page_pair_count', 0)}/"
            f"{visual_audit.get('eligible_page_pair_count', 0)}, "
            f"failed {visual_audit.get('failed_page_pair_count', 0)}, "
            f"ambiguous {visual_audit.get('ambiguous_page_count', 0)}"
        )
    oracle_complete = case["oracle_complete"]
    if oracle_complete:
        unexpected_count = len(actual_events) - len(matched_actual)
        if unexpected_count:
            failures.append(f"{unexpected_count} unexpected actual events")
    metrics = _case_metrics(
        expected_events,
        actual_events,
        matched_expected,
        matched_actual,
        oracle_complete=oracle_complete,
    )
    return {
        "case_index": case_index,
        "status": "fail" if failures else "pass",
        "failures": failures,
        "oracle_complete": oracle_complete,
        "visual_coverage_required": visual_coverage_required,
        "visual_coverage_complete": visual_coverage_complete,
        "metrics": metrics,
        "_family_source_pair": family_source_pair,
    }


def _gold_source_pair_key(result: DiffResult) -> tuple[str, str] | None:
    """Return an order-neutral key only for two different immutable sources."""

    provenance = result.provenance
    if provenance is None:
        return None
    old_sha256 = provenance.old_input.sha256
    new_sha256 = provenance.new_input.sha256
    if not old_sha256 or not new_sha256 or old_sha256 == new_sha256:
        return None
    return tuple(sorted((old_sha256, new_sha256)))


def _actual_events(
    payload: dict[str, Any],
    reader_blob: str | Mapping[str, str | _ReaderSurfaceEvidence],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for change in payload.get("changes", []):
        if change.get("role") != "technical":
            continue
        occurrence_counts: Counter[tuple[str, str]] = Counter()
        for pair in change.get("replaced_snippets", []):
            old_value = str(pair.get("old", ""))
            new_value = str(pair.get("new", ""))
            occurrence_counts[(old_value, new_value)] += 1
            events.append(
                _literal_event(
                    "text",
                    old_value,
                    new_value,
                    reader_blob,
                    location=str(change.get("report_location", "")),
                    scope_hints=(_change_reader_location(change),),
                    scope_key=_reader_scope_key(change),
                    occurrence_index=occurrence_counts[(old_value, new_value)],
                )
            )
        for value in change.get("removed_snippets", []):
            old_value = str(value)
            occurrence_counts[(old_value, "")] += 1
            events.append(
                _literal_event(
                    "text",
                    old_value,
                    "",
                    reader_blob,
                    location=str(change.get("report_location", "")),
                    scope_hints=(_change_reader_location(change),),
                    scope_key=_reader_scope_key(change),
                    occurrence_index=occurrence_counts[(old_value, "")],
                )
            )
        for value in change.get("added_snippets", []):
            new_value = str(value)
            occurrence_counts[("", new_value)] += 1
            events.append(
                _literal_event(
                    "text",
                    "",
                    new_value,
                    reader_blob,
                    location=str(change.get("report_location", "")),
                    scope_hints=(_change_reader_location(change),),
                    scope_key=_reader_scope_key(change),
                    occurrence_index=occurrence_counts[("", new_value)],
                )
            )
    for table in payload.get("table_changes", []):
        if table.get("role", "technical") != "technical":
            continue
        occurrence_counts = Counter()
        table_location = _table_event_location(table)
        row_changes = table.get("row_changes", [])
        if (
            table.get("caption_changed")
            or table.get("change_type") in {"added", "deleted", "review"}
            or not row_changes
        ):
            old_card = _table_side_event_literal(table, side="old")
            new_card = _table_side_event_literal(table, side="new")
            occurrence_counts[(old_card, new_card)] += 1
            events.append(
                _literal_event(
                    "table",
                    old_card,
                    new_card,
                    reader_blob,
                    location=table_location,
                    scope_hints=tuple(
                        value for value in (old_card, new_card) if value
                    ),
                    scope_key=_reader_scope_key(table),
                    occurrence_index=occurrence_counts[(old_card, new_card)],
                )
            )
        for row in row_changes:
            old_value = str(row.get("old_value", ""))
            new_value = str(row.get("new_value", ""))
            occurrence_counts[(old_value, new_value)] += 1
            events.append(
                _literal_event(
                    "table",
                    old_value,
                    new_value,
                    reader_blob,
                    location=table_location,
                    scope_hints=_table_scope_hints(table),
                    scope_key=_reader_scope_key(table),
                    occurrence_index=occurrence_counts[(old_value, new_value)],
                )
            )
    for formula_index, formula in enumerate(payload.get("formula_changes", []), start=1):
        old_formula = formula.get("old_formula") or {}
        new_formula = formula.get("new_formula") or {}
        formula_marker = f"F{formula_index}"
        events.append(
            _literal_event(
                "formula",
                str(old_formula.get("semantic_text", "")),
                str(new_formula.get("semantic_text", "")),
                reader_blob,
                location=_formula_event_location(old_formula, new_formula),
                scope_hints=(formula_marker,),
                scope_key=_reader_scope_key(formula, fallback=formula_marker),
            )
        )
    for visual_index, visual in enumerate(payload.get("visual_review_items", []), start=1):
        visual_marker = f"V{visual_index}."
        visibility = _literal_visibility(
            (visual_marker,),
            reader_blob,
            # A default-collapsed visual card remains reader-reachable through
            # the visible summary that names all V identifiers.  Unlike prose
            # and table literals, the visual event has no text fact to repeat
            # inside an open card, so the public summary is its HTML scope.
            scope_key="",
        )
        events.append(
            {
                "kind": "visual",
                "old_page": visual.get("old_page_number"),
                "new_page": visual.get("new_page_number"),
                "reader_visible": all(visibility.values()),
                "reader_visibility": visibility,
            }
        )
    return events


def _change_reader_location(change: dict[str, Any]) -> str:
    """Use the exact reader heading while retaining raw location for Gold matching."""

    return str(
        change.get("display_report_location")
        or change.get("report_location")
        or ""
    )


def _reader_scope_key(payload_item: dict[str, Any], *, fallback: str = "") -> str:
    """Distinguish legacy payloads from explicitly suppressed reader cards."""

    if "reader_card_id" in payload_item:
        value = payload_item.get("reader_card_id")
        return str(value) if value else "__suppressed__"
    return fallback


def _table_scope_hints(table: dict[str, Any]) -> tuple[str, ...]:
    """Return rendered table-card anchors shared by HTML, Markdown, and TXT."""

    hints = []
    for side in ("old", "new"):
        literal = _table_side_event_literal(table, side=side)
        if literal:
            hints.append(literal)
    return tuple(hints)


def _table_event_location(table: dict[str, Any]) -> str:
    """Build a stable title/page location accepted by Gold manifest substrings."""

    fragments: list[str] = []
    for side in ("old", "new"):
        titles = [str(value) for value in table.get(f"{side}_titles", []) if value]
        pages = [str(value) for value in table.get(f"{side}_pages", [])]
        if titles:
            fragments.append(f"{side} titles: {' | '.join(titles)}")
        if pages:
            fragments.append(f"{side} pages: {', '.join(pages)}")
    return "; ".join(fragments)


def _table_side_event_literal(table: dict[str, Any], *, side: str) -> str:
    """Mirror the stable table-side description rendered in every reader report."""

    titles = [str(value) for value in table.get(f"{side}_titles", []) if value]
    pages = [str(value) for value in table.get(f"{side}_pages", [])]
    if not pages:
        return "无对应表格"
    title_text = " / ".join(titles) if titles else "无表题续段"
    return f"{title_text}（页 {', '.join(pages)}）"


def _formula_event_location(
    old_formula: dict[str, Any],
    new_formula: dict[str, Any],
) -> str:
    """Build a stable page/formula-number location for repeated expressions."""

    fragments: list[str] = []
    for side, formula in (("old", old_formula), ("new", new_formula)):
        if not formula:
            continue
        page = formula.get("page_number")
        number = formula.get("formula_number")
        values = []
        if page is not None:
            values.append(f"page {page}")
        if number:
            values.append(f"formula {number}")
        if values:
            fragments.append(f"{side} " + " ".join(values))
    return "; ".join(fragments)


def _literal_event(
    kind: str,
    old: str,
    new: str,
    reader_blob: str | Mapping[str, str | _ReaderSurfaceEvidence],
    *,
    location: str = "",
    scope_hints: tuple[str, ...] = (),
    scope_key: str = "",
    occurrence_index: int = 1,
) -> dict[str, Any]:
    literals = [value for value in (old, new) if value]
    visibility = _literal_visibility(
        tuple(literals),
        reader_blob,
        scope_hints=tuple(value for value in scope_hints if value),
        scope_key=scope_key,
        occurrence_index=occurrence_index,
    )
    visible = bool(literals) and all(visibility.values())
    return {
        "kind": kind,
        "old": old,
        "new": new,
        "location": location,
        "reader_visible": visible,
        "reader_visibility": visibility,
    }


def _literal_visibility(
    literals: tuple[str, ...],
    reader_blob: str | Mapping[str, str | _ReaderSurfaceEvidence],
    *,
    scope_hints: tuple[str, ...] = (),
    scope_key: str = "",
    occurrence_index: int = 1,
) -> dict[str, bool]:
    """Require one concrete report card on every surface to carry the event."""

    surfaces = (
        {"combined": reader_blob}
        if isinstance(reader_blob, str)
        else dict(reader_blob)
    )
    visibility: dict[str, bool] = {}
    for name, surface in surfaces.items():
        structured_surface = isinstance(surface, _ReaderSurfaceEvidence)
        evidence = surface if structured_surface else _ReaderSurfaceEvidence(full_text=str(surface))
        keyed_blocks = evidence.blocks
        candidates = (
            tuple(block for _key, block in keyed_blocks)
            if structured_surface
            else (evidence.full_text,)
        )
        if structured_surface and not scope_key and not scope_hints:
            candidates = (evidence.full_text,)
        elif scope_key and keyed_blocks:
            candidates = tuple(
                block for key, block in keyed_blocks if key == scope_key
            )
        elif scope_hints and keyed_blocks:
            candidates = tuple(
                block
                for _key, block in keyed_blocks
                if all(_literal_matches(hint, block) for hint in scope_hints)
            )
        required_counts = Counter(literals)
        visibility[name] = bool(literals) and any(
            all(
                _literal_occurrences(value, block) >= count * occurrence_index
                for value, count in required_counts.items()
            )
            for block in candidates
        )
    return visibility


def _match_expected_events(
    expected_events: list[dict[str, Any]],
    actual_events: list[dict[str, Any]],
) -> tuple[set[int], set[int], list[str]]:
    matched_expected: set[int] = set()
    matched_actual: set[int] = set()
    failures: list[str] = []
    for expected_index, expected in enumerate(expected_events):
        candidate_indexes = [
            actual_index
            for actual_index, actual in enumerate(actual_events)
            if actual_index not in matched_actual and _event_matches(expected, actual)
        ]
        expected_occurrences = expected.get("occurrences", 1)
        if len(candidate_indexes) != expected_occurrences:
            failures.append(
                f"expected event {expected_index + 1} matched {len(candidate_indexes)} actual events; "
                f"expected {expected_occurrences}"
            )
            continue
        matched_expected.add(expected_index)
        matched_actual.update(candidate_indexes)
        if any(
            not _reader_visibility_matches(
                expected["reader_visible"],
                actual_events[actual_index],
            )
            for actual_index in candidate_indexes
        ):
            failures.append(f"expected event {expected_index + 1} reader visibility mismatch")
    return matched_expected, matched_actual, failures


def _event_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if expected["kind"] != actual["kind"]:
        return False
    if expected["kind"] == "visual":
        return (
            expected.get("old_page") == actual.get("old_page")
            and expected.get("new_page") == actual.get("new_page")
        )
    return all(
        not expected.get(side)
        or _literal_matches(str(expected[side]), str(actual.get(side, "")))
        for side in ("old", "new")
    ) and (
        not expected.get("location")
        or _literal_matches(
            str(expected["location"]),
            str(actual.get("location", "")),
        )
    )


def _reader_visibility_matches(expected_visible: bool, actual: dict[str, Any]) -> bool:
    """Require material facts on every surface and suppressed facts on none."""

    surface_states = actual.get("reader_visibility")
    if isinstance(surface_states, dict) and surface_states:
        values = [bool(value) for value in surface_states.values()]
        return all(values) if expected_visible else not any(values)
    return bool(actual.get("reader_visible")) is expected_visible


def _literal_matches(expected: str, actual: str) -> bool:
    """Match a case-sensitive normalized phrase at technical token boundaries."""

    pattern, normalized_actual = _literal_pattern_and_actual(expected, actual)
    if pattern is None:
        return True
    return re.search(pattern, normalized_actual) is not None


def _literal_occurrences(expected: str, actual: str) -> int:
    """Count non-overlapping case-sensitive technical phrases at safe boundaries."""

    pattern, normalized_actual = _literal_pattern_and_actual(expected, actual)
    if pattern is None:
        return 0
    return sum(1 for _match in re.finditer(pattern, normalized_actual))


def _literal_pattern_and_actual(expected: str, actual: str) -> tuple[str | None, str]:
    """Build the shared bounded literal pattern and normalized reader text."""

    normalized_expected = " ".join(expected.split())
    normalized_actual = " ".join(actual.split())
    if not normalized_expected:
        return None, normalized_actual
    pattern = re.escape(normalized_expected).replace(r"\ ", r"\s+")
    if normalized_expected[0].isdigit():
        pattern = r"(?<![\w.+\-−])" + pattern
    elif normalized_expected[0] in "+-−":
        pattern = r"(?<![\w.+\-−])" + pattern
    elif normalized_expected[0].isalnum() or normalized_expected[0] == "_":
        pattern = r"(?<![\w/\-])" + pattern
    if normalized_expected[-1].isdigit():
        pattern += r"(?![\w.])"
    elif normalized_expected[-1].isalnum() or normalized_expected[-1] == "_":
        pattern += r"(?![\w/\-])"
    return pattern, normalized_actual


class _VisibleHTMLTextParser(HTMLParser):
    """Collect rendered text while excluding scripts and collapsed detail bodies."""

    _BLOCK_TAGS = frozenset(
        {"p", "div", "li", "td", "th", "tr", "h1", "h2", "h3", "h4", "br", "summary"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tag_depth = 0
        self.closed_details_depth = 0
        self.summary_depth = 0
        self.blocks: list[tuple[str, list[str]]] = []
        self._active_blocks: list[int] = []
        self._tag_stack: list[tuple[str, int | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        attr_map = {key.casefold(): value for key, value in attrs}
        element_id = attr_map.get("id") or ""
        opened_block: int | None = None
        if re.fullmatch(
            r"(?:change|table-change|formula|visual-review)-\d+",
            element_id,
        ):
            opened_block = len(self.blocks)
            self.blocks.append((_html_reader_block_key(element_id), []))
            self._active_blocks.append(opened_block)
        if normalized not in {"br", "img", "meta", "link", "input", "hr"}:
            self._tag_stack.append((normalized, opened_block))
        if normalized in {"style", "script"}:
            self.hidden_tag_depth += 1
        elif normalized == "details" and not any(
            key.casefold() == "open" for key, _value in attrs
        ):
            self.closed_details_depth += 1
        elif normalized == "summary":
            self.summary_depth += 1
        elif normalized == "sub" and self._is_visible():
            self._append_visible("_{")
        elif normalized == "sup" and self._is_visible():
            self._append_visible("^{")
        if normalized == "br" and self._is_visible():
            self._append_visible(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        visible_before_close = self._is_visible()
        if normalized in {"sub", "sup"} and visible_before_close:
            self._append_visible("}")
        if normalized in {"style", "script"} and self.hidden_tag_depth:
            self.hidden_tag_depth -= 1
        elif normalized == "summary" and self.summary_depth:
            self.summary_depth -= 1
        elif normalized == "details" and self.closed_details_depth:
            self.closed_details_depth -= 1
        if visible_before_close and normalized in self._BLOCK_TAGS:
            self._append_visible(" ")
        while self._tag_stack:
            opened_tag, opened_block = self._tag_stack.pop()
            if opened_block is not None and opened_block in self._active_blocks:
                self._active_blocks.remove(opened_block)
            if opened_tag == normalized:
                break

    def handle_data(self, data: str) -> None:
        if self._is_visible():
            self._append_visible(data)

    def _append_visible(self, value: str) -> None:
        self.parts.append(value)
        for block_index in self._active_blocks:
            self.blocks[block_index][1].append(value)

    def _is_visible(self) -> bool:
        return self.hidden_tag_depth == 0 and (
            self.closed_details_depth == 0 or self.summary_depth > 0
        )


def _read_visible_html_text(path: Path) -> str:
    """Stream HTML reader text without retaining embedded base64 screenshots."""

    return _read_visible_html_evidence(path).full_text


def _read_visible_html_evidence(path: Path) -> _ReaderSurfaceEvidence:
    """Stream visible HTML and retain only compact per-card text scopes."""

    parser = _VisibleHTMLTextParser()
    for fragment in _iter_html_without_image_data(path):
        parser.feed(fragment)
    parser.close()
    return _ReaderSurfaceEvidence(
        full_text=" ".join("".join(parser.parts).split()),
        blocks=tuple(
            (key, compact)
            for key, parts in parser.blocks
            if (compact := " ".join("".join(parts).split()))
        ),
    )


def _read_markdown_evidence(path: Path) -> _ReaderSurfaceEvidence:
    """Read Markdown and split T/F/V/body cards by their stable headings."""

    text = path.read_text(encoding="utf-8")
    return _ReaderSurfaceEvidence(
        full_text=text,
        blocks=_split_reader_blocks(text, r"(?m)^### (?:T|F|V)?\d+\.\s"),
    )


def _read_text_evidence(path: Path) -> _ReaderSurfaceEvidence:
    """Read plain text and split the same numbered report cards."""

    text = path.read_text(encoding="utf-8")
    return _ReaderSurfaceEvidence(
        full_text=text,
        blocks=_split_reader_blocks(text, r"(?m)^(?:T|F|V)?\d+\.\s"),
    )


def _split_reader_blocks(
    text: str,
    marker_pattern: str,
) -> tuple[tuple[str, str], ...]:
    """Return compact card slices without treating section navigation as evidence."""

    matches = list(re.finditer(marker_pattern, text))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section_break = re.search(r"(?m)^##\s+[^#\n].*$", text[match.end() : end])
        if section_break:
            end = match.end() + section_break.start()
        block = " ".join(text[match.start() : end].split())
        if block:
            label_match = re.match(r"(?:###\s+)?((?:T|F|V)?\d+)\.", block)
            if label_match:
                label = label_match.group(1)
                key = f"C{label}" if label.isdigit() else label
                blocks.append((key, block))
    return tuple(blocks)


def _html_reader_block_key(element_id: str) -> str:
    """Map report DOM ids to the same compact card ids stored in JSON."""

    prefix, number = element_id.rsplit("-", 1)
    return {
        "change": f"C{number}",
        "table-change": f"T{number}",
        "formula": f"F{number}",
        "visual-review": f"V{number}",
    }[prefix]


def _iter_html_without_image_data(path: Path):
    """Yield HTML fragments while replacing data:image payloads before parsing."""

    marker = "data:image/"
    tail = ""
    skipping_payload = False
    with path.open("r", encoding="utf-8") as source:
        while chunk := source.read(64 * 1024):
            data = tail + chunk
            tail = ""
            while data:
                if skipping_payload:
                    quote_indexes = [
                        index for quote in ('"', "'")
                        if (index := data.find(quote)) >= 0
                    ]
                    if not quote_indexes:
                        data = ""
                        continue
                    end = min(quote_indexes)
                    yield data[end]
                    data = data[end + 1 :]
                    skipping_payload = False
                    continue
                marker_index = data.find(marker)
                if marker_index >= 0:
                    yield data[:marker_index] + "embedded-image"
                    data = data[marker_index + len(marker) :]
                    skipping_payload = True
                    continue
                keep = min(len(marker) - 1, len(data))
                if keep:
                    yield data[:-keep]
                    tail = data[-keep:]
                else:
                    yield data
                data = ""
    if tail and not skipping_payload:
        yield tail


def _case_metrics(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matched_expected: set[int],
    matched_actual: set[int],
    *,
    oracle_complete: bool,
) -> dict[str, Any]:
    critical_indexes = {index for index, event in enumerate(expected) if event["critical"]}
    visual_indexes = {index for index, event in enumerate(expected) if event["kind"] == "visual"}
    return {
        "expected_event_count": len(expected),
        "matched_expected_event_count": len(matched_expected),
        "actual_event_count": len(actual),
        "matched_actual_event_count": len(matched_actual),
        "false_negative_count": len(expected) - len(matched_expected),
        "recall": _ratio(len(matched_expected), len(expected)),
        "critical_expected_count": len(critical_indexes),
        "critical_matched_count": len(critical_indexes & matched_expected),
        "critical_recall": _ratio(len(critical_indexes & matched_expected), len(critical_indexes)),
        "visual_expected_count": len(visual_indexes),
        "visual_matched_count": len(visual_indexes & matched_expected),
        "visual_recall": _ratio(len(visual_indexes & matched_expected), len(visual_indexes)),
        "precision": _ratio(len(matched_actual), len(actual)) if oracle_complete else None,
    }


def _aggregate_metrics(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    executed = [case for case in case_results if "metrics" in case]
    sums = {
        key: sum(case["metrics"][key] for case in executed)
        for key in (
            "expected_event_count",
            "matched_expected_event_count",
            "actual_event_count",
            "matched_actual_event_count",
            "false_negative_count",
            "critical_expected_count",
            "critical_matched_count",
            "visual_expected_count",
            "visual_matched_count",
        )
    }
    all_complete = bool(executed) and all(case["oracle_complete"] for case in executed)
    return {
        **sums,
        "recall": _ratio(sums["matched_expected_event_count"], sums["expected_event_count"]),
        "critical_recall": _ratio(sums["critical_matched_count"], sums["critical_expected_count"]),
        "visual_recall": _ratio(sums["visual_matched_count"], sums["visual_expected_count"]),
        "precision": (
            _ratio(sums["matched_actual_event_count"], sums["actual_event_count"])
            if all_complete
            else None
        ),
        "precision_scope": "complete-oracles" if all_complete else "unavailable-incomplete-oracle",
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _manifest_failure(failures: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "fail",
        "counts": {"pass": 0, "fail": 1, "skip": 0},
        "metrics": {
            "recall": None,
            "critical_recall": None,
            "visual_recall": None,
            "precision": None,
            "false_negative_count": 0,
            "precision_scope": "unavailable-manifest-error",
        },
        "cases": [{"case_index": 0, "status": "fail", "failures": failures}],
    }
