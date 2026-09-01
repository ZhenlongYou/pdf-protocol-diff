"""Validate visual-mask fixture expectations without importing production code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _inside(point: tuple[int, int], rectangle: list[int]) -> bool:
    x, y = point
    left, top, right, bottom = rectangle
    return left <= x < right and top <= y < bottom


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+")
    args = parser.parse_args()
    for value in args.fixtures:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
        rectangles = payload["rectangles"]
        if not payload["expect_item"]:
            if rectangles:
                raise AssertionError("empty case must contain no rectangles")
            continue
        changed = tuple(payload["changed_probe"])
        clear = tuple(payload["clear_probe"])
        if not any(_inside(changed, rectangle) for rectangle in rectangles):
            raise AssertionError("changed probe is outside every material rectangle")
        if any(_inside(clear, rectangle) for rectangle in rectangles):
            raise AssertionError("clear probe overlaps a material rectangle")
    print("ORACLE_VISUAL_MASK_SCOPE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
