# PDF Parsing Benchmark and Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement this plan task-by-task. Every task uses TDD, then receives an independent specification review and code-quality review before the next task starts.

**Goal:** Build an auditable parser benchmark, a coordinate-preserving `DocumentBlock` intermediate representation, and explicit per-page parser routing telemetry before introducing a heavy Docling/PaddleOCR backend.

**Architecture:** Keep `pdfplumber` as the native-text/table engine and Tesseract as the scan fallback. Evaluate extraction anchors, reading order, OCR usage, state, and elapsed time without persisting source text. Attach immutable blocks and route labels to extraction results so later optional backends can be compared without silently merging contradictory output.

**Conservative amendment (2026-07-18):** The lightweight coordinate path may reorder only pure-prose dual columns. When two or more shared baselines contain a numeric literal plus a known engineering unit, it retains pdfplumber's y-first text and `layout_risk`: long-label tables and measurement-heavy prose are not reliably distinguishable without an optional layout backend. The unit gate uses a finite physical/SerDes token list (for example `V`, `mV`, `ps`, `UI`, `dB`, `GHz`, `GT/s`), so ordinary prose counts such as `1 item` remain reorderable. `DocumentBlock` geometry remains available for that future backend and A/B corpus gate.

**Tech Stack:** Python 3.12, `pdfplumber 0.11.10`, `pypdfium2 5.11.0`, `Pillow 12.3.0`, `pytesseract 0.3.13`, Tesseract 5.5.2, `unittest`, JSON manifests.

**Official sources:**

- `pdfplumber` coordinates, words, filtering, and rendering: https://github.com/jsvine/pdfplumber#the-pdfplumberpage-class
- `pytesseract` language, config, timeout, and OCR calls: https://github.com/madmaze/pytesseract#usage
- Tesseract image-quality guidance: https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html

---

## File map

- Create `src/protocol_pdf_diff/parsing_benchmark.py`: strict manifest validation, extraction-only gates, controlled fixtures, and privacy-safe summaries.
- Create `tools/benchmark_pdf_parsing.py`: CLI for controlled or user-manifest benchmarks.
- Create `corpus/parsing_benchmark.example.json`: optional real-document cases.
- Create `tests/test_parsing_benchmark.py`: runner, CLI, privacy, order, OCR, and failure tests.
- Create `src/protocol_pdf_diff/layout_blocks.py`: coordinate words/OCR/table evidence to immutable blocks.
- Modify `src/protocol_pdf_diff/models.py`: block and route enums/models plus backward-compatible `PageText` fields.
- Modify `src/protocol_pdf_diff/pdf_extract.py`: attach blocks, image facts, and route labels.
- Modify `src/protocol_pdf_diff/quality.py` and `reporting.py`: aggregate/serialize route and block audit data.
- Modify user docs and create `sources/parsing_benchmark_baseline_20260718.json`.

## Task 1: Extraction-only benchmark and controlled fixtures

**Files:**

- Create: `src/protocol_pdf_diff/parsing_benchmark.py`
- Create: `tools/benchmark_pdf_parsing.py`
- Create: `corpus/parsing_benchmark.example.json`
- Test: `tests/test_parsing_benchmark.py`

- [ ] **Step 1: Write one failing public-interface tracer test**

Generate `native.pdf` with `write_multipage_text_pdf`, write this manifest, and call `run_parsing_benchmark`:

```python
{
    "schema_version": 1,
    "cases": [{
        "id": "native-order",
        "required": True,
        "document": {"path": "native.pdf"},
        "expect": {
            "states": ["reliable", "degraded"],
            "must_extract": ["1 Scope", "Voltage limit is 12 V"],
            "must_not_extract": ["UNSEEN TOKEN"],
            "ordered_extract": ["1 Scope", "Voltage limit is 12 V"],
            "ocr_used": False
        }
    }]
}
```

Assert one pass and confirm serialized output omits corpus-root absolute paths and full extracted text.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_parsing_benchmark.ParsingBenchmarkTests.test_anchor_order_and_privacy_gates
```

Expected: import failure for `protocol_pdf_diff.parsing_benchmark`.

- [ ] **Step 3: Implement the strict public runner**

Implement public `validate_parsing_benchmark_manifest(manifest)` returning a
list of every schema error, and `run_parsing_benchmark(manifest_path, *,
corpus_root=None)` returning the JSON-serializable summary described below.

Schema rules:

- top level: `schema_version`, `cases`;
- case: `id`, `required`, `description`, `document`, `ocr_language`, `expect`;
- document: relative PDF `path`, optional positive `start_page`/`end_page`;
- expect: mutually exclusive `state`/`states`, plus `must_extract`, `must_not_extract`, `ordered_extract`, `ocr_used`;
- reject unknown keys, unsafe paths, duplicate ids/states, invalid page windows, and non-boolean `ocr_used`;
- optional missing inputs skip; required missing inputs fail;
- existing cases call public `extract_pdf_text`, then `compare_extractions(extraction, extraction, DiffOptions(ocr_language=case.get("ocr_language")))` for the existing assessment;
- anchor matching is literal and case-insensitive; order anchors must have monotonically increasing positions;
- persist only id/status/failures/state/page count/OCR pages/warnings/character count/elapsed seconds, never source text, hashes, report paths, or resolved corpus roots.

Every meaningful source line gets a nearby Chinese maintenance comment per repository policy.

- [ ] **Step 4: Run GREEN, then add one RED→GREEN cycle per behavior**

Cover malformed schema, optional/required missing files, missing/forbidden/order anchors, OCR use, redacted execution errors, state alternatives, and duplicate state rejection.

- [ ] **Step 5: Add controlled fixture generation**

Implement public `run_controlled_parsing_benchmark()` returning the same
privacy-safe summary as the manifest runner.

Generate disposable PDFs inside `TemporaryDirectory`:

1. native linear text with numeric/unit anchors;
2. positioned pure-prose two-column native text with left anchors before right anchors;
3. formula text containing `Vout = Vin * (1 + R2/R1)` and `BER <= 1e-12`;
4. borderless table text containing `Parameter`, `Minimum`, `Maximum`, `Units`;
5. raster English text using Pillow's bundled font, requiring real OCR when Tesseract exists.

Only the OCR case skips when Tesseract is absent. No binary fixture or full extracted source text persists.

- [ ] **Step 6: Add CLI and optional real manifest**

`tools/benchmark_pdf_parsing.py` accepts either positional `manifest` or `--controlled`, plus `--corpus-root` and `--output-json`. Input modes are exclusive. Print summary JSON; exit `1` on failures, otherwise `0` including optional skips.

`corpus/parsing_benchmark.example.json` reuses optional OIF/JLT/form/scan/slides paths and only anchors already verified by existing corpus.

- [ ] **Step 7: Verify and commit Task 1**

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_parsing_benchmark
PYTHONPATH=src .venv/bin/python tools/benchmark_pdf_parsing.py --controlled \
  --output-json /tmp/pdf_parsing_benchmark.json
git diff --check
```

Commit: `test(pdf-diff): add parser benchmark gates`.

## Task 2: Immutable `DocumentBlock` intermediate representation

**Files:**

- Create: `src/protocol_pdf_diff/layout_blocks.py`
- Modify: `src/protocol_pdf_diff/models.py`
- Modify: `src/protocol_pdf_diff/pdf_extract.py`
- Test: `tests/test_layout_blocks.py`

- [ ] **Step 1: Write and run a failing public extraction test**

Generate a positioned native PDF, call `extract_pdf_text`, and assert `PageText.blocks` contains contiguous ordered blocks with page, bbox, kind, text, source engine, and optional confidence. Expected RED: missing `blocks`.

- [ ] **Step 2: Add immutable models**

```python
class DocumentBlockKind(str, Enum):
    TEXT = "text"
    TABLE = "table"
    OCR = "ocr"

@dataclass(frozen=True)
class DocumentBlock:
    page_number: int
    bbox: tuple[float, float, float, float]
    kind: DocumentBlockKind
    text: str
    reading_order: int
    source_engine: str
    confidence: float | None = None
```

Extend `PageText` with `blocks: tuple[DocumentBlock, ...] = ()`.

- [ ] **Step 3: Implement documented coordinate-word grouping**

`extract_pdfplumber_text_blocks(page, page_number)` calls documented `extract_words(keep_blank_chars=False, use_text_flow=False)`, skips malformed coordinates, groups close `top` values into physical lines, orders words by `x0`, emits one `TEXT` block per line, sorts by `(top, x0)`, and returns contiguous reading order plus warnings. Cite https://github.com/jsvine/pdfplumber#extracting-text in the source.

- [ ] **Step 4: Attach OCR/table blocks without changing comparison text**

- native blocks come from the same filtered page;
- successful OCR adds one page-bounds `OCR` block with `source_engine="tesseract"` and no guessed confidence;
- each accepted `TableVisual` adds one `TABLE` block with its bbox and row text;
- reassign final reading order;
- do not change `PageText.text`, sectioning, or comparison semantics.

- [ ] **Step 5: Add RED→GREEN boundary cases**

Test malformed/blank/duplicate words, coordinate errors, OCR/table provenance, stable ordering, and backward-compatible empty blocks. Prefer public `extract_pdf_text` tests; use pure geometry tests only where the public path cannot isolate the rule.

- [ ] **Step 6: Verify and commit Task 2**

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_layout_blocks tests.test_page_ocr tests.test_layout_generality
git diff --check
```

Commit: `feat(pdf-diff): add document block evidence`.

## Task 3: Explicit parser routes and audit serialization

**Files:**

- Modify: `src/protocol_pdf_diff/models.py`
- Modify: `src/protocol_pdf_diff/page_ocr.py`
- Modify: `src/protocol_pdf_diff/pdf_extract.py`
- Modify: `src/protocol_pdf_diff/quality.py`
- Modify: `src/protocol_pdf_diff/reporting.py`
- Test: `tests/test_parser_routing.py`
- Test: `tests/test_reporting_generality.py`

- [ ] **Step 1: Write and run failing public route tests**

```python
class PageParserRoute(str, Enum):
    NATIVE_TEXT = "native_text"
    NATIVE_LAYOUT_RISK = "native_layout_risk"
    OCR_FALLBACK = "ocr_fallback"
    IMAGE_TEXT_LAYER = "image_text_layer"
    UNREADABLE_IMAGE = "unreadable_image"
```

Exercise public extraction for ordinary native, non-linear native, successful OCR, image-dominant searchable, and image-dominant unreadable pages. Expected RED: no `parser_route`.

- [ ] **Step 2: Preserve facts and implement pure classification**

Add `image_dominant: bool = False` and `parser_route: PageParserRoute = NATIVE_TEXT` to `PageText`. Preserve `image_dominant` separately from `ocr_used`. Implement:

```python
def classify_page_parser_route(
    *, text: str, layout_risk: bool, image_dominant: bool, ocr_used: bool
) -> PageParserRoute:
    if ocr_used:
        return PageParserRoute.OCR_FALLBACK
    if image_dominant:
        return (
            PageParserRoute.IMAGE_TEXT_LAYER
            if text.strip()
            else PageParserRoute.UNREADABLE_IMAGE
        )
    if layout_risk:
        return PageParserRoute.NATIVE_LAYOUT_RISK
    return PageParserRoute.NATIVE_TEXT
```

Precedence: OCR; image-dominant searchable/unreadable; native layout risk; native text. Do not inspect warnings or filenames. Image/OCR pages remain quality-degraded.

- [ ] **Step 3: Aggregate route metrics**

Add `parser_route_pages: tuple[tuple[str, tuple[int, ...]], ...] = ()` to `DocumentQualityMetrics`. Include every selected page exactly once; keep `ocr_pages` for compatibility.

- [ ] **Step 4: Serialize audit facts without source text**

- assessment metrics: route name to page lists;
- extraction audit: page number, parser route, image-dominant/OCR/layout flags, and block count;
- never serialize block text or full page text;
- keep OCR thresholds/language in provenance.

- [ ] **Step 5: Add completeness/privacy/backward-compatibility tests**

Test all five routes, every page exactly once, JSON output, absence of source/block text, and manually constructed legacy `PageText` objects.

- [ ] **Step 6: Verify and commit Task 3**

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_parser_routing tests.test_reporting_generality tests.test_page_ocr
git diff --check
```

Commit: `feat(pdf-diff): expose parser routing evidence`.

## Task 4: Documentation, baseline, and milestone verification

**Files:**

- Modify: `README.md`, `TEST_MATRIX.md`, `corpus/README.md`
- Modify: `sources/research_pdf_parsing_landscape_20260718.md`
- Create: `sources/parsing_benchmark_baseline_20260718.json`

- [ ] **Step 1: Run controlled and optional real benchmarks**

```bash
PYTHONPATH=src .venv/bin/python tools/benchmark_pdf_parsing.py --controlled \
  --output-json sources/parsing_benchmark_baseline_20260718.json
PYTHONPATH=src .venv/bin/python tools/benchmark_pdf_parsing.py \
  corpus/parsing_benchmark.example.json --corpus-root /Users/mac/Downloads \
  --output-json /tmp/pdf_parsing_real_benchmark.json
```

Do not weaken failing anchors/order gates to make the baseline green; failures define the next backend target.

- [ ] **Step 2: Update docs**

Document commands, pure-prose-only column-order gate semantics, numeric/unit dual-column y-first boundary, route names, privacy, actual Tesseract 5.5.2 verification, and that blocks/routes are evidence plumbing—not a Docling/PaddleOCR accuracy claim. Keep P0b budget/cancel and optional backend work explicit.

- [ ] **Step 3: Full verification**

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
COVERAGE_FILE=/tmp/pdf_parser_milestone1.coverage PYTHONPATH=src \
  .venv/bin/python -m coverage run --branch --source=protocol_pdf_diff \
  -m unittest discover -s tests
COVERAGE_FILE=/tmp/pdf_parser_milestone1.coverage \
  .venv/bin/python -m coverage report -m
PYTHONPATH=src .venv/bin/python gui_app.py --smoke-test
PYTHONPATH=src .venv/bin/python main.py --demo --output-dir /tmp/pdf_parser_milestone1_demo
PYTHONPATH=src .venv/bin/python tools/validate_corpus.py \
  corpus/manifest.example.json --corpus-root /Users/mac/Downloads \
  --output-json /tmp/pdf_parser_milestone1_corpus.json
.venv/bin/python -m compileall -q src main.py gui_app.py tests tools
.venv/bin/python -m pip check
git diff --check
```

- [ ] **Step 4: Multiple independent final reviewers**

Use at least three read-only reviewers: parser/OCR correctness; benchmark methodology/privacy; architecture/docs/direct-run delivery. Fix Critical/Important findings and re-review material changes.

- [ ] **Step 5: Final commit and push**

Stage only milestone files, commit `feat(pdf-diff): add parsing benchmark and routing evidence`, push `codex/pdf-parser-robustness-round2`, and verify remote SHA equals local `HEAD`.

## Plan self-review

- Coverage: benchmark, real OCR, complex-layout gates, blocks, routes, privacy, docs, verification, multi-agent review, commit, and push all have explicit tasks.
- Deferred by design: Docling/PaddleOCR, visual diff, deskew/orientation, and long-document cancellation are later milestones measured by this benchmark.
- Placeholder scan: no `TODO`, `TBD`, unnamed test, or unspecified error handling remains.
- Type consistency: `DocumentBlock`, `DocumentBlockKind`, `PageParserRoute`, `PageText.blocks`, `PageText.image_dominant`, `PageText.parser_route`, and `DocumentQualityMetrics.parser_route_pages` are consistent.
