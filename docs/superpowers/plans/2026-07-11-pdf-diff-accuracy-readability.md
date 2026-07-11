# PDF Diff Accuracy And Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove confirmed OIF false positives and make prose/table findings share one auditable report structure.

**Architecture:** Keep the existing pdfplumber extraction and section matcher, but strengthen their unsafe seams instead of rewriting the pipeline. Build table-change records once in the reporting layer and reuse those records for HTML, Markdown, CSV, and JSON so every output has the same facts.

**Tech Stack:** Python 3.12, pdfplumber, dataclasses, difflib, unittest, standalone HTML.

---

### Task 1: Remove rotated DRAFT objects and repair grouped table rows

**Files:**
- Modify: `src/protocol_pdf_diff/pdf_extract.py:486-596,1166-1380`
- Test: `tests/test_protocol_diff.py:1813-1824`

- [ ] **Step 1: Write failing extraction tests**

```python
def test_large_rotated_draft_text_is_filtered_even_when_upright_flag_is_true(self) -> None:
    watermark = {
        "object_type": "char", "size": 175.34, "text": "D", "upright": True,
        "matrix": (101.82, 101.82, -101.82, 101.82, 171.53, 186.69),
    }
    self.assertFalse(_keep_non_watermark_object(watermark))

def test_device_die_model_group_label_does_not_shift_expanded_rows(self) -> None:
    rows = [["Device die model\nSingle-ended device capacitance1", "C\nd1", "40", "fF"]]
    lines = _table_lines_from_rows(rows, table_number=1)
    self.assertIn("Parameter=Single-ended device capacitance1 | Symbol=Cd1 | Value=40", lines)
    self.assertFalse(any("Parameter=Device die model" in line for line in lines))
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_protocol_diff.ProtocolDiffTests.test_large_rotated_draft_text_is_filtered_even_when_upright_flag_is_true tests.test_protocol_diff.ProtocolDiffTests.test_device_die_model_group_label_does_not_shift_expanded_rows`

Expected: both tests fail against the current implementation.

- [ ] **Step 3: Implement rotation-aware filtering and group-label recognition**

```python
def _is_rotated_text_object(obj: dict[str, object]) -> bool:
    matrix = tuple(obj.get("matrix", ()) or ())
    if len(matrix) >= 4:
        a, b, c, d = (float(value) for value in matrix[:4])
        axis_scale = max(abs(a), abs(d), 1.0)
        return max(abs(b), abs(c)) >= axis_scale * 0.15
    return not bool(obj.get("upright", True))

def _keep_non_watermark_object(obj: dict[str, object]) -> bool:
    if obj.get("object_type") != "char":
        return True
    size = float(obj.get("size", 0) or 0)
    text = str(obj.get("text", ""))
    return not (
        size >= _WATERMARK_MIN_FONT_SIZE
        and text in _WATERMARK_TEXT_CHARS
        and _is_rotated_text_object(obj)
    )
```

Extend `_looks_like_group_label()` so multi-word names containing `model` are treated as group labels and skipped during logical row expansion.

- [ ] **Step 4: Re-run the focused tests**

Run the command from Step 2.

Expected: both tests pass.

### Task 2: Apply safe replacement pairing and make the unchanged threshold functional

**Files:**
- Modify: `src/protocol_pdf_diff/compare.py:418-526,1084-1168`
- Test: `tests/test_protocol_diff.py`

- [ ] **Step 1: Write failing comparison tests**

```python
def test_equal_length_replace_block_does_not_pair_unrelated_units(self) -> None:
    old = ExtractionResult(Path("old.pdf"), [PageText(1, "1 Scope\nAlpha requirement.\nFooter text.")])
    new = ExtractionResult(Path("new.pdf"), [PageText(1, "1 Scope\nEquation x = y.\nDifferent formula.")])
    result = compare_extractions(old, new, DiffOptions())
    self.assertFalse(result.changes[0].replaced_snippets)

def test_unchanged_threshold_only_suppresses_high_similarity_spelling_noise(self) -> None:
    old = ExtractionResult(Path("old.pdf"), [PageText(1, "1 Scope\nThe receiver supports calibration.")])
    new = ExtractionResult(Path("new.pdf"), [PageText(1, "1 Scope\nThe receiver support calibration.")])
    self.assertEqual([], compare_extractions(old, new, DiffOptions(unchanged_similarity=0.95)).changes)
    self.assertTrue(compare_extractions(old, new, DiffOptions(unchanged_similarity=0.9999)).changes)

def test_unchanged_threshold_never_hides_numeric_change(self) -> None:
    old = ExtractionResult(Path("old.pdf"), [PageText(1, "1 Scope\nUse Np = 53 samples.")])
    new = ExtractionResult(Path("new.pdf"), [PageText(1, "1 Scope\nUse Np = 60 samples.")])
    self.assertTrue(compare_extractions(old, new, DiffOptions(unchanged_similarity=0.5)).changes)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run the three tests above with `PYTHONPATH=src .venv/bin/python -m unittest ...`.

Expected: unsafe pairs remain and the threshold tests fail.

- [ ] **Step 3: Route every replace block through the safe matcher**

Replace the equal-length `zip` branch with `_unequal_replace_delta_candidates()` for all replace opcodes. Add `_only_minor_spelling_delta()` and use `options.unchanged_similarity` only when the section has no numeric/identifier changes and every changed token is a close spelling variant; exact normalized equality remains the primary unchanged rule.

- [ ] **Step 4: Re-run focused tests**

Expected: all three tests pass and `Np = 53 -> 60` stays visible.

### Task 3: Build one table-change fact model for every report output

**Files:**
- Modify: `src/protocol_pdf_diff/models.py:161-213`
- Modify: `src/protocol_pdf_diff/reporting.py:108-180,187-710,819-1207,1576-1796`
- Test: `tests/test_protocol_diff.py`

- [ ] **Step 1: Write failing report consistency test**

```python
def test_table_changes_share_html_json_csv_and_navigation(self) -> None:
    old_table = TableVisual(1, 1, "Table 1", (0, 0, 10, 10), "", ["表格行: T1 | Parameter=Limit | Value=50"], "")
    new_table = TableVisual(1, 1, "Table 1", (0, 0, 10, 10), "", ["表格行: T1 | Parameter=Limit | Value=46.25"], "")
    result = compare_extractions(
        ExtractionResult(Path("old.pdf"), [PageText(1, "1 Scope\nStable.")], table_visuals=[old_table]),
        ExtractionResult(Path("new.pdf"), [PageText(1, "1 Scope\nStable.")], table_visuals=[new_table]),
        DiffOptions(),
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        paths = write_reports(result, temp_dir, DiffOptions())
        html = paths["html"].read_text()
        payload = json.loads(paths["json"].read_text())
        table_csv = paths["table_csv"].read_text(encoding="utf-8-sig")
    self.assertIn('href="#table-change-1"', html)
    self.assertIn("表格行变化", html)
    self.assertEqual("46.25", payload["table_changes"][0]["row_changes"][0]["new_value"])
    self.assertIn("46.25", table_csv)
```

- [ ] **Step 2: Confirm the report consistency test fails**

Expected: no table navigation, no `table_changes` JSON field, and no table CSV.

- [ ] **Step 3: Add table change records and reuse them**

```python
@dataclass(frozen=True)
class TableRowChange:
    item: str
    old_value: str
    new_value: str
    change_type: str

@dataclass(frozen=True)
class TableChange:
    change_type: str
    old_tables: tuple[TableVisual, ...]
    new_tables: tuple[TableVisual, ...]
    similarity: float
    row_changes: list[TableRowChange]
```

Build table changes once in `write_reports()`. Use the same records for HTML metrics/navigation/cards, Markdown summaries, `table_changes.csv`, and JSON. Exclude unchanged table groups from the default report. Render table cards before prose cards, show old/new captions, pages, and pairing similarity, and use `table-change-N` anchors.

- [ ] **Step 4: Re-run report tests**

Expected: the consistency test and existing table-report tests pass after updating assertions that previously expected unchanged table cards.

### Task 4: Validate the real OIF pair and document the behavior

**Files:**
- Modify: `README.md:75-145`
- Test: `tests/test_protocol_diff.py`

- [ ] **Step 1: Add real-sample quality assertions**

Extend the local OIF regression test to assert that `foAr`, `vaTlues`, `specifDications`, `canA`, `Afmin`, and `T mVppd` are absent; that `Np = 53/60` remains visible; and that the JSON table-change count matches HTML table navigation.

- [ ] **Step 2: Update README**

Document that the unchanged threshold only suppresses high-similarity spelling noise and never hides numeric/identifier changes. Document the separate section/table metrics and `table_changes.csv`.

- [ ] **Step 3: Run verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m py_compile main.py gui_app.py src/protocol_pdf_diff/*.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python main.py --old-pdf /Users/mac/Downloads/oif2024.058.11.pdf --new-pdf /Users/mac/Downloads/oif2024.058.13.pdf --output-dir /Users/mac/Documents/ProtocolPdfDiffReports --max-snippets 20
```

Expected: compilation succeeds, all tests pass, and the regenerated report has no known watermark words or unsafe low-score replacement pairs.
