"""Validate visual-layout fixture expectations without importing production code."""

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
        if payload["expect_item"]:
            if payload["expected_preview_width"] != payload["width"]:
                raise AssertionError("preview must retain the source page width")
        expected_client = min(payload["source_width"], payload["container_width"])
        if payload["expected_client_width"] != expected_client:
            raise AssertionError("client width must be bounded by intrinsic width and container")
        if payload["mask_default_open"] is not False:
            raise AssertionError("technical mask must be collapsed by default")
    print("ORACLE_VISUAL_REVIEW_LAYOUT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
