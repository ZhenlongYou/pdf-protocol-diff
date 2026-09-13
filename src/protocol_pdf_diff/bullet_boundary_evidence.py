"""Exact, occurrence-owned evidence for a bullet/prose sentence boundary split."""
import re


def _flatten(text: str) -> str:
    return ' '.join(text.split())


def _owners(body: str, fragment: str) -> list[tuple[str, tuple[str, ...]]] | None:
    # The bounded path only accepts plain single-line prose records. No cells,
    # indentation or structural whitespace may be discarded to obtain a match.
    if not body or not fragment or '\n' in fragment or '\r' in fragment:
        return None
    lines = body.splitlines()
    if any('\t' in line or '|' in line or '表格行:' in line or re.search(r'\S {2,}\S', line) for line in lines):
        return None
    headings = [line for line in lines if re.match(r'^\s*[•●▪]\s+\S', line)]
    if not headings or len(set(headings)) != len(headings):
        return None
    owners = []
    prefix = []
    for line in lines:
        if line in headings:
            prefix = [line]
        elif fragment in line:
            if line != fragment or not prefix:
                return None
            owners.append((prefix[0], tuple(prefix)))
            prefix.append(line)
        elif prefix:
            prefix.append(line)
    # This count closes coverage only after every occurrence has an ordered
    # bullet identity and exact full prefix. Counts alone never prove equality.
    if not owners or body.count(fragment) != len(owners):
        return None
    return owners


def boundary_partial_candidate_indices(old_body: str, new_body: str, candidates: list) -> set[int]:
    """Identify A-owned parts of the exact A+B→B plus added A pattern.

    This NEVER proves B scope. Callers must retain B as explicit unknown.
    Every source occurrence of A must have the same unique bullet header and
    complete ordered prefix on both sides. Numerical changes, movement to
    another owner, duplicate headers and incomplete bodies fail closed.
    """
    removed = set()
    for i, change in enumerate(candidates):
        if change.kind != 'replaced' or change.pair is None:
            continue
        old, new = change.pair.old, change.pair.new
        if not new or not old.endswith(' ' + new):
            continue
        fragment = old[:-len(new)-1]
        additions = [j for j, other in enumerate(candidates) if other.kind == 'added' and other.text == fragment]
        if len(additions) != 1 or i in removed or additions[0] in removed:
            continue
        old_owners, new_owners = _owners(old_body, fragment), _owners(new_body, fragment)
        if old_owners is None or old_owners != new_owners:
            continue
        # Bind the entire displayed replacement to exactly one source span;
        # retain the shared suffix if its origin is absent or ambiguous.
        if _flatten(old_body).count(old) != 1:
            continue
        if _flatten(old_body).count(new) != 1 or _flatten(new_body).count(new) != 1:
            continue
        removed.update((i, additions[0]))
    return removed
