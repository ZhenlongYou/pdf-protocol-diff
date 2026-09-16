"""Hand-authored expectations for the near-one numeric folding contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    count = 0
    for raw_path in paths:
        document = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        for case in document["cases"]:
            assert case["expected_main"] == 1, case["id"]
            assert case["expected_appendix"] == 0, case["id"]
            assert float(case["pair_similarity"]) <= 1.0, case["id"]
            assert str(case["old"]) != str(case["new"]), case["id"]
            count += 1
    assert count > 0
    print("PROSE_NUMERIC_NEAR_ONE_FOLD_ORACLE_OK", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
