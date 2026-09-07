"""Run the candidate occurrence alignment through real PDF extraction.

The candidate report retains unresolved items and is separate from the desktop
report. It does not claim table/figure/scan correctness or replace that path.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import html
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from protocol_pdf_diff.evidence_alignment import align_evidence, evidence_from_extraction
from protocol_pdf_diff.pdf_extract import extract_pdf_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_pdf", type=Path)
    parser.add_argument("new_pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output directory to preserve previous evidence")
    started = time.perf_counter()
    old = evidence_from_extraction(extract_pdf_text(args.old_pdf))
    new = evidence_from_extraction(extract_pdf_text(args.new_pdf))
    extracted = time.perf_counter()
    result = align_evidence(old, new)
    ended = time.perf_counter()
    args.output.mkdir(parents=True)
    payload = {"schema_version": 1, "status": "candidate", "alignment": asdict(result),
               "old": asdict(old), "new": asdict(new),
               "timings": {"extraction_seconds": extracted - started, "alignment_seconds": ended - extracted}}
    (args.output / "evidence.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    lookup = {u.occurrence_id: u for document in (old, new) for u in document.units}
    labels = {"equal": "内容对应", "resegmented": "内容相同，分段不同", "added": "新增",
              "deleted": "删除", "modified": "对应范围内替换", "unresolved": "对应关系待确认"}
    rows = []
    for relation in result.relations:
        if relation.kind in {"equal", "resegmented"}:
            continue
        cells = []
        for ids in (relation.old_ids, relation.new_ids):
            cells.append("<td>" + "".join(f"<p><small>PDF 第 {lookup[i].page} 页</small><br>{html.escape(lookup[i].text)}</p>" for i in ids) + "</td>")
        rows.append(f"<tr><th colspan='2'>{labels[relation.kind]}</th></tr><tr>{''.join(cells)}</tr>")
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>来源对应关系候选报告</title>
<style>body{font:16px/1.6 system-ui;max-width:1200px;margin:40px auto;padding:0 24px;color:#243047}table{width:100%;border-collapse:collapse;table-layout:fixed}td,th{border:1px solid #d8dce3;padding:14px;text-align:left;vertical-align:top;overflow-wrap:anywhere}small{color:#64748b}th{background:#f3f5f8}</style>
<h1>来源对应关系候选报告</h1><p>用于验证跨页、分段变化及重复内容的对应关系。未确认项完整保留；本报告尚未覆盖图表与扫描内容的准确性验收，不能替代正式比较报告。</p>"""
    page += f"<p>旧版 {result.old_unit_count} 个来源单元，新版 {result.new_unit_count} 个；待确认 {result.unresolved_unit_count} 个。</p>"
    for label, document in (("旧版", old), ("新版", new)):
        if document.coverage_reasons:
            reasons = {"extraction_warnings_require_review": "解析过程中存在警告", "partial_page_coverage": "未覆盖全部页面",
                       "image_or_ocr_coverage_unproven": "图像或扫描文字尚未核验", "reading_order_unresolved": "阅读顺序尚未确定",
                       "page_furniture_unresolved": "页边文字归属尚未确定", "source_coordinates_missing": "原文与坐标文字尚未一一对应",
                       "no_comparable_source_units": "尚未取得可比较原文"}
            page += f"<p>{label}存在未完成的来源核验：{html.escape('；'.join(reasons.get(r, '来源信息待核验') for r in document.coverage_reasons))}。这部分内容不能据此确认无差异。</p>"
        if document.alternate_source_views:
            page += f"<details><summary>{label}尚未与整页原文对应的坐标文字（保留核验，不重复计数）</summary>"
            page += "".join(f"<p>PDF 第 {u.page} 页：{html.escape(u.text)}</p>" for u in document.alternate_source_views)
            page += "</details>"
    page += "<table><tr><th>旧版原文</th><th>新版原文</th></tr>" + "".join(rows) + "</table></html>"
    (args.output / "report.html").write_text(page)
    print(json.dumps({"status": "candidate", "unresolved_units": result.unresolved_unit_count, "seconds": ended - started}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
