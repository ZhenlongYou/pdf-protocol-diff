# PDF Diff Document Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task, with a specification review and a code-quality review after each implementation task.

**Goal:** Make the comparator state honestly when a document pair is reliable, needs manual review, or cannot be judged; reduce the highest-frequency protocol-PDF false recognitions; and add a repeatable corpus gate for future document changes.

**Architecture:** Preserve the current prose-first extraction, sectioning, comparison, and report pipeline. Add a small quality-assessment layer that consumes extraction and sectioning facts without suppressing content, expose provenance in every report, and keep layout-risk signals internal. Treat native-text, predominantly linear, numbered protocol/specification PDFs as the supported profile; scans and complex reading orders must degrade or become indeterminate instead of producing a confident equality claim.

**Tech Stack:** Python 3.12, dataclasses, pdfplumber, unittest, standalone HTML/Markdown/CSV/JSON.

---

### Task 1: Add explicit reliability assessment and provenance

**Files:**
- Create: `src/protocol_pdf_diff/quality.py`
- Modify: `src/protocol_pdf_diff/models.py`
- Modify: `src/protocol_pdf_diff/pdf_extract.py`
- Modify: `src/protocol_pdf_diff/compare.py`
- Modify: `src/protocol_pdf_diff/reporting.py`
- Test: `tests/test_protocol_diff.py`

- [x] **Step 1: Write failing assessment tests**

Add tests proving that an empty/scanned extraction is `indeterminate`, a low-text or page-fallback comparison is `degraded`, and a sufficiently populated numbered protocol pair is `reliable`. Add a report regression asserting that an indeterminate self-comparison says it cannot determine equality and never says “未发现章节级差异”.

- [x] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
PYTHONPATH=src /Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff/.venv/bin/python -m unittest \
  tests.test_protocol_diff.ProtocolDiffTests.test_empty_extraction_is_indeterminate \
  tests.test_protocol_diff.ProtocolDiffTests.test_page_fallback_comparison_is_degraded \
  tests.test_protocol_diff.ProtocolDiffTests.test_numbered_protocol_comparison_is_reliable \
  tests.test_protocol_diff.ProtocolDiffTests.test_indeterminate_report_never_claims_no_difference
```

Expected: imports or assertions fail because no assessment model exists.

- [x] **Step 3: Implement the quality model**

Create string-backed reliability states `reliable`, `degraded`, and `indeterminate`; per-document metrics for selected pages, non-empty pages, characters, stable/fallback sections, extraction warnings, and layout-risk pages; and a pair assessment with a Chinese headline, reasons, supported-profile description, and a boolean that says whether a no-difference conclusion is allowed.

Use conservative rules:

- zero comparable text or zero sections on either side -> `indeterminate`;
- low text volume, extraction warnings, page fallback, empty-page ratio, or non-linear reading-order risk -> `degraded`;
- only two adequately populated, stable-section inputs without those risks -> `reliable`.

The quality layer must not delete or rewrite extracted facts.

- [x] **Step 4: Capture reproducibility facts**

Add source SHA-256 when the input file exists, package version, supported profile, selected page windows, and the effective comparison thresholds to `DiffResult`/report JSON. Synthetic in-memory test inputs may use `null` hashes. Keep build commit optional rather than shelling out from the desktop app.

- [x] **Step 5: Put the state at the top of every report**

Render a prominent HTML state banner, a Markdown/TXT “识别可信度” section, and JSON `assessment` plus `provenance`. For `degraded`, an empty result must say “未检出差异，但不能据此确认一致”; for `indeterminate`, it must say “无法判断是否存在差异”. Preserve ordinary change findings so users can still inspect partial evidence.

- [x] **Step 6: Re-run focused and report tests**

Expected: all new assessment tests and existing report-format tests pass.

### Task 2: Detect non-linear pages and repair common false structures

**Files:**
- Modify: `src/protocol_pdf_diff/models.py`
- Modify: `src/protocol_pdf_diff/pdf_extract.py`
- Modify: `src/protocol_pdf_diff/sectioning.py`
- Modify: `src/protocol_pdf_diff/models.py`
- Modify: `src/protocol_pdf_diff/reporting.py`
- Test: `tests/test_protocol_diff.py`

- [x] **Step 1: Write failing structure tests**

Add tests for these confirmed failure modes:

- a page with two simultaneous text columns carries a non-linear reading-order risk;
- a numbered postal-address or parenthesized statistical sentence is not a heading;
- table rows cannot become section headings;
- multi-row Note/Notes boxes are not emitted as tables;
- repeating headers are removed before split heading/body lines are joined;
- front matter and revision-history changes are labeled as document metadata and sorted after technical prose.

- [x] **Step 2: Run the focused tests and confirm failure**

Run only the new tests and record the expected failures before editing implementation.

- [x] **Step 3: Add internal layout evidence**

Capture conservative page-level reading-order hints from pdfplumber word coordinates. Mark risk only when several visual rows contain substantial text on both sides of a persistent central gutter. Store the signal in `PageText` with safe defaults so existing synthetic constructors remain compatible. Do not reorder columns or expose a new visual-comparison UI in this task.

- [x] **Step 4: Harden section and table roles**

Tighten heading rejection for addresses, enumerated prose/statistical sentences, and internal table rows. Broaden Note-box rejection using row shape and leading Note/Notes semantics without suppressing genuine one-column value tables. Ensure page furniture cleanup happens before standalone heading joins.

- [x] **Step 5: Demote metadata without suppressing it**

Classify cover/front matter, contents, notices, and revision history as `document_metadata`; keep technical sections as `technical`. Report technical prose first and metadata in a clearly labeled later group. Keep table screenshots/row evidence supplementary after prose, not the primary navigation group.

- [x] **Step 6: Re-run focused and full tests**

Expected: structure tests pass and no existing numeric/table findings disappear.

### Task 3: Add Corpus v0 as an executable regression gate

**Files:**
- Create: `corpus/README.md`
- Create: `corpus/manifest.example.json`
- Create: `tools/validate_corpus.py`
- Modify: `tests/test_protocol_diff.py`
- Modify: `.gitignore`
- Modify: `README.md`

- [x] **Step 1: Write failing corpus-runner tests**

Test manifest validation, self-diff expectations, must-find/must-ignore anchors, expected reliability states, page-window handling, and a clear skip result when private local PDFs are absent.

- [x] **Step 2: Implement a privacy-safe manifest runner**

Use `PDF_DIFF_CORPUS_ROOT` for local PDFs and never commit source documents. Support:

- real OIF and PCIe version-pair anchors;
- self-diff gates for a native protocol, multi-column document, form, scan, and slide deck;
- generated formula/numeric and cross-page-table micro PDFs;
- machine-readable pass/fail/skip output and a non-zero exit only for failures.

- [x] **Step 3: Seed the example manifest and controlled cases**

Document 20-30 anchor facts as the target for each real version pair while keeping the checked-in example minimal and path-neutral. Generate controlled PDFs during tests instead of checking binary fixtures into Git.

- [x] **Step 4: Update user documentation**

Document the supported profile, reliability meanings, unsupported/degraded categories, provenance fields, and how to run Corpus v0 before changing extraction heuristics.

- [x] **Step 5: Run corpus tests and the available local corpus**

Expected: controlled cases pass; missing private documents are reported as skipped; any available OIF/PCIe anchors pass or produce an actionable failure list.

### Task 4: End-to-end verification and handoff

**Files:**
- Modify as required by review findings only.

- [x] **Step 1: Run static and full regression checks**

```bash
PYTHONPATH=src /Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff/.venv/bin/python -m py_compile main.py gui_app.py src/protocol_pdf_diff/*.py tools/validate_corpus.py
PYTHONPATH=src /Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff/.venv/bin/python -m unittest discover -s tests
git diff --check
```

- [x] **Step 2: Exercise the actual entrypoints**

Run the GUI smoke test, demo comparison, available OIF/PCIe version pairs, and at least one empty/scanned window plus one non-linear document. Inspect the generated HTML and JSON, not only the command exit code.

- [x] **Step 3: Request independent final reviews**

Use one reviewer for requirement/behavior coverage and another for code quality/regression risk. Fix all material findings and rerun Step 1.

- [x] **Step 4: Commit and push**

Review the final diff for unrelated files, commit the isolated branch `codex/pdf-diff-robustness`, and push it to `origin`.
