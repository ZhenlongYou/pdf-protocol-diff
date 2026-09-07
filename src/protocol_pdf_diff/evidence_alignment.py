"""Occurrence-preserving alignment independent of PDF chapter numbering.

Parsers supply evidence units; this module never rewrites their content or
borrows a matched occurrence a second time. Unresolved evidence is an output,
not an implicit insertion/deletion. It is a candidate API until corpus gates
permit migration of the legacy section/report path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import math
import re


@dataclass(frozen=True)
class SourceUnit:
    occurrence_id: str
    page: int
    bbox: tuple[float, float, float, float] | None
    text: str
    role: str = "text"
    risks: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.occurrence_id or type(self.page) is not int or self.page < 1:
            raise ValueError("Source unit requires an occurrence identity and physical page")
        if self.bbox is None:
            if "source_coordinates_missing" not in self.risks:
                raise ValueError("Missing source coordinates require an explicit risk")
        elif (len(self.bbox) != 4 or not all(math.isfinite(v) for v in self.bbox)
                or self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]):
            raise ValueError("Source unit requires a nonempty finite source box")


@dataclass(frozen=True)
class EvidenceDocument:
    source_sha256: str
    units: tuple[SourceUnit, ...]
    complete: bool = False
    coverage_reasons: tuple[str, ...] = ()
    alternate_source_views: tuple[SourceUnit, ...] = ()

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("Document identity must be the SHA-256 of the parsed snapshot")
        ids = [u.occurrence_id for u in self.units]
        if len(set(ids)) != len(ids):
            raise ValueError("Repeated text must retain distinct occurrence identities")
        if self.complete and (self.coverage_reasons or any(u.risks for u in self.units)):
            raise ValueError("Unresolved coverage cannot be declared complete")


def evidence_from_extraction(extraction) -> EvidenceDocument:
    """Adapt actual native-coordinate blocks without assigning inferred chapters.

    Missing coordinate evidence, OCR, cropped input and ambiguous page margins
    remain explicit. Table summaries are excluded because their native words
    already occur in the source text blocks; they must not duplicate an event.
    """
    from .models import DocumentBlockKind
    if not extraction.source_sha256:
        raise ValueError("A source snapshot identity is required")
    units, reasons, alternate_views = [], set(), []
    if extraction.warnings:
        reasons.add("extraction_warnings_require_review")
    selected = [p.page_number for p in extraction.pages]
    if not extraction.total_pages or selected != list(range(1, extraction.total_pages + 1)):
        reasons.add("partial_page_coverage")
    for page in extraction.pages:
        if page.ocr_used or page.image_dominant:
            reasons.add("image_or_ocr_coverage_unproven")
        if page.layout_risk:
            reasons.add("reading_order_unresolved")
        if page.ambiguous_line_number_sides:
            reasons.add("page_furniture_unresolved")
        text_blocks = [b for b in page.blocks if b.kind != DocumentBlockKind.TABLE]
        # Coordinate omission is page-local. Preserve the entire page as one
        # unresolved source record rather than silently dropping the missing text
        # or counting available blocks plus their page fallback twice.
        required = literal_key(page.text)
        available = literal_key(" ".join(b.text for b in text_blocks))
        if required != available:
            reasons.add("source_coordinates_missing")
            units.append(SourceUnit(f"{extraction.source_sha256}:p{page.page_number}:unlocated",
                                    page.page_number, None, page.text,
                                    risks=("source_coordinates_missing",)))
            # These are alternative views of the same page, not additional
            # comparison occurrences. Preserve both when correspondence fails.
            alternate_views.extend(SourceUnit(
                f"{extraction.source_sha256}:p{page.page_number}:alternate:{index}",
                page.page_number, block.bbox, block.text,
                risks=("source_view_correspondence_unresolved",))
                for index, block in enumerate(text_blocks) if block.text.strip())
            continue
        for index, block in enumerate(page.blocks):
            if block.kind == DocumentBlockKind.TABLE or not block.text.strip():
                continue
            risks = ()
            if block.kind == DocumentBlockKind.OCR:
                risks = ("ocr_text_unverified",)
            units.append(SourceUnit(f"{extraction.source_sha256}:p{page.page_number}:b{index}",
                                    page.page_number, block.bbox, block.text,
                                    role="text", risks=risks))
    if not units:
        reasons.add("no_comparable_source_units")
    return EvidenceDocument(extraction.source_sha256, tuple(units),
                            complete=not reasons, coverage_reasons=tuple(sorted(reasons)),
                            alternate_source_views=tuple(alternate_views))


@dataclass(frozen=True)
class EvidenceRelation:
    kind: str
    old_ids: tuple[str, ...]
    new_ids: tuple[str, ...]
    reason: str
    search_complete: bool = False


@dataclass(frozen=True)
class AlignmentResult:
    old_sha256: str
    new_sha256: str
    relations: tuple[EvidenceRelation, ...]
    old_unit_count: int
    new_unit_count: int

    @property
    def unresolved_unit_count(self) -> int:
        return sum(len(r.old_ids) + len(r.new_ids) for r in self.relations if r.kind == "unresolved")


def literal_key(text: str) -> str:
    """Whitespace-only view: signs, decimals, case and symbols remain facts."""
    return " ".join(text.split())


def _mark_order_conflicts(relations, old, new):
    """Check every paired span, including matches found in later passes."""
    old_positions = {u.occurrence_id:i for i,u in enumerate(old.units)}
    new_positions = {u.occurrence_id:i for i,u in enumerate(new.units)}
    spans = []
    for index, relation in enumerate(relations):
        if relation.old_ids and relation.new_ids:
            a = [old_positions[i] for i in relation.old_ids]
            b = [new_positions[i] for i in relation.new_ids]
            spans.append((min(a),max(a),min(b),max(b),index))
    spans.sort()
    prefix, suffix = [], []
    old_high = new_high = -1
    for a0,a1,b0,b1,index in spans:
        prefix.append((old_high,new_high))
        old_high,new_high = max(old_high,a1),max(new_high,b1)
    old_low,new_low = len(old.units),len(new.units)
    for a0,a1,b0,b1,index in reversed(spans):
        suffix.append((old_low,new_low))
        old_low,new_low = min(old_low,a0),min(new_low,b0)
    suffix.reverse()
    changed, old_ids = list(relations), set()
    for span, before, after in zip(spans,prefix,suffix):
        a0,a1,b0,b1,index = span
        if a0 <= before[0] or b0 <= before[1] or a1 >= after[0] or b1 >= after[1]:
            relation = relations[index]
            old_ids.update(relation.old_ids)
            changed[index] = EvidenceRelation('unresolved',relation.old_ids,relation.new_ids,
                                               'source_order_or_ownership_unverified')
    return changed, old_ids


def align_evidence(old: EvidenceDocument, new: EvidenceDocument, *, max_group: int = 8) -> AlignmentResult:
    """Align exact one/many units; report all remaining occurrences explicitly.

    Bounded groups make split/merge candidate generation O(N * max_group).
    Exhausting this search never proves absence. Confirmed one-sided differences
    additionally require complete extraction AND a unique bracketing pair with
    no counterpart unit in the corresponding gap. Other differences stay open.
    """
    if type(max_group) is not int or not 1 <= max_group <= 16:
        raise ValueError("max_group must be an integer in 1..16")
    old_keys = [literal_key(u.text) for u in old.units]
    new_keys = [literal_key(u.text) for u in new.units]
    used_old, used_new = set(), set()
    relations = []
    anchors = []

    def add(kind, oi, ni, reason, complete=False):
        if used_old.intersection(oi) or used_new.intersection(ni):
            raise AssertionError("An occurrence cannot belong to two relations")
        used_old.update(oi)
        used_new.update(ni)
        relations.append(EvidenceRelation(kind, tuple(old.units[i].occurrence_id for i in oi),
                                          tuple(new.units[i].occurrence_id for i in ni), reason, complete))

    def groups(document, keys):
        index = defaultdict(list)
        for start, unit in enumerate(document.units):
            if unit.risks or not keys[start]:
                continue
            texts = []
            for end in range(start, min(start + max_group, len(keys))):
                current = document.units[end]
                if current.risks or current.role != unit.role or not keys[end]:
                    break
                # A stable digest indexes the candidate; actual literal equality
                # is checked below. Digests are never occurrence identities.
                texts.append(keys[end])
                key = " ".join(texts)
                if end > start and len(key) < 40:
                    continue
                digest = hashlib.sha256(key.encode()).digest()
                index[(unit.role, digest)].append((start, end + 1, key))
        return index

    left, right = groups(old, old_keys), groups(new, new_keys)
    candidates = []
    for key, entries in left.items():
        counterparts = right.get(key, [])
        if len(entries) == len(counterparts) == 1:
            a, b = entries[0], counterparts[0]
            if a[2] == b[2]:
                candidates.append((len(a[2]), a, b))
    for _, (a0, a1, _), (b0, b1, _) in sorted(candidates, reverse=True):
        oi, ni = tuple(range(a0, a1)), tuple(range(b0, b1))
        if used_old.intersection(oi) or used_new.intersection(ni):
            continue
        kind = "equal" if a1 - a0 == b1 - b0 == 1 else "resegmented"
        add(kind, oi, ni, "unique_literal_span")
        anchors.append((a0, a1, b0, b1))

    # Repeated runs bounded by already proven anchors can be paired by order;
    # global multiplicity alone cannot identify which repeated occurrence moved.
    ordered = sorted(anchors)
    # Literal movement does not prove unchanged meaning: a whole parameter
    # sentence can move under another owner. Mark every order inversion for
    # review, independently of language, sentence length or technical keywords.
    relations, crossing_old_ids = _mark_order_conflicts(relations, old, new)
    # An unaccounted target span might be a moved or split replacement. A
    # missing literal cannot establish deletion while that alternative is open.
    old_fully_matched = len(used_old) == len(old.units) and not crossing_old_ids
    new_fully_matched = len(used_new) == len(new.units) and not crossing_old_ids
    new_anchor_starts = sorted(x[2] for x in ordered)
    ordered = [anchor for anchor in ordered if not any(old.units[i].occurrence_id in crossing_old_ids
               for i in range(anchor[0], anchor[1]))]
    brackets = [(-1, 0, -1, 0), *ordered, (len(old.units), len(old.units), len(new.units), len(new.units))]
    old_counts, new_counts = Counter(old_keys), Counter(new_keys)
    old_full, new_full = " ".join(old_keys), " ".join(new_keys)
    for before, after in zip(brackets, brackets[1:]):
        a0, a1, b0, b1 = before[1], after[0], before[3], after[2]
        if a1 < a0 or b1 < b0:
            continue  # Crossing exact anchors are moves, not an absence scope.
        # Any crossing anchor in the range invalidates this local absence proof.
        if bisect_left(new_anchor_starts, b1) != bisect_left(new_anchor_starts, b0):
            continue
        oi = tuple(i for i in range(a0, a1) if i not in used_old)
        ni = tuple(i for i in range(b0, b1) if i not in used_new)
        if oi and ni and before[0] >= 0 and after[0] < len(old.units):
            # A globally repeated phrase can be unique within two proven
            # surrounding anchors. Other unmatched material in the gap must
            # not prevent that correspondence or be discarded with it.
            local_old, local_new = defaultdict(list), defaultdict(list)
            for i in oi:
                if old_keys[i] and not old.units[i].risks:
                    local_old[(old.units[i].role, old_keys[i])].append(i)
            for j in ni:
                if new_keys[j] and not new.units[j].risks:
                    local_new[(new.units[j].role, new_keys[j])].append(j)
            local_pairs = sorted((indices[0], local_new[key][0]) for key, indices in local_old.items()
                                 if len(indices) == len(local_new.get(key, ())) == 1)
            high, prefix = -1, []
            for _, j in local_pairs:
                prefix.append(high)
                high = max(high, j)
            low, suffix = len(new.units), []
            for _, j in reversed(local_pairs):
                suffix.append(low)
                low = min(low, j)
            suffix.reverse()
            for index, (i, j) in enumerate(local_pairs):
                crossed = j < prefix[index] or j > suffix[index]
                add("unresolved" if crossed else "equal", (i,), (j,),
                    "literal_relocation_owner_unverified" if crossed else "unique_literal_between_anchors")
            oi = tuple(i for i in oi if i not in used_old)
            ni = tuple(j for j in ni if j not in used_new)
        if oi and ni and [old_keys[i] for i in oi] == [new_keys[i] for i in ni]:
            if all(old.units[i].role == new.units[j].role and not old.units[i].risks and not new.units[j].risks for i, j in zip(oi, ni)):
                for i, j in zip(oi, ni):
                    add("equal", (i,), (j,), "literal_in_anchored_gap")
        elif (oi and ni and before[0] >= 0 and after[0] < len(old.units)
              and old.complete and new.complete
              and len({old.units[i].role for i in oi} | {new.units[j].role for j in ni}) == 1):
            add("modified", oi, ni, "replacement_between_unique_anchors", True)
        elif oi and not ni and new.complete and new_fully_matched and before[0] >= 0 and after[0] < len(old.units):
            for i in oi:
                if not old.units[i].risks and old_keys[i] and new_counts[old_keys[i]] == 0 and old_keys[i] not in new_full:
                    add("deleted", (i,), (), "complete_empty_anchored_gap", True)
        elif ni and not oi and old.complete and old_fully_matched and before[0] >= 0 and after[0] < len(old.units):
            for j in ni:
                if not new.units[j].risks and new_keys[j] and old_counts[new_keys[j]] == 0 and new_keys[j] not in old_full:
                    add("added", (), (j,), "complete_empty_anchored_gap", True)

    for document, used, side in ((old, used_old, "old"), (new, used_new, "new")):
        for i, unit in enumerate(document.units):
            if i in used:
                continue
            reason = ";".join(unit.risks) or "no_unique_correspondence_within_search_budget"
            add("unresolved", (i,) if side == "old" else (), (i,) if side == "new" else (), reason)
    relations, _ = _mark_order_conflicts(relations, old, new)
    return AlignmentResult(old.source_sha256, new.source_sha256, tuple(relations), len(old.units), len(new.units))
