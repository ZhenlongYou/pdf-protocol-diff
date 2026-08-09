#!/usr/bin/env python3
"""Run the quantified gold-corpus evaluator and print a JSON summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from protocol_pdf_diff.accuracy_evaluation import run_gold_accuracy_evaluation


def parse_args() -> argparse.Namespace:
    """Parse explicit local corpus inputs without discovering private files."""

    parser = argparse.ArgumentParser(
        description="Evaluate PDF diff precision/recall against a manually verified gold manifest."
    )
    parser.add_argument("manifest", help="Gold accuracy manifest JSON")
    parser.add_argument(
        "--corpus-root",
        help="Root containing manifest-relative PDFs; defaults to the manifest directory",
    )
    parser.add_argument(
        "--output-json",
        help="Optional summary path; source text and PDF paths are never copied into it",
    )
    return parser.parse_args()


def main() -> int:
    """Run the production diff path and return nonzero for any failed gold case."""

    args = parse_args()
    summary = run_gold_accuracy_evaluation(
        args.manifest,
        corpus_root=args.corpus_root,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output_json:
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
