"""Complete formula source handoff to explicit review. Never proves equivalence."""

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .compare import _split_units
from .formula_forward_evidence import bind, forward
from .formula_source_bridge import bridge, discover
from .text_utils import compact_inline

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormulaSourceReview:
    old: dict
    new: dict
    status: str = "formula_source_requires_review"


def section_scope_hash(section):
    import json

    return hashlib.sha256(
        json.dumps(asdict(section), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def render_source_page(path, page):
    import fitz

    if type(page) is not int or page < 1:
        raise ValueError("invalid_source_page")
    with fitz.open(path) as document:
        return (
            document[page - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).tobytes("png")
        )


class PreparedFormulaReviews(tuple):
    """Report-local rendering cache, never a process-global authority."""

    def __new__(cls, values, cache, context):
        result = super().__new__(cls, values)
        result.source_cache = cache
        result.source_context = context
        return result


def source_image_matches(side, cache=None):
    cache = {} if cache is None else cache
    try:
        path = Path(side["pdf_path"]).resolve()
        stat = path.stat()
        identity = (str(path), stat.st_dev, stat.st_ino, stat.st_size,
                    stat.st_mtime_ns, stat.st_ctime_ns)
        key = (identity, side["pdf_sha256"], side["page"], 1.5, 1.5, "png")
        if key not in cache:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != side["pdf_sha256"]:
                return False
            rendered = render_source_page(path, side["page"])
            after = path.stat()
            if (after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns) != identity[1:]:
                return False
            cache[key] = hashlib.sha256(rendered).hexdigest()
        return cache[key] == side["image_sha256"]
    except (KeyError, TypeError, ValueError, OSError, IndexError):
        return False


def actual_source_matches(q, context, cache):
    """Rebuild native glyph positions using actual extraction context, not receipts."""
    matches = [page for path, sha, page in context
               if path == q["pdf_path"] and sha == q["pdf_sha256"]
               and page.page_number == q["page"]]
    if len(matches) != 1:
        return False
    page = matches[0]
    if hashlib.sha256(page.text.encode()).hexdigest() != q["page_text_sha256"]:
        return False
    stat = Path(q["pdf_path"]).stat()
    key = ("native", q["pdf_path"], q["pdf_sha256"], q["page"],
           stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
           page.text, tuple(page.visual_noise_bboxes), tuple(q["roi"]))
    if key not in cache:
        cache[key] = bridge(q["pdf_path"], q["page"], q["roi"],
                            page.text, page.visual_noise_bboxes)
    return cache[key] == q["bridge"]


def mixed_or_scalar(text):
    return bool(
        re.search(
            r"[<>≤≥≦≧]|\b(?:if|at|or|is|mV|mA|dB|ns|ps|UI)\b|\b[A-Za-z]{3,}\b", text
        )
    )


def prove_side(path, section, pages, comparison_maps=()):
    if section is None:
        raise ValueError("unpaired_section")
    candidates = []
    for pn in range(section.start_page, section.end_page + 1):
        if pn not in pages:
            raise ValueError("incomplete_pages")
        candidates.extend((pn, roi) for roi in discover(path, pn))
    if len(candidates) != 1:
        raise ValueError("nonunique_roi_in_section")
    pn, roi = candidates[0]
    page = pages[pn]
    evidence = bridge(path, pn, roi, page.text, page.visual_noise_bboxes)
    expected = sorted(g["final_index"] for g in evidence["glyphs"])
    matches = []
    texts = {n: p.text for n, p in pages.items()}
    mapped = {n: (text, spans) for n, text, spans, removed in comparison_maps}
    removed_by_page = {n: removed for n, text, spans, removed in comparison_maps}
    for n, (text, spans) in mapped.items():
        if n not in pages:
            continue
        original = pages[n].text
        for a, z, start in spans:
            if text[a:z] != original[start : start + z - a]:
                raise ValueError("caption_source_drift")
        texts[n] = text
    for unit in _split_units(section.body):
        try:
            parts = bind(unit, section, texts, pn)
        except (ValueError, KeyError):
            continue
        if pn in mapped:
            _, spans = mapped[pn]
            valid = True
            for part in parts:
                translated = []
                for v in part["page_offsets"]:
                    if v is None:
                        translated.append(None)
                        continue
                    hits = [start + v - a for a, z, start in spans if a <= v < z]
                    if len(hits) != 1:
                        valid = False
                        break
                    translated.append(hits[0])
                part["page_offsets"] = translated
            if not valid:
                continue
        offsets = [v for p in parts for v in p["page_offsets"] if v is not None]
        if offsets == expected and len(set(offsets)) == len(offsets):
            matches.append((parts, unit))
    if len(matches) != 1:
        raise ValueError("complete_forward_snippet_source_missing")
    return {
        "caption_source_spans": mapped.get(pn, ("", ()))[1],
        "caption_removed_spans": removed_by_page.get(pn, ()),
        "page": pn,
        "roi": roi,
        "section_id": section.section_id,
        "parts": matches[0][0],
        "original_formula_text": matches[0][1],
        "bridge": evidence,
        "page_text_sha256": hashlib.sha256(page.text.encode()).hexdigest(),
    }


def build_formula_source_reviews(result, old_extraction, new_extraction):
    """Only existing raw formula differences trigger bounded source discovery."""
    from .compare import _is_displayed_formula_review_unit

    output = []
    for path, ex in (
        (result.old_pdf, old_extraction),
        (result.new_pdf, new_extraction),
    ):
        if (
            not ex.source_sha256
            or hashlib.sha256(Path(path).read_bytes()).hexdigest() != ex.source_sha256
        ):
            return ()
    for change in result.changes:
        if change.old_section is None or change.new_section is None:
            continue
        if change.old_section.heading_path != change.new_section.heading_path:
            continue
        if any(
            s.end_page - s.start_page > 2
            for s in (change.old_section, change.new_section)
        ):
            continue
        raw = {
            "old": [
                *change.removed_snippets,
                *[p.old for p in change.replaced_snippets],
            ],
            "new": [*change.added_snippets, *[p.new for p in change.replaced_snippets]],
        }
        if not all(
            any(
                _is_displayed_formula_review_unit(u)
                and any(compact_inline(p["text"]) in raw[side] for p in forward(u))
                for u in _split_units(getattr(change, side + "_section").body)
            )
            for side in ("old", "new")
        ):
            continue
        try:
            sides = []
            for side, path, ex in [
                ("old", result.old_pdf, old_extraction),
                ("new", result.new_pdf, new_extraction),
            ]:
                q = prove_side(
                    path,
                    getattr(change, side + "_section"),
                    {p.page_number: p for p in ex.pages},
                    getattr(result, side + "_formula_page_maps"),
                )
                # This path must not relocate ordinary prose, constraints or notes.
                if mixed_or_scalar(q["original_formula_text"]):
                    raise ValueError("mixed_ordinary_prose")
                q["pdf_path"] = str(path)
                q["pdf_sha256"] = ex.source_sha256
                q["section_sha256"] = hashlib.sha256(
                    getattr(change, side + "_section").body.encode()
                ).hexdigest()
                q["section_scope_sha256"] = section_scope_hash(
                    getattr(change, side + "_section")
                )
                q["image_bytes"] = render_source_page(path, q["page"])
                q["image_scope"] = "complete_source_page"
                q["image_sha256"] = hashlib.sha256(q["image_bytes"]).hexdigest()
                if (
                    hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    != ex.source_sha256
                ):
                    raise ValueError("changed_source_pdf")
                sides.append(q)
            output.append(FormulaSourceReview(*sides))
        except Exception as error:  # noqa: BLE001 -- Unsupported PDF decoders grant no handoff authority.
            # Unsupported PDF decoding provides no transfer authority.
            _LOGGER.debug("Formula source review unavailable: %s", error)
            continue
    return tuple(output)


def prepare_formula_source_reviews(reviews, directory, source_context=()):
    """Writing both source images is a necessary handoff condition."""
    accepted = []
    source_cache = {}
    for index, r in enumerate(reviews):
        if not isinstance(r, FormulaSourceReview):
            continue
        try:
            paths = []
            for side in ("old", "new"):
                q = getattr(r, side)
                data = q["image_bytes"]
                if not source_image_matches(q, source_cache):
                    raise ValueError("image_not_rendered_from_source")
                if not actual_source_matches(q, source_context, source_cache):
                    raise ValueError("actual_native_source_mismatch")
                if not data or hashlib.sha256(data).hexdigest() != q["image_sha256"]:
                    raise ValueError("missing_image")
                import io

                from PIL import Image

                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                p = Path(directory) / f"formula-review-{index}-{side}.png"
                p.write_bytes(data)
                if hashlib.sha256(p.read_bytes()).hexdigest() != q["image_sha256"]:
                    raise ValueError("image_write_failed")
                paths.append(str(p))
            accepted.append((r, tuple(paths)))
        except (KeyError, ValueError, OSError):
            continue
    return PreparedFormulaReviews(accepted, source_cache, source_context)


def project_formula_source_review(change, accepted):
    """All raw occurrences transfer atomically to one explicit unknown record."""
    source_cache = getattr(accepted, "source_cache", {})
    for receipt, paths in accepted:
        try:
            if not isinstance(receipt, FormulaSourceReview) or len(paths) != 2:
                continue
            if receipt.status != "formula_source_requires_review":
                continue
            records = {}
            for side, path in zip(("old", "new"), paths):
                q = getattr(receipt, side)
                s = getattr(change, side + "_section")
                if s is None or section_scope_hash(s) != q["section_scope_sha256"]:
                    raise ValueError("changed_complete_section_scope")
                unit = q["original_formula_text"]
                if _split_units(s.body).count(unit) != 1:
                    raise ValueError("original_formula_not_unique_source_unit")
                original_parts = forward(unit)
                if len(original_parts) != len(q["parts"]) or any(
                    any(
                        expected[k] != actual.get(k)
                        for k in ("text", "offsets", "removed")
                    )
                    for expected, actual in zip(original_parts, q["parts"])
                ):
                    raise ValueError("original_formula_forward_mismatch")
                if not source_image_matches(q, source_cache):
                    raise ValueError("image_not_rendered_from_source")
                if not actual_source_matches(q, getattr(accepted, "source_context", ()), source_cache):
                    raise ValueError("actual_native_source_mismatch")

                if (
                    hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    != q["image_sha256"]
                ):
                    raise ValueError("missing_output_image")
                if (
                    s is None
                    or q["section_id"] != s.section_id
                    or not s.start_page <= q["page"] <= s.end_page
                    or hashlib.sha256(s.body.encode()).hexdigest()
                    != q["section_sha256"]
                ):
                    raise ValueError("stale_section")
                if (
                    hashlib.sha256(Path(q["pdf_path"]).read_bytes()).hexdigest()
                    != q["pdf_sha256"]
                ):
                    raise ValueError("source_changed")
                glyphs = sorted(q["bridge"]["glyphs"], key=lambda g: g["final_index"])
                offsets = [
                    i
                    for part in q["parts"]
                    for i in part["page_offsets"]
                    if i is not None
                ]
                if offsets != [g["final_index"] for g in glyphs] or len(
                    set(offsets)
                ) != len(offsets):
                    raise ValueError("incomplete_source_offsets")
                if "".join(
                    c for p in q["parts"] for c in p["text"] if not c.isspace()
                ) != "".join(g["text"] for g in glyphs):
                    raise ValueError("source_text_mismatch")
                if mixed_or_scalar(q["original_formula_text"]):
                    raise ValueError("mixed_body")
                records[side] = {k: v for k, v in q.items() if k != "image_bytes"}
                records[side]["image_path"] = Path(path).name
                records[side]["image_path_base"] = "report_directory"
            needles = {
                s: [compact_inline(p["text"]) for p in getattr(receipt, s)["parts"]]
                for s in ("old", "new")
            }
            vals = {
                "old": [
                    *change.removed_snippets,
                    *[p.old for p in change.replaced_snippets],
                ],
                "new": [
                    *change.added_snippets,
                    *[p.new for p in change.replaced_snippets],
                ],
            }
            if any(
                len(set(ns)) != len(ns) or any(vals[side].count(n) != 1 for n in ns)
                for side, ns in needles.items()
            ):
                continue
            if any(
                (p.old in needles["old"]) != (p.new in needles["new"])
                for p in change.replaced_snippets
            ):
                continue
            records.update(
                status=receipt.status,
                review_count=1,
                meaning="Formula sources require review; no mathematical or glyph equivalence is asserted.",
                raw_removed=[v for v in change.removed_snippets if v in needles["old"]],
                raw_added=[v for v in change.added_snippets if v in needles["new"]],
                raw_replaced=[
                    {"old": p.old, "new": p.new}
                    for p in change.replaced_snippets
                    if p.old in needles["old"]
                ],
            )
            added = [v for v in change.added_snippets if v not in needles["new"]]
            removed = [v for v in change.removed_snippets if v not in needles["old"]]
            pairs = [p for p in change.replaced_snippets if p.old not in needles["old"]]
            return replace(
                change,
                change_type=change.change_type
                if added or removed or pairs
                else "review",
                added_snippets=added,
                removed_snippets=removed,
                replaced_snippets=pairs,
                formula_review_records=[*change.formula_review_records, records],
            )
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return change
