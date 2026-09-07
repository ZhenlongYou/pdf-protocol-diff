"""Same-input, fresh-process parser evaluation; private evidence stays in output.

This deliberately does not promote a parser into production. A successful
conversion is distinct from an evaluated extraction, and neither proves that
two document versions were compared correctly.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(os.environ.get("PROTOCOL_PDF_DIFF_EVALUATION_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "src"))


def compact(text: str) -> str:
    # Keep signs, decimal points, Unicode symbols and case. No fuzzy oracle.
    return re.sub(r"\s+", " ", text).strip()


def check_expectations(pages: list[dict], expect: dict) -> list[dict]:
    """Evaluate explicit independent literals/order/region ownership.

    Checks include their identity so missing or failing categories cannot hide
    behind a single aggregate parsing score.
    """
    checks = []
    for page_spec in expect.get("pages", []):
        number = page_spec["page"]
        page = next((p for p in pages if p["page"] == number), None)
        text = compact(page["text"]) if page else ""
        for key, predicate in (
            ("contains", lambda value: compact(value) in text),
            ("absent", lambda value: compact(value) not in text),
        ):
            for index, value in enumerate(page_spec.get(key, [])):
                checks.append({"id": f"p{number}:{key}:{index}",
                               "pass": page is not None and predicate(value)})
        cursor = 0
        for index, value in enumerate(page_spec.get("ordered", [])):
            position = text.find(compact(value), cursor)
            checks.append({"id": f"p{number}:ordered:{index}",
                           "pass": page is not None and position >= cursor})
            if position >= cursor:
                cursor = position + len(compact(value))
        for index, region in enumerate(page_spec.get("regions", []) + page_spec.get("excluded_regions", [])):
            found = []
            for block in (page or {}).get("blocks", []):
                if block["label"] not in region["labels"]:
                    continue
                if compact(region.get("contains", "")) not in compact(block["text"]):
                    continue
                if "point" in region:
                    x, y = region["point"]
                    box = block.get("bbox")
                    if not box or not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                        continue
                found.append(block)
            excluded = index >= len(page_spec.get("regions", []))
            checks.append({"id": f"p{number}:region:{index}", "pass": (not found if excluded else bool(found)) and page is not None})
    return checks


def native_pages(path: Path) -> list[dict]:
    import fitz
    with fitz.open(path) as doc:
        return [{"page": p.number + 1, "width": p.rect.width, "height": p.rect.height,
                 "text": p.get_text("text", sort=True),
                 "blocks": [{"id": f"p{p.number + 1}:b{i}", "label": "text",
                             "bbox": list(b[:4]), "text": b[4]}
                            for i, b in enumerate(p.get_text("blocks")) if b[6] == 0],
                 "words": [{"id": f"p{p.number + 1}:w{i}", "bbox": list(w[:4]),
                            "text": w[4]} for i, w in enumerate(p.get_text("words"))]}
                for p in doc]


def docling_pages(document, *, included_content_layers=None) -> list[dict]:
    """Project character provenance; never duplicate a merged item per page."""
    if included_content_layers is None:
        from docling_core.types.doc import ContentLayer
        included_content_layers = set(ContentLayer)
    pages = [{"page": int(k), "text": "", "blocks": []} for k in document.pages]
    by_page = {p["page"]: p for p in pages}
    for item, _ in document.iterate_items(traverse_pictures=True, included_content_layers=included_content_layers):
        label = str(getattr(item.label, "value", item.label))
        source_text = getattr(item, "text", "")
        if label == "table":
            source_text = "\n".join(cell.text for cell in item.data.table_cells)
        for index, loc in enumerate(item.prov):
            risks = []
            if label == "table":
                text = source_text if len(item.prov) == 1 else ""
                if len(item.prov) > 1:
                    risks.append("table_cell_page_ownership_unresolved")
            elif source_text:
                start, end = loc.charspan
                if len(item.prov) == 1:
                    # A unique page/box still owns this complete item. List
                    # normalization can remove markers while provenance spans
                    # continue to address orig. Preserve text and flag the
                    # different basis instead of clearing the entire item.
                    text = source_text
                    if (start, end) != (0, len(source_text)):
                        risks.append("source_character_span_basis_differs")
                elif not 0 <= start < end <= len(source_text):
                    text = ""
                    risks.append("source_character_span_invalid")
                else:
                    text = source_text[start:end]
            else:
                text = ""
            box = loc.bbox.to_top_left_origin(document.pages[loc.page_no].size.height)
            block = {"id": f"{item.self_ref}:prov:{index}", "label": label,
                     "bbox": list(box.as_tuple()), "text": text,
                     "source_item": item.self_ref, "charspan": list(loc.charspan), "risks": risks}
            by_page[loc.page_no]["blocks"].append(block)
            by_page[loc.page_no]["text"] += text + "\n"
    return pages


def run_engine(engine: str, path: Path, output: Path) -> dict:
    """Run one engine; preserve raw output separately from normalized evidence."""
    started = time.perf_counter()
    input_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if engine == "native":
        from protocol_pdf_diff.pdf_extract import extract_pdf_text
        extraction = extract_pdf_text(path)
        pages = [{"page": p.page_number, "text": p.text,
                  "blocks": [{"id": f"p{p.page_number}:b{i}", "label": b.kind.value,
                              "bbox": list(b.bbox), "text": b.text}
                             for i, b in enumerate(p.blocks)]} for p in extraction.pages]
        versions = {p: importlib.metadata.version(p) for p in ("pdfplumber", "PyMuPDF")}
        config = {"entrypoint": "extract_pdf_text", "ocr": "production-default"}
    elif engine == "pymupdf":
        pages = native_pages(path)
        versions = {"PyMuPDF": importlib.metadata.version("PyMuPDF")}
        config = {"sort": True, "ocr": False}
    elif engine == "docling":
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
        options.generate_parsed_pages = True
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = converter.convert(path)
        raw = result.document.export_to_dict()
        (output / "raw.json").write_text(json.dumps(raw, ensure_ascii=False))
        pages = docling_pages(result.document)
        versions = {"docling": importlib.metadata.version("docling")}
        config = {"ocr": False, "table_structure": True, "pipeline": "standard"}
    elif engine == "pymupdf4llm":
        import pymupdf4llm
        raw = pymupdf4llm.to_json(str(path), use_ocr=False)
        if isinstance(raw, str):
            raw = json.loads(raw)
        (output / "raw.json").write_text(json.dumps(raw, ensure_ascii=False))
        pages = []
        for p in raw["pages"]:
            blocks = []
            for index, box in enumerate(p["boxes"]):
                text = "\n".join(" ".join(s["text"] for s in line["spans"]) for line in (box.get("textlines") or []))
                if box.get("table"):
                    text = "\n".join("\n".join(html.unescape(re.sub(r"<br\s*/?>", "\n", cell or "", flags=re.I)) for cell in row) for row in box["table"]["extract"])
                blocks.append({"id": f"b{index}", "label": box["boxclass"], "bbox": [box[k] for k in ("x0", "y0", "x1", "y1")], "text": text})
            pages.append({"page": p["page_number"], "blocks": blocks,
                          "text": "\n".join(b["text"] for b in blocks)})
        versions = {"pymupdf4llm": importlib.metadata.version("pymupdf4llm")}
        config = {"ocr": False, "layout": True}
    elif engine == "opendataloader":
        import opendataloader_pdf
        opendataloader_pdf.convert(input_path=[str(path)], output_dir=str(output), format="json", quiet=True)
        raw = json.loads((output / (path.stem + ".json")).read_text())
        pages = []
        by_page = {}
        def content_tree(item):
            own = item.get("content", "")
            parts = [own] if isinstance(own, str) and own else []
            for value in item.values():
                if isinstance(value, list):
                    parts.extend(content_tree(child) for child in value if isinstance(child, dict))
            return "\n".join(parts)

        def visit(items):
            for item in items:
                number = item.get("page number")
                if number:
                    page = by_page.setdefault(number, {"page": number, "text": "", "blocks": []})
                    text = item.get("content", "")
                    if not isinstance(text, str):
                        text = ""
                    bbox = item.get("bounding box")
                    if bbox:
                        height = heights[number - 1]
                        bbox = [bbox[0], height - bbox[3], bbox[2], height - bbox[1]]
                    page["blocks"].append({"id": str(item.get("id", "")), "label": item["type"], "bbox": bbox, "text": content_tree(item)})
                    page["text"] += text + "\n"
                for value in item.values():
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        visit(value)
        import fitz
        with fitz.open(path) as document:
            heights = [page.rect.height for page in document]
        visit(raw.get("kids", []))
        pages = list(by_page.values())
        versions = {"opendataloader-pdf": importlib.metadata.version("opendataloader-pdf")}
        config = {"hybrid": False, "ocr": False}
    else:
        raise ValueError(f"Unknown engine: {engine}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != input_sha256:
        raise ValueError('Input snapshot changed during parsing')
    return {"engine": engine, "versions": versions, "config": config,
            "input_sha256": input_sha256,
            "raw_sha256": hashlib.sha256((output/'raw.json').read_bytes()).hexdigest() if (output/'raw.json').exists() else None,
            "parse_seconds": time.perf_counter() - started, "pages": pages}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", choices=["native", "pymupdf", "docling", "pymupdf4llm", "opendataloader"], default=["native", "pymupdf", "docling", "pymupdf4llm", "opendataloader"])
    parser.add_argument("--engine-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--worker", choices=["native", "pymupdf", "docling", "pymupdf4llm", "opendataloader"])
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        result = run_engine(args.worker, args.pdf, args.output)
        try:
            import resource
            result["peak_worker_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        except ImportError:
            result["peak_worker_rss_bytes"] = None
        # Worker RSS excludes JVM / OCR descendants; never label it total memory.
        (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False))
        return 0
    if not args.manifest or args.timeout <= 0:
        parser.error("--manifest and positive --timeout are required")
    if any(args.output.iterdir()):
        parser.error("Output is not empty; choose a new directory to preserve evidence")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    case_ids = [case['id'] for case in manifest['cases']]
    if len(set(case_ids)) != len(case_ids):
        parser.error("Case ids must be unique")
    (args.output / "manifest.json").write_bytes(manifest_bytes)
    import fitz
    runner_bytes = Path(__file__).read_bytes()
    frozen_runner = args.output.resolve() / "runner.py"
    frozen_runner.write_bytes(runner_bytes)
    worker_env = dict(os.environ, PROTOCOL_PDF_DIFF_EVALUATION_ROOT=str(ROOT))
    summary = {"schema_version": 1, "runner_sha256": hashlib.sha256(runner_bytes).hexdigest(), "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "runs": []}
    summary["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    summary["native_source_files"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted((ROOT / "src/protocol_pdf_diff").rglob("*.py"))}
    summary["timing_scope"] = "fresh process imports, initialization, conversion, raw serialization, normalization and checks; not comparison or report generation"
    for case in manifest["cases"]:
        if not isinstance(case["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", case["id"]):
            raise ValueError("Case id must be a safe opaque directory name")
        case_dir = args.output / case["id"]
        case_dir.mkdir(exist_ok=True)
        path = Path(case["path"])
        selected = case["pages"]
        if not selected or any(type(p) is not int or p < 1 for p in selected) or len(set(selected)) != len(selected):
            raise ValueError("pages must contain unique positive physical page numbers")
        snapshot = case_dir / "input.pdf"
        source_bytes = path.read_bytes()
        with fitz.open(stream=source_bytes, filetype='pdf') as original, fitz.open() as subset:
            if max(selected) > len(original):
                raise ValueError("Selected physical page is outside the input document")
            for number in selected:
                subset.insert_pdf(original, from_page=number - 1, to_page=number - 1)
            subset.save(snapshot)
        identity = {"source_sha256": hashlib.sha256(source_bytes).hexdigest(), "physical_pages": selected,
                    "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}
        (case_dir / "source.json").write_text(json.dumps(identity, indent=2))
        for engine in args.engines:
            target = case_dir / engine
            target.mkdir(exist_ok=True)
            executable = Path(sys.executable) if engine == "native" else args.engine_python
            command = [str(executable), str(frozen_runner), "--worker", engine,
                       "--pdf", str(snapshot), "--output", str(target)]
            begin = time.perf_counter()
            entry = {"case": case["id"], "engine": engine, "status": "INCONCLUSIVE", "input": identity}
            entry["executable"] = str(executable.resolve())
            with (target / "run.log").open("w") as log:
                try:
                    with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=worker_env, start_new_session=os.name != "nt") as process:
                        try:
                            process.wait(timeout=args.timeout)
                        except BaseException:
                            if os.name != "nt":
                                try:
                                    os.killpg(process.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            else:
                                process.kill()
                            process.wait()
                            raise
                        if process.returncode:
                            raise subprocess.CalledProcessError(process.returncode, command)
                    result = json.loads((target / "result.json").read_text())
                    checks = check_expectations(result["pages"], case.get("expect", {}))
                    entry.update({k: result[k] for k in ("versions", "config", "parse_seconds", "peak_worker_rss_bytes")})
                    entry["checks"] = checks
                    entry["status"] = ("PASS" if all(c["pass"] for c in checks) else "FAIL") if checks else "INCONCLUSIVE"
                    entry["extracted_pages"] = len(result["pages"])
                except (subprocess.SubprocessError, OSError, ValueError) as exc:
                    entry["error"] = type(exc).__name__
            entry["wall_seconds"] = time.perf_counter() - begin
            summary["runs"].append(entry)
            (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            print(json.dumps({k: entry[k] for k in ("case", "engine", "status", "wall_seconds")}), flush=True)
    return 1 if any(r["status"] != "PASS" for r in summary["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
