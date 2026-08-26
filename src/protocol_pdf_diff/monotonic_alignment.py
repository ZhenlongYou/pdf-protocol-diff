"""Small shared dynamic-programming helper for ordered evidence alignment.

Protocol sections and Figure captions both preserve document order even when
items are inserted or removed.  Keeping the maximum-weight monotonic pairing in
one place avoids subtly different tie-breaking in the two reader-evidence paths.
"""

from __future__ import annotations

from collections.abc import Callable


def maximum_weight_monotonic_pairs(
    old_count: int,
    new_count: int,
    score_for_pair: Callable[[int, int], float | None],
) -> tuple[tuple[int, int], ...]:
    """Return ordered one-to-one index pairs with the greatest total score.

    ``None`` rejects a candidate pair. Positive scores authorize it. Skipped
    items remain available to the caller as explicit old-only or new-only
    evidence. Pairing wins an exact-score tie so deterministic equal candidates
    do not disappear from the report.
    """

    scores = [[0.0] * (new_count + 1) for _ in range(old_count + 1)]
    actions = [[""] * (new_count + 1) for _ in range(old_count + 1)]
    for old_position in range(1, old_count + 1):
        actions[old_position][0] = "old"
    for new_position in range(1, new_count + 1):
        actions[0][new_position] = "new"
    for old_position in range(1, old_count + 1):
        for new_position in range(1, new_count + 1):
            choices = [
                (scores[old_position - 1][new_position], "old"),
                (scores[old_position][new_position - 1], "new"),
            ]
            pair_score = score_for_pair(old_position - 1, new_position - 1)
            if pair_score is not None and pair_score > 0.0:
                choices.append(
                    (
                        scores[old_position - 1][new_position - 1] + pair_score,
                        "pair",
                    )
                )
            best_score, best_action = max(
                choices,
                key=lambda item: (item[0], item[1] == "pair"),
            )
            scores[old_position][new_position] = best_score
            actions[old_position][new_position] = best_action

    pairs: list[tuple[int, int]] = []
    old_position, new_position = old_count, new_count
    while old_position and new_position:
        action = actions[old_position][new_position]
        if action == "pair":
            pairs.append((old_position - 1, new_position - 1))
            old_position -= 1
            new_position -= 1
        elif action == "old":
            old_position -= 1
        else:
            new_position -= 1
    return tuple(reversed(pairs))
