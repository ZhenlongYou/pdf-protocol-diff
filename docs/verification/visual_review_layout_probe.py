"""Exercise full-width, no-upscale visual-review layout contracts."""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.visual_preview import VISUAL_REVIEW_IMAGE_CSS
from protocol_pdf_diff.visual_watchdog import _compare_page_images

TEST_ID = "VISUAL_REVIEW_LAYOUT_TEST"


def _check_crop(path: Path) -> None:
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
            raise AssertionError("EMPTY_CHANGE_CREATED_PREVIEW")
        return
    if item is None or item.old_image_data_uri is None:
        raise AssertionError("MATERIAL_CHANGE_MISSING")
    encoded = item.old_image_data_uri.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as preview:
        if preview.width != int(payload["expected_preview_width"]):
            raise AssertionError("HORIZONTAL_CONTEXT_CROPPED")


def _check_scale() -> None:
    compact = "".join(VISUAL_REVIEW_IMAGE_CSS.split())
    if "width:auto;" not in compact or "max-width:100%;" not in compact:
        raise AssertionError("VISUAL_REVIEW_UPSCALED")
    if re.search(r"(?<!max-)width:100%;", compact):
        raise AssertionError("VISUAL_REVIEW_UPSCALED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("crop", "scale", "all"), default="all")
    parser.add_argument("fixtures", nargs="*")
    args = parser.parse_args()
    print(TEST_ID)
    try:
        if args.check in {"crop", "all"}:
            for value in args.fixtures:
                _check_crop(Path(value))
        if args.check in {"scale", "all"}:
            _check_scale()
    except (AssertionError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1
    print("VISUAL_REVIEW_LAYOUT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
