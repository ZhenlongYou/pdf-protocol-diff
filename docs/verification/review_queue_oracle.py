"""Validate frozen review-queue expectations without importing product code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+")
    args = parser.parse_args()
    for value in args.fixtures:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
        expected = payload["expected"]
        counts = expected["counts"]
        if set(counts) != {"detected", "review", "coverage"}:
            raise AssertionError("ORACLE_QUEUE_STATUS_KEYS")
        if any(not isinstance(value, int) or value < 0 for value in counts.values()):
            raise AssertionError("ORACLE_QUEUE_STATUS_VALUE")
        if expected["detected_facts"] < 0:
            raise AssertionError("ORACLE_QUEUE_FACT_VALUE")
        if len(expected["ids"]) != sum(counts.values()):
            raise AssertionError("ORACLE_QUEUE_ACTION_COUNT")
        if any(not identifier[:1] in {"C", "T", "V", "U"} for identifier in expected["ids"]):
            raise AssertionError("ORACLE_QUEUE_IDENTIFIER")
    print("ORACLE_REVIEW_QUEUE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
