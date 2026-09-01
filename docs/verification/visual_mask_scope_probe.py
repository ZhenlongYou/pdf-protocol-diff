"""Exercise the report preview against identity-bound visual-mask fixtures."""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.visual_watchdog import _compare_page_images

TEST_ID = "VISUAL_MASK_SCOPE_TEST"


def _run_fixture(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    size = (int(payload["width"]), int(payload["height"]))
    old_image = Image.new("RGB", size, "white")
    new_image = Image.new("RGB", size, "white")
    for rectangle in payload["rectangles"]:
        new_image.paste((0, 0, 0), tuple(int(value) for value in rectangle))

    item = _compare_page_images(
        old_image,
        new_image,
        old_page_number=1,
        new_page_number=1,
        alignment_method="same-page-text",
    )
    if not payload["expect_item"]:
        if item is not None:
            raise AssertionError("EMPTY_MASK_CREATED_ITEM")
        return
    if item is None or item.diff_image_data_uri is None:
        raise AssertionError("MATERIAL_CHANGE_MISSING")

    encoded = item.diff_image_data_uri.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as preview:
        rendered = preview.convert("RGB")
        changed = tuple(int(value) for value in payload["changed_probe"])
        clear = tuple(int(value) for value in payload["clear_probe"])
        if rendered.getpixel(changed) != (220, 38, 38):
            raise AssertionError("MATERIAL_PIXEL_NOT_MARKED")
        if rendered.getpixel(clear) != (255, 255, 255):
            raise AssertionError("UNCHANGED_PIXEL_MARKED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+")
    args = parser.parse_args()
    print(TEST_ID)
    try:
        for value in args.fixtures:
            _run_fixture(Path(value))
    except (AssertionError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1
    print("VISUAL_MASK_SCOPE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
