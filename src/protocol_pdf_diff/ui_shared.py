"""Small non-visual contracts shared by the legacy and WebView shells."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "ProtocolPdfDiffReports"


def parse_optional_page(value: str, label: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        page = int(stripped)
    except ValueError as exc:
        raise ValueError(f"{label} 必须是正整数。") from exc
    if page < 1:
        raise ValueError(f"{label} 必须大于等于 1。")
    return page


def page_range_values(
    mode: str, start_value: str, end_value: str, label: str
) -> tuple[int | None, int | None]:
    if mode == "all":
        return None, None
    if mode != "range":
        raise ValueError(f"{label}页码模式无效。")
    start = parse_optional_page(start_value, f"{label}起始页")
    end = parse_optional_page(end_value, f"{label}终止页")
    if start is None or end is None:
        raise ValueError(f"{label}指定范围时必须填写起始页和终止页。")
    if start > end:
        raise ValueError(f"{label}起始页不能大于终止页。")
    return start, end


def parse_positive_float(value: str, label: str) -> float:
    try:
        number = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} 必须是数字。") from exc
    if not math.isfinite(number) or not 0.0 < number <= 1.0:
        raise ValueError(f"{label} 必须大于 0；允许区间为 0 到 1（含 1），且必须是有限数字。")
    return number


def parse_positive_int(value: str, label: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} 必须是整数。") from exc
    if number < 0:
        raise ValueError(f"{label} 不能小于 0。")
    return number


def open_path(path: Path) -> bool:
    resolved = str(path.resolve())
    if sys.platform == "darwin":
        return subprocess.run(["open", resolved], check=False).returncode == 0
    if os.name == "nt":
        os.startfile(resolved)  # type: ignore[attr-defined]
        return True
    return subprocess.run(["xdg-open", resolved], check=False).returncode == 0


@dataclass(frozen=True)
class ReaderReportSummary:
    """Counts already filtered into the human-facing report."""

    body_changes: int
    modified: int
    added: int
    deleted: int
    table_changes: int
    visual_items: int


def reported_reader_summary(
    outputs: Mapping[str, Path],
) -> ReaderReportSummary | None:
    """Read the Markdown summary so UI and report use one reader-facing total."""

    markdown_path = outputs.get("markdown")
    if markdown_path is None:
        return None
    try:
        lines = markdown_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    values: dict[str, str] = {}
    in_summary = False
    for line in lines:
        if line.strip() == "## 汇总":
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            break
        if not in_summary:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2:
            values[cells[0]] = cells[1]
    try:
        modified, added, deleted = (
            int(value.strip())
            for value in values["章节修改 / 新增 / 删除"].split("/")
        )
        summary = ReaderReportSummary(
            body_changes=int(
                values["正文差异候选（章节）"]
                if "正文差异候选（章节）" in values
                else values["核心技术变化"]
            ),
            modified=modified,
            added=added,
            deleted=deleted,
            table_changes=int(values["变化表格"]),
            visual_items=int(values["视觉漏检核对项"]),
        )
        if any(value < 0 for value in summary.__dict__.values()):
            return None
        return summary
    except (KeyError, ValueError):
        return None
