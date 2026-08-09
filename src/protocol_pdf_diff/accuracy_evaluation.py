"""Quantified gold-corpus evaluation through the production PDF diff pipeline.

The manifest describes manually verified change events.  The evaluator runs the
same ``run_diff`` and ``write_reports`` entry points used by the application,
matches expected events against complete JSON audit facts, and separately checks
whether each event obeys its reader-visibility contract.  Precision is reported
only when every executed case declares an exhaustive oracle.
"""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import Any

from .compare import run_diff
from .models import DiffOptions
from .reporting import write_reports


_EVENT_KINDS = frozenset({"text", "table", "formula", "visual"})
_TOP_LEVEL_KEYS = frozenset({"schema_version", "cases"})
_CASE_KEYS = frozenset(
    {"id", "required", "old", "new", "options", "oracle_complete", "expected_events"}
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
    counts = {
        status: sum(case["status"] == status for case in case_results)
        for status in ("pass", "fail", "skip")
    }
    status = "fail" if counts["fail"] else ("pass" if counts["pass"] else "skip")
    return {
        "schema_version": 1,
        "status": status,
        "counts": counts,
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
        if not isinstance(case.get("required"), bool):
            failures.append(f"{location}.required must be a boolean")
        _validate_document(case.get("old"), f"{location}.old", failures)
        _validate_document(case.get("new"), f"{location}.new", failures)
        _validate_options(case.get("options", {}), f"{location}.options", failures)
        if not isinstance(case.get("oracle_complete"), bool):
            failures.append(f"{location}.oracle_complete must be a boolean")
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
        with TemporaryDirectory(prefix="pdf_diff_gold_") as report_root:
            outputs = write_reports(result, report_root, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            reader_blob = "\n".join(
                outputs[key].read_text(encoding="utf-8")
                for key in ("markdown", "html", "text")
            )
    except Exception as exc:
        return {
            "case_index": case_index,
            "status": "fail",
            "failures": [f"pipeline error: {type(exc).__name__}"],
        }
    actual_events = _actual_events(payload, reader_blob)
    expected_events = case["expected_events"]
    matched_expected, matched_actual, failures = _match_expected_events(
        expected_events,
        actual_events,
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
        "metrics": metrics,
    }


def _actual_events(payload: dict[str, Any], reader_blob: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for change in payload.get("changes", []):
        if change.get("role") != "technical":
            continue
        for pair in change.get("replaced_snippets", []):
            events.append(
                _literal_event(
                    "text",
                    pair.get("old", ""),
                    pair.get("new", ""),
                    reader_blob,
                    location=str(change.get("report_location", "")),
                )
            )
        for value in change.get("removed_snippets", []):
            events.append(
                _literal_event(
                    "text",
                    value,
                    "",
                    reader_blob,
                    location=str(change.get("report_location", "")),
                )
            )
        for value in change.get("added_snippets", []):
            events.append(
                _literal_event(
                    "text",
                    "",
                    value,
                    reader_blob,
                    location=str(change.get("report_location", "")),
                )
            )
    for table in payload.get("table_changes", []):
        for row in table.get("row_changes", []):
            events.append(
                _literal_event(
                    "table",
                    str(row.get("old_value", "")),
                    str(row.get("new_value", "")),
                    reader_blob,
                )
            )
    for formula in payload.get("formula_changes", []):
        old_formula = formula.get("old_formula") or {}
        new_formula = formula.get("new_formula") or {}
        events.append(
            _literal_event(
                "formula",
                str(old_formula.get("semantic_text", "")),
                str(new_formula.get("semantic_text", "")),
                reader_blob,
            )
        )
    visual_section_visible = "视觉漏检核对" in reader_blob
    for visual in payload.get("visual_review_items", []):
        events.append(
            {
                "kind": "visual",
                "old_page": visual.get("old_page_number"),
                "new_page": visual.get("new_page_number"),
                "reader_visible": visual_section_visible,
            }
        )
    return events


def _literal_event(
    kind: str,
    old: str,
    new: str,
    reader_blob: str,
    *,
    location: str = "",
) -> dict[str, Any]:
    literals = [value for value in (old, new) if value]
    visible = bool(literals) and all(value in reader_blob for value in literals)
    return {
        "kind": kind,
        "old": old,
        "new": new,
        "location": location,
        "reader_visible": visible,
    }


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
            actual_events[actual_index]["reader_visible"] != expected["reader_visible"]
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
        not expected.get(side) or expected[side] in actual.get(side, "")
        for side in ("old", "new")
    ) and (
        not expected.get("location")
        or expected["location"] in actual.get("location", "")
    )


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
