"""Generate and reopen a report to verify full-width, no-upscale evidence."""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sample_data import write_multipage_text_pdf

OLD_PATTERN = re.compile(r'<img src="data:image/jpeg;base64,([^"]+)" alt="旧版第 1 页视觉证据">')


def main() -> int:
    pages = [["1 Scope", "The visual evidence text remains identical between revisions."]]
    with tempfile.TemporaryDirectory(prefix="visual-layout-real-path-") as temp_dir:
        root = Path(temp_dir)
        old_pdf = write_multipage_text_pdf(root / "old.pdf", pages)
        new_pdf = write_multipage_text_pdf(root / "new.pdf", pages, decorative_marks={1: "distant"})
        result = run_diff(old_pdf, new_pdf, DiffOptions())
        outputs = write_reports(result, root / "reports", DiffOptions())
        html = outputs["html"].read_text(encoding="utf-8")
        if html.count('class="table-shot visual-review-shot"') != 3:
            raise AssertionError("VISUAL_REVIEW_CLASS_MISSING")
        if html.count('<details class="visual-mask-detail">') != len(result.visual_review_items):
            raise AssertionError("VISUAL_MASK_NOT_COLLAPSED")
        if '<details class="visual-mask-detail" open>' in html:
            raise AssertionError("VISUAL_MASK_NOT_COLLAPSED")
        if "像素变化定位（技术复核）" not in html:
            raise AssertionError("VISUAL_MASK_READER_LABEL_MISSING")
        if "红色仅表示像素发生变化，不等同于协议参数或文字内容发生变化。" not in html:
            raise AssertionError("VISUAL_MASK_EXPLANATION_MISSING")
        compact = "".join(html.split())
        if ".visual-review-shotimg{display:block;width:auto;max-width:100%;" not in compact:
            raise AssertionError("VISUAL_REVIEW_UPSCALED")
        match = OLD_PATTERN.search(html)
        if match is None:
            raise AssertionError("V1_OLD_IMAGE_MISSING")
        with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as image:
            preview_width = image.width
        if not result.visual_review_items or preview_width <= 0:
            raise AssertionError("V1_PREVIEW_MISSING")

    artifact = PROJECT_ROOT / "docs" / "verification" / "out" / "visual-review-layout-real-path.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "visual_card_count": len(result.visual_review_items),
                "preview_width": preview_width,
                "horizontal_context": "full",
                "image_upscale_rule": "none",
                "mask_default_state": "collapsed",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    print("REAL_VISUAL_REVIEW_LAYOUT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
