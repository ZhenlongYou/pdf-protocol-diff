#!/usr/bin/env python3
"""Run privacy-safe PDF regression cases described by a Corpus v0 manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions, Section
from protocol_pdf_diff.reporting import write_reports


_STATES = frozenset({"reliable", "degraded", "indeterminate"})
_TOP_LEVEL_KEYS = frozenset({"schema_version", "cases"})
_CASE_KEYS = frozenset(
    {
        "id",
        "kind",
        "required",
        "certification_required",
        "description",
        "old",
        "new",
        "document",
        "expect",
    }
)
_DOCUMENT_KEYS = frozenset({"path", "start_page", "end_page"})
_EXPECT_KEYS = frozenset(
    {
        "state",
        "states",
        "max_technical_section_changes",
        "max_technical_table_changes",
        "must_find",
        "must_ignore",
        "must_review",
        "must_extract_old",
        "must_extract_new",
        "must_ignore_locations",
    }
)


def run_manifest(
    manifest_path: str | Path,
    *,
    corpus_root: str | Path | None = None,
    require_executed: bool = False,
) -> dict[str, Any]:
    """Run every case in a v1 manifest and return a JSON-serializable summary."""

    source = Path(manifest_path).expanduser().resolve()
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        detail = str(exc).replace(str(source), "<manifest>")
        return _manifest_failure_summary([f"cannot read manifest: {detail}"])
    validation_failures = validate_manifest(
        manifest,
        require_certification_oracles=require_executed,
    )
    if validation_failures:
        return _manifest_failure_summary(validation_failures)
    root = _resolve_corpus_root(source, corpus_root)
    case_results = [_run_case_safely(case, root) for case in manifest["cases"]]
    if require_executed:
        for index, (case, result) in enumerate(zip(manifest["cases"], case_results)):
            certification_required = case.get(
                "certification_required",
                case["kind"] == "pair",
            )  # 版本对默认属于准确度认证；不在本次本地语料范围的 pair 必须显式退出。
            if certification_required and result["status"] == "skip":
                reason = result.get("reason", "case was skipped")
                case_results[index] = {
                    "id": case["id"],
                    "status": "fail",
                    "failures": [f"认证必跑 case 未执行: {reason}"],
                }  # self-diff 通过不能掩盖真实版本对缺失；认证覆盖不足直接成为机器可读失败。
    counts = {
        status: sum(case["status"] == status for case in case_results)
        for status in ("pass", "fail", "skip")
    }
    if require_executed and counts["pass"] == 0 and counts["fail"] == 0:  # 认证模式下，全 skip 不能充当准确度成功证据。
        case_results.append(  # 追加独立机器可读失败项，不篡改每份 optional 文档原有 skip 事实。
            {
                "id": "<corpus-execution>",
                "status": "fail",
                "failures": ["没有执行任何 case；请提供 corpus 文件或检查 --corpus-root。"],
            }
        )
        counts["fail"] += 1  # 总体状态由新增认证失败项驱动为 fail。
    status = "fail" if counts["fail"] else ("pass" if counts["pass"] else "skip")
    return {
        "schema_version": 1,
        "status": status,
        "counts": counts,
        "cases": case_results,
    }


def validate_manifest(
    manifest: object,
    *,
    require_certification_oracles: bool = False,
) -> list[str]:
    """Return every schema error found in a Corpus v1 manifest."""

    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    failures: list[str] = []
    unknown_top_level = sorted(set(manifest) - _TOP_LEVEL_KEYS)
    if unknown_top_level:
        failures.append(
            "unsupported top-level keys: " + ", ".join(unknown_top_level)
        )
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        failures.append("schema_version must be exactly 1")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        failures.append("cases must be a JSON array")
        return failures
    if not cases:
        failures.append("cases must contain at least one case")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(case, dict):
            failures.append(f"{location} must be a JSON object")
            continue
        unknown_case_keys = sorted(set(case) - _CASE_KEYS)
        if unknown_case_keys:
            failures.append(
                f"{location} has unsupported keys: {', '.join(unknown_case_keys)}"
            )
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            failures.append(f"{location}.id must be a non-empty string")
        elif case_id in seen_ids:
            failures.append(f"{location}.id duplicates {case_id!r}")
        else:
            seen_ids.add(case_id)
        kind = case.get("kind")
        if not isinstance(kind, str) or kind not in {"pair", "self_diff"}:
            failures.append(f"{location}.kind must be 'pair' or 'self_diff'")
        required = case.get("required", False)
        if not isinstance(required, bool):
            failures.append(f"{location}.required must be a boolean")
        certification_required = case.get("certification_required")
        if certification_required is not None and not isinstance(
            certification_required,
            bool,
        ):
            failures.append(
                f"{location}.certification_required must be a boolean"
            )
        description = case.get("description")
        if description is not None and not isinstance(description, str):
            failures.append(f"{location}.description must be a string")
        if kind == "pair":
            if "document" in case:
                failures.append(f"{location}.document is not allowed for a pair case")
            _validate_document(case.get("old"), f"{location}.old", failures)
            _validate_document(case.get("new"), f"{location}.new", failures)
        elif kind == "self_diff":
            if "old" in case or "new" in case:
                failures.append(
                    f"{location}.old/new are not allowed for a self_diff case"
                )
            _validate_document(case.get("document"), f"{location}.document", failures)
        expect = case.get("expect", {})
        _validate_expect(expect, f"{location}.expect", failures)
        certification_required = case.get(
            "certification_required",
            kind == "pair",
        )
        if require_certification_oracles and certification_required:
            _validate_certification_expect(
                expect,
                f"{location}.expect",
                failures,
                require_positive_change_oracle=kind == "pair",
            )
    if require_certification_oracles and not any(
        isinstance(case, dict)
        and case.get("kind") == "pair"
        and case.get("certification_required", True) is True
        for case in cases
    ):
        failures.append(
            "manifest certification requires at least one certification_required pair"
        )  # self-diff 只能证明确定性，不能替代 old/new 差异识别证据。
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
        failures.append(f"{location}.path must be a non-empty relative path")
    else:
        path = Path(path_value)
        if path.is_absolute() or ".." in path.parts:
            failures.append(f"{location}.path must be a safe relative path")
        if path.suffix.casefold() != ".pdf":
            failures.append(f"{location}.path must name a PDF file")
    for page_key in ("start_page", "end_page"):
        page = document.get(page_key)
        if page is not None and (
            not isinstance(page, int) or isinstance(page, bool) or page < 1
        ):
            failures.append(f"{location}.{page_key} must be a positive integer")
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
    if not isinstance(expect, dict):
        failures.append(f"{location} must be a JSON object")
        return
    unknown = sorted(set(expect) - _EXPECT_KEYS)
    if unknown:
        failures.append(f"{location} has unsupported keys: {', '.join(unknown)}")
    state = expect.get("state")
    if state is not None and (
        not isinstance(state, str) or state not in _STATES
    ):
        failures.append(
            f"{location}.state must be reliable, degraded, or indeterminate"
        )
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
    if state is not None and states is not None:
        failures.append(f"{location} cannot define both state and states")
    for key in ("max_technical_section_changes", "max_technical_table_changes"):
        value = expect.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            failures.append(f"{location}.{key} must be a non-negative integer")
    for key in (
        "must_find",
        "must_ignore",
        "must_review",
        "must_extract_old",
        "must_extract_new",
        "must_ignore_locations",
    ):
        value = expect.get(key, [])
        if not isinstance(value, list) or any(
            not isinstance(anchor, str) or not anchor.strip() for anchor in value
        ):
            failures.append(f"{location}.{key} must be an array of non-empty strings")


def _validate_certification_expect(
    expect: object,
    location: str,
    failures: list[str],
    *,
    require_positive_change_oracle: bool,
) -> None:
    """Require an explicit quality state and one observable accuracy oracle."""

    if not isinstance(expect, dict):
        return  # 基础 schema 已经报告 expect 类型，避免重复噪声。
    if "state" not in expect and "states" not in expect:
        failures.append(
            f"{location} certification requires state or states"
        )
    anchor_keys = (
        "must_find",
        "must_ignore",
        "must_review",
        "must_extract_old",
        "must_extract_new",
        "must_ignore_locations",
    )
    has_anchor_gate = any(
        isinstance(expect.get(key), list)
        and any(isinstance(anchor, str) and anchor.strip() for anchor in expect[key])
        for key in anchor_keys
    )
    cap_keys = (
        "max_technical_section_changes",
        "max_technical_table_changes",
    )
    has_count_cap = any(
        isinstance(expect.get(key), int)
        and not isinstance(expect[key], bool)
        and expect[key] >= 0
        for key in cap_keys
    )
    if not has_anchor_gate and not has_count_cap:
        failures.append(
            f"{location} certification requires at least one independent accuracy gate "
            "(non-empty anchor/location gate or technical change-count cap)"
        )
    has_positive_change_oracle = (
        isinstance(expect.get("must_find"), list)
        and any(
            isinstance(anchor, str) and anchor.strip()
            for anchor in expect["must_find"]
        )
    )
    if require_positive_change_oracle and not has_positive_change_oracle:
        failures.append(
            f"{location} certification pair requires a non-empty positive must_find oracle"
        )  # 上限只能阻止 false-positive 洪泛，不能证明已找到任何已知差异。


def _manifest_failure_summary(failures: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "fail",
        "counts": {"pass": 0, "fail": 1, "skip": 0},
        "cases": [
            {
                "id": "<manifest>",
                "status": "fail",
                "failures": failures,
            }
        ],
    }


def _resolve_corpus_root(manifest_path: Path, corpus_root: str | Path | None) -> Path:
    configured = corpus_root or os.environ.get("PDF_DIFF_CORPUS_ROOT")
    return Path(configured).expanduser().resolve() if configured else manifest_path.parent


def _run_case_safely(case: dict[str, Any], corpus_root: Path) -> dict[str, Any]:
    try:
        return _run_case(case, corpus_root)
    except Exception as exc:  # One corrupt private input must not hide other case results.
        detail = str(exc).replace(str(corpus_root), "<corpus-root>")
        return {
            "id": case["id"],
            "status": "fail",
            "failures": [f"case execution error: {type(exc).__name__}: {detail}"],
        }


def _run_case(case: dict[str, Any], corpus_root: Path) -> dict[str, Any]:
    case_id = case["id"]
    descriptors = (
        (case["old"], case["new"])
        if case["kind"] == "pair"
        else (case["document"], case["document"])
    )
    paths = [_corpus_path(corpus_root, descriptor["path"]) for descriptor in descriptors]
    missing = list(
        dict.fromkeys(
            descriptor["path"]
            for descriptor, path in zip(descriptors, paths)
            if not path.is_file()
        )
    )
    if missing:
        required = case.get("required", False)
        failure = f"missing required PDF: {', '.join(missing)}"
        return {
            "id": case_id,
            "status": "fail" if required else "skip",
            "failures": [failure] if required else [],
            "reason": failure.replace("required ", "optional ") if not required else "",
        }

    options = DiffOptions(
        old_start_page=descriptors[0].get("start_page"),
        old_end_page=descriptors[0].get("end_page"),
        new_start_page=descriptors[1].get("start_page"),
        new_end_page=descriptors[1].get("end_page"),
    )
    with TemporaryDirectory(prefix="pdf_diff_corpus_") as temp_dir:
        result = run_diff(paths[0], paths[1], options)
        report_paths = write_reports(result, temp_dir, options)
        payload = json.loads(report_paths["json"].read_text(encoding="utf-8"))
        searchable = _combined_report_text(report_paths, payload)
        material_searchable = _material_report_text(payload)
        reviewable = _review_report_text(payload)
        source_texts = {  # 源抽取锚点只查对应版本全文，避免变化报告或文件名替漏抽取“作证”。
            "old": _section_source_text(result.old_sections),
            "new": _section_source_text(result.new_sections),
        }
        location_text = _section_location_text(  # 伪章节门禁只查结构位置，不被普通正文中的引用误触发。
            [*result.old_sections, *result.new_sections]
        )

    metrics = {
        "state": payload["assessment"]["state"],
        "technical_section_changes": sum(
            change.get("role") == "technical" for change in payload["changes"]
        ),
        "technical_table_changes": sum(
            change.get("role") == "technical" for change in payload["table_changes"]
        ),
    }
    failures = _expectation_failures(
        case.get("expect", {}),
        metrics,
        searchable,
        material_searchable=material_searchable,
        reviewable=reviewable,
        source_texts=source_texts,
        location_text=location_text,
    )
    return {
        "id": case_id,
        "status": "fail" if failures else "pass",
        "failures": failures,
        "metrics": metrics,
    }


def _corpus_path(corpus_root: Path, relative_path: str) -> Path:
    root = corpus_root.resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("PDF path escapes <corpus-root>")
    return resolved


def _combined_report_text(
    report_paths: dict[str, Path],
    json_payload: dict[str, Any],
) -> str:
    json_findings = json.dumps(
        {
            "changes": json_payload.get("changes", []),
            "table_changes": json_payload.get("table_changes", []),
        },
        ensure_ascii=False,
    )
    html_report = report_paths["html"].read_text(
        encoding="utf-8-sig", errors="replace"
    )
    markdown_report = report_paths["markdown"].read_text(
        encoding="utf-8-sig", errors="replace"
    )
    rendered_findings = [
        _text_after_marker(
            html_report,
            '<h2 class="section-heading" id="text-changes">',
        ),
        _text_after_marker(markdown_report, "## 技术正文变化"),
        report_paths["csv"].read_text(encoding="utf-8-sig", errors="replace"),
        report_paths["table_csv"].read_text(
            encoding="utf-8-sig", errors="replace"
        ),
    ]
    return "\n".join([json_findings, *rendered_findings]).casefold()


def _review_report_text(json_payload: dict[str, Any]) -> str:
    """Return only findings explicitly classified as reader review evidence."""

    section_reviews = [
        change
        for change in json_payload.get("changes", [])
        if change.get("change_type") == "review"
    ]
    table_reviews: list[dict[str, Any]] = []
    for change in json_payload.get("table_changes", []):
        review_rows = [
            row
            for row in change.get("row_changes", [])
            if row.get("change_type") == "需人工复核"
        ]
        if change.get("change_type") != "review" and not review_rows:
            continue
        table_reviews.append(
            {
                "old_titles": change.get("old_titles", []),
                "new_titles": change.get("new_titles", []),
                "row_changes": review_rows,
            }
        )
    return json.dumps(
        {"changes": section_reviews, "table_changes": table_reviews},
        ensure_ascii=False,
    ).casefold()


def _material_report_text(json_payload: dict[str, Any]) -> str:
    """Return only confirmed material findings for positive corpus oracles."""

    material_sections = [
        change
        for change in json_payload.get("changes", [])
        if change.get("change_type") not in {"review", "unchanged"}
        and change.get("role", "technical") == "technical"
    ]
    material_tables: list[dict[str, Any]] = []
    for change in json_payload.get("table_changes", []):
        if change.get("role", "technical") != "technical":
            continue
        material_rows = [
            row
            for row in change.get("row_changes", [])
            if row.get("change_type") not in {"需人工复核", "无变化"}
        ]
        table_change_type = change.get("change_type")
        caption_material = table_change_type != "review" and (
            bool(change.get("caption_changed"))
            or table_change_type in {"added", "deleted"}
        )
        if not material_rows and not caption_material:
            continue
        material_tables.append(
            {
                "change_type": table_change_type,
                "caption_changed": bool(change.get("caption_changed")),
                "old_titles": change.get("old_titles", []),
                "new_titles": change.get("new_titles", []),
                "row_changes": material_rows,
            }
        )
    return json.dumps(
        {"changes": material_sections, "table_changes": material_tables},
        ensure_ascii=False,
    ).casefold()


def _text_after_marker(value: str, marker: str) -> str:
    """Exclude report headers/provenance while preserving rendered findings."""

    _prefix, separator, findings = value.partition(marker)
    return findings if separator else ""


def _section_source_text(sections: list[Section]) -> str:
    """Return a case-insensitive source surface without report metadata."""

    fields: list[str] = []
    for section in sections:
        fields.extend((section.heading, section.body))  # 直接使用未截断正文，排除路径、哈希与报告说明。
    return "\n".join(fields).casefold()


def _section_location_text(sections: list[Section]) -> str:
    """Return only structural section locations from both input versions."""

    locations = [section.location for section in sections]  # 全章节位置可发现未进入变化列表的伪标题。
    return "\n".join(locations).casefold()


def _expectation_failures(
    expect: dict[str, Any],
    metrics: dict[str, Any],
    searchable: str,
    *,
    material_searchable: str | None = None,
    reviewable: str,
    source_texts: dict[str, str],
    location_text: str,
) -> list[str]:
    failures: list[str] = []
    searchable_surface = _normalized_anchor_surface(searchable)
    material_surface = _normalized_anchor_surface(
        material_searchable if material_searchable is not None else searchable
    )
    review_surface = _normalized_anchor_surface(reviewable)
    source_surfaces = {
        side: _normalized_anchor_surface(value)
        for side, value in source_texts.items()
    }
    location_surface = _normalized_anchor_surface(location_text)
    expected_state = expect.get("state")
    if expected_state is not None and metrics["state"] != expected_state:
        failures.append(
            f"state expected {expected_state!r}, got {metrics['state']!r}"
        )
    expected_states = expect.get("states")
    if expected_states is not None and metrics["state"] not in expected_states:
        failures.append(
            f"state expected one of {expected_states!r}, got {metrics['state']!r}"
        )
    for field, metric in (
        ("max_technical_section_changes", "technical_section_changes"),
        ("max_technical_table_changes", "technical_table_changes"),
    ):
        maximum = expect.get(field)
        if maximum is not None and metrics[metric] > maximum:
            failures.append(f"{metric} expected <= {maximum}, got {metrics[metric]}")
    for anchor in expect.get("must_find", []):
        if _normalized_anchor_surface(anchor) not in material_surface:
            failures.append(f"must_find anchor absent: {anchor!r}")
    for anchor in expect.get("must_ignore", []):
        if _normalized_anchor_surface(anchor) in searchable_surface:
            failures.append(f"must_ignore anchor present: {anchor!r}")
    for anchor in expect.get("must_review", []):
        if _normalized_anchor_surface(anchor) not in review_surface:
            failures.append(f"must_review anchor absent: {anchor!r}")
    for side in ("old", "new"):
        field = f"must_extract_{side}"
        for anchor in expect.get(field, []):
            if _normalized_anchor_surface(anchor) not in source_surfaces[side]:
                failures.append(f"{field} anchor absent: {anchor!r}")
    for anchor in expect.get("must_ignore_locations", []):
        if _normalized_anchor_surface(anchor) in location_surface:
            failures.append(f"must_ignore_locations anchor present: {anchor!r}")
    return failures


def _normalized_anchor_surface(value: str) -> str:
    """Collapse PDF/report line-wrap whitespace for literal anchor matching."""

    return re.sub(r"\s+", " ", value).strip().casefold()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument(
        "--require-executed",
        action="store_true",
        help="准确性认证模式：认证必跑 case 未执行或所有 case 均 skip 时返回失败。",
    )  # pair 默认是认证必跑；可用 certification_required=false 显式排除不在本次 corpus 的版本对。
    args = parser.parse_args(argv)
    summary = run_manifest(  # 把 CLI 的认证选择传到统一 runner，确保 stdout/JSON/退出码一致。
        args.manifest,
        corpus_root=args.corpus_root,
        require_executed=args.require_executed,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    return 1 if summary["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
