"""小型 PDF 反例经过生产比较和报告出口；不读取或比较整本私有 PDF。

运行：.venv/bin/python tools/verify_adaptability_slices.py --output work/adaptability-20261007
保留报告供目视验收，检查结束后删除 output 目录；生成输入可由本文件重建。
"""

import argparse
import json
import os
import sys
import time
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 从真实项目加载修改后的源码，不依赖启动目录。
sys.path.insert(0, str(ROOT / "src"))
import fitz
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.table_view_transaction import run_diff_transaction, report_outcome


def make_scan(path, lines):
    """用已知原文生成栅格 PDF，确保产品必须经过真实 OCR。"""
    with fitz.open() as source:
        page = source.new_page(width=612, height=792)
        for index, line in enumerate(lines):
            page.insert_text((55, 80 + index * 40), line, fontsize=14)
        pixels = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
        with fitz.open() as output:
            output.new_page(width=612, height=792).insert_image(page.rect, stream=pixels)
            output.save(path)  # 输出只有图片，无隐藏文字层。



def run_manifest(path, output):
    """独立来源先给出预期，产品运行后按章节、原页和具体变化检查。"""
    records = []
    for case in json.loads(path.read_text()):
        if not any(case.get(key) for key in ("replacements", "no_material_change", "table_review_contains", "reader_contains")):
            raise ValueError("真实切片必须提供至少一项事先定义的预期，不能把成功运行记为 PASS。")
        windows = {side: case[side + "_pages"] for side in ("old", "new")}
        if any(len(v) != 2 or v[0] < 1 or not 0 <= v[1]-v[0] <= 2 for v in windows.values()):
            raise ValueError("真实样本每侧必须是连续1至3页，禁止隐式整本运行。")
        for side in ("old", "new"):
            expected_hash = case.get(side + "_sha256")
            if expected_hash:
                digest = sha256()
                with Path(case[side]).open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected_hash:
                    raise ValueError(f"{case['name']} {side}: 输入哈希与预期来源不一致。")
        options = DiffOptions(old_start_page=windows["old"][0], old_end_page=windows["old"][1],
                              new_start_page=windows["new"][0], new_end_page=windows["new"][1],
                              ocr_time_budget_seconds=30)
        started = time.monotonic()
        outcome = report_outcome(run_diff_transaction(case["old"], case["new"], options), output/case["name"], options)
        payload = json.loads(outcome.outputs["json"].read_text())
        failures = []
        for expected in case.get("replacements", []):
            found = [c for c in payload["content_changes"] if expected["section"] in (c.get("new_location") or "")
                     and c.get("old_pages") == expected["old_page"] and c.get("new_pages") == expected["new_page"]
                     and any(expected["old"].replace(" ", "") in p["old"].replace(" ", "")
                             and expected["new"].replace(" ", "") in p["new"].replace(" ", "")
                             for p in c.get("replaced_snippets", []))]
            if len(found) != 1:
                failures.append("replacement: " + expected["section"])
        if case.get("no_material_change") and any(c["change_type"] not in {"review", "unchanged"}
                for c in [*payload["content_changes"], *payload["content_table_changes"]]):
            failures.append("unexpected material change")
        table_text = json.dumps(payload["content_table_changes"], ensure_ascii=False)
        for text in case.get("table_review_contains", []):
            if text not in table_text:
                failures.append("table review: " + text)
        for text in case.get("reader_contains", []):
            if text not in json.dumps(payload["content_changes"], ensure_ascii=False):
                failures.append("reader: " + text)
        record = dict(case=case["name"], status="FAIL" if failures else "PASS", failures=failures,
                      seconds=round(time.monotonic()-started,2), html=str(outcome.outputs["html"]),
                      expected_events=len(case.get("replacements", [])),
                      content_changes=len(payload["content_changes"]), tables=len(payload["content_table_changes"]),
                      coverage_items=len(payload["coverage_review_items"]),
                      scope="只验证清单列明的事件和反例，不代表整页完整标注或整本准确率")
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    if any(r["status"] == "FAIL" for r in records):
        (output/"failed-local-cases.json").write_text(json.dumps(records,ensure_ascii=False,indent=2))
        raise AssertionError("真实切片存在未满足的预期，见 failed-local-cases.json")
    return records


def run_region_controls(output):
    """局部截图改字与正文/矢量图同时变化，均经过公开事务和报告入口。"""
    records = []
    for case in ("mixed", "graphic"):
        directory = output / case
        directory.mkdir()
        for side, value in (("old", "3.3"), ("new", "5.5")):
            with fitz.open() as pdf:
                page = pdf.new_page(width=612, height=792)
                page.insert_text((55, 70), "1 Scope", fontsize=15)
                body_value = value if case == "graphic" else "3.3"
                page.insert_text((55, 105), "The controller shall use a limit of " + body_value + " volts.", fontsize=12)
                if case == "mixed":
                    with fitz.open() as picture:
                        image_page = picture.new_page(width=350, height=70)
                        image_page.insert_text((10, 40), "Image threshold shall be " + value + " V.", fontsize=15)
                        page.insert_image(fitz.Rect(65, 220, 415, 290),
                                          stream=image_page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes("png"))
                else:
                    page.draw_circle((200, 260), 45, fill=(0, 0, 1) if side == "old" else (1, 0, 0))
                    page.insert_text((140, 326), "Figure 1. Receiver path", fontsize=11)
                pdf.save(directory / (side + ".pdf"))
        options = DiffOptions(ocr_language="eng", ocr_time_budget_seconds=30)
        started = time.monotonic()
        outcome = report_outcome(run_diff_transaction(directory/"old.pdf", directory/"new.pdf", options),
                                 directory/"reports", options)
        payload = json.loads(outcome.outputs["json"].read_text())
        if case == "mixed":
            changes = payload["content_changes"]
            if (len(changes) != 1 or changes[0]["change_type"] != "review"
                    or "图像区域" not in (changes[0].get("old_location") or "")
                    or not all(value in json.dumps(changes) for value in ("3.3 V", "5.5 V"))):
                raise AssertionError("局部截图变化应单独复核，且不得制造原生正文变化。")
        else:
            audit = payload["provenance"]["visual_watchdog_run"]
            regions = audit["checked_graphic_regions"]
            if len(regions) != 1 or not regions[0]["changed"] or audit["complete"]:
                raise AssertionError("固定图示应提示变化，但不能把局部检查计为整页覆盖。")
            if not payload["content_changes"]:
                raise AssertionError("图示检查不能覆盖或丢失原生正文变化。")
        record = dict(case=case, status="PASS", seconds=round(time.monotonic()-started, 2),
                      html=str(outcome.outputs["html"]), scope="受控区域反例，不代表一般图形语义准确率")
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="可选的本地真实页窗清单；每侧最多3页")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)  # 不覆盖其他任务或之前未核对的输出。
    os.environ["PROTOCOL_PDF_DIFF_OCR_CACHE"] = "off"  # 本验收必须真正调用识别器。
    base = ["1 Scope", "The receiver shall preserve every requirement."]
    samples = {
        "scan-occurrence": (base + ["Call ENABLE", "Call enable"], base + ["Call ENABLE"]),
        "scan-repeat": (base + ["The output shall remain enabled."] * 2,
                        base + ["The output shall remain enabled."]),
    }
    results = []
    for name, (old_lines, new_lines) in samples.items():
        directory = args.output / name
        directory.mkdir()
        old, new = directory / "old.pdf", directory / "new.pdf"
        make_scan(old, old_lines)
        make_scan(new, new_lines)
        options = DiffOptions(ocr_language="eng", ocr_time_budget_seconds=30)
        started = time.monotonic()
        outcome = report_outcome(run_diff_transaction(old, new, options), directory / "reports", options)
        payload = json.loads(outcome.outputs["json"].read_text())
        changes = payload.get("content_changes", [])
        expected = "Call enable" if name == "scan-occurrence" else "The output shall remain enabled."
        removed = [text for change in changes for text in change.get("removed_snippets", [])]
        unexpected = any(change.get("added_snippets") or change.get("replaced_snippets") for change in changes)
        if removed != [expected] or unexpected:
            raise AssertionError(f"{name}: 预期只删除一次 {expected!r}，实际为 {removed!r}")
        result = outcome.selected_result
        if result.assessment.allows_no_difference_conclusion:
            raise AssertionError(f"{name}: 扫描页不能自动判等")
        results.append({"case": name, "status": "PASS", "seconds": round(time.monotonic() - started, 2),
                        "content_changes": len(changes), "html": str(outcome.outputs["html"]),
                        "scope": "受控真实OCR删除反例；不代表扫描准确率"})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    # 同一真实扫描文件，零识别额度必须明确无法判断，不能生成假一致结果。
    options = DiffOptions(ocr_language="eng", ocr_time_budget_seconds=0)
    outcome = report_outcome(run_diff_transaction(old, old, options), args.output / "budget-zero", options)
    result = outcome.selected_result
    assert not result.assessment.allows_no_difference_conclusion
    assert any("累计 OCR" in warning for warning in result.warnings)
    results.append({"case": "budget-zero", "status": "PASS", "html": str(outcome.outputs["html"])})
    results.extend(run_region_controls(args.output))
    if args.manifest:
        results.extend(run_manifest(args.manifest, args.output))
    (args.output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
