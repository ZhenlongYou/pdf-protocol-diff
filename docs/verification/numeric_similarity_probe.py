"""Exercise the reader report contract for near-one numeric section changes."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from protocol_pdf_diff.models import DiffOptions, DiffResult, Section, SectionChange, SnippetPair
from protocol_pdf_diff.reporting import write_reports


def _section(section_id: str, body: str) -> Section:
    return Section(
        section_id,
        "32.3.1.7 Transmitter output jitter",
        "Transmitter output jitter",
        3,
        ("32.3.1.7 Transmitter output jitter",),
        ("32.3.1.7",),
        17,
        17,
        body,
    )


def _run_case(case: dict[str, object], root: Path) -> dict[str, object]:
    old_body = str(case["old"])
    new_body = str(case["new"])
    old_section = _section("old", old_body)
    new_section = _section("new", new_body)
    change = SectionChange(
        "modified",
        old_section,
        new_section,
        float(case["pair_similarity"]),
        replaced_snippets=[SnippetPair(old_body, new_body)],
    )
    result = DiffResult(
        Path("old.pdf"),
        Path("new.pdf"),
        [old_section],
        [new_section],
        [change],
        [],
    )
    paths = write_reports(result, root / str(case["id"]), DiffOptions(visual_watchdog=False))
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    main = payload["content_changes"]
    appendix = payload["similarity_review_changes"]
    expected_main = int(case["expected_main"])
    expected_appendix = int(case["expected_appendix"])
    if len(main) != expected_main or len(appendix) != expected_appendix:
        raise AssertionError(
            f"{case['id']}: main={len(main)} appendix={len(appendix)} "
            f"expected={expected_main}/{expected_appendix}"
        )
    if expected_main:
        observed = main[0]
        if observed["critical_content_equal"] is not False:
            raise AssertionError(f"{case['id']}: critical content was treated as equal")
        if observed["content_similarity"] >= 1.0:
            raise AssertionError(f"{case['id']}: content similarity did not expose the edit")
        html = paths["html"].read_text(encoding="utf-8")
        if "26450" not in html or "26560" not in html:
            raise AssertionError(f"{case['id']}: numeric values missing from HTML")
    return {
        "id": case["id"],
        "main": len(main),
        "appendix": len(appendix),
        "content_similarity": main[0]["content_similarity"] if main else None,
        "critical_content_equal": main[0]["critical_content_equal"] if main else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+")
    parser.add_argument("--artifact")
    args = parser.parse_args(argv)
    print("PROSE_NUMERIC_NEAR_ONE_FOLD_TEST", flush=True)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(args.artifact).parent / "reports" if args.artifact else Path(temporary)
            observations = []
            for fixture in args.fixtures:
                cases = json.loads(Path(fixture).read_text(encoding="utf-8"))["cases"]
                observations.extend(_run_case(case, root) for case in cases)
            if args.artifact:
                artifact = Path(args.artifact)
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        print("PROSE_NUMERIC_NEAR_ONE_FOLD_CONTRACT_FAIL", str(error)[:1500])
        return 1
    print("PROSE_NUMERIC_NEAR_ONE_FOLD_CONTRACT_OK", len(observations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
