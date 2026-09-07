"""Exact difflib matching for long repetitive text, with no junk filtering.

The suffix automaton changes only how the longest contiguous match is found.
Direction, earliest-position ties and recursive partitions match difflib.
"""

from __future__ import annotations

from difflib import Match, SequenceMatcher


def _longest_match(a: str, b: str, alo: int, ahi: int, blo: int, bhi: int) -> Match:
    if (ahi - alo) * (bhi - blo) <= 16384:
        match = SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=False).find_longest_match()
        return Match(alo + match.a, blo + match.b, match.size)
    return _automaton_match(a, b, alo, ahi, blo, bhi)


def _automaton_match(a: str, b: str, alo: int, ahi: int, blo: int, bhi: int) -> Match:
    transitions: list[dict[str, int]] = [{}]
    links = [-1]
    lengths = [0]
    first_ends = [-1]
    last = 0
    for pos in range(blo, bhi):
        char = b[pos]
        cur = len(links)
        transitions.append({})
        lengths.append(lengths[last] + 1)
        first_ends.append(pos)
        links.append(0)
        parent = last
        while parent >= 0 and char not in transitions[parent]:
            transitions[parent][char] = cur
            parent = links[parent]
        if parent >= 0:
            target = transitions[parent][char]
            if lengths[parent] + 1 == lengths[target]:
                links[cur] = target
            else:
                clone = len(links)
                transitions.append(transitions[target].copy())
                lengths.append(lengths[parent] + 1)
                links.append(links[target])
                # A clone represents earlier occurrences of the target, not
                # the position at which the clone itself is created.
                first_ends.append(first_ends[target])
                while parent >= 0 and transitions[parent].get(char) == target:
                    transitions[parent][char] = clone
                    parent = links[parent]
                links[target] = links[cur] = clone
        last = cur

    state = size = best_size = 0
    best_a, best_b = alo, blo
    for pos in range(alo, ahi):
        char = a[pos]
        while state and char not in transitions[state]:
            state = links[state]
            size = lengths[state]
        target = transitions[state].get(char)
        if target is None:
            state = size = 0
            continue
        state = target
        size += 1
        if size > best_size:
            # Ascending a positions keep the earliest a tie. Every state's
            # first occurrence supplies the earliest b tie for its strings.
            best_a, best_b, best_size = pos - size + 1, first_ends[state] - size + 1, size
    return Match(best_a, best_b, best_size)


def matching_blocks(a: str, b: str) -> list[Match]:
    """Same blocks as SequenceMatcher(None, a, b, autojunk=False)."""
    pending = [(0, len(a), 0, len(b))]
    blocks = []
    while pending:
        alo, ahi, blo, bhi = pending.pop()
        i, j, size = _longest_match(a, b, alo, ahi, blo, bhi)
        if size:
            blocks.append((i, j, size))
            if alo < i and blo < j:
                pending.append((alo, i, blo, j))
            if i + size < ahi and j + size < bhi:
                pending.append((i + size, ahi, j + size, bhi))
    result: list[Match] = []
    for i, j, size in sorted(blocks):
        if result and result[-1].a + result[-1].size == i and result[-1].b + result[-1].size == j:
            prior = result.pop()
            result.append(Match(prior.a, prior.b, prior.size + size))
        else:
            result.append(Match(i, j, size))
    result.append(Match(len(a), len(b), 0))
    return result


def ratio(a: str, b: str) -> float:
    """Preserve difflib's exact score; accelerate only long strings."""
    if max(len(a), len(b)) <= 4096:
        return SequenceMatcher(None, a, b, autojunk=False).ratio()
    if a == b:
        return 1.0
    return 2.0 * sum(block.size for block in matching_blocks(a, b)) / (len(a) + len(b))
