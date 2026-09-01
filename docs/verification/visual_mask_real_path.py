"""Generate and reopen a report that contains distant visual-only changes."""

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

MASK_PATTERN = re.compile(r'<img src="data:image/png;base64,([^"]+)" alt="V1 差异掩膜">')


def main() -> int:
    pages = [["1 Scope", "The visual evidence text remains identical between revisions."]]
    with tempfile.TemporaryDirectory(prefix="visual-mask-real-path-") as temp_dir:
        root = Path(temp_dir)
        old_pdf = write_multipage_text_pdf(root / "old.pdf", pages)
        new_pdf = write_multipage_text_pdf(
            root / "new.pdf",
            pages,
            decorative_marks={1: "distant"},
        )
        result = run_diff(old_pdf, new_pdf, DiffOptions())
        outputs = write_reports(result, root / "reports", DiffOptions())
        html = outputs["html"].read_text(encoding="utf-8")
        match = MASK_PATTERN.search(html)
        if match is None:
            raise AssertionError("V1_MASK_MISSING")
        with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as image:
            colors = {
                color: count
                for count, color in (
                    image.convert("RGB").getcolors(maxcolors=image.width * image.height) or []
                )
            }
        outline_pixels = colors.get((185, 28, 28), 0)
        if outline_pixels:
            raise AssertionError("UNCHANGED_PIXEL_MARKED")

    artifact = PROJECT_ROOT / "docs" / "verification" / "out" / "visual-mask-real-path.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {"visual_card_count": len(result.visual_review_items), "union_outline_pixels": 0},
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    print("REAL_REPORT_PATH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
