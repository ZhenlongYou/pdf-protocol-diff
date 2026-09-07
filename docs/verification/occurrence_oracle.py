"""Manual per-occurrence expectations; no production alignment imports."""
from collections import Counter
import json
from pathlib import Path
import sys


def validate(fixture, observed):
    if "expected_exception" in fixture:
        assert observed.get("exception") == fixture["expected_exception"], "exception mismatch"
        return
    assert "exception" not in observed, "unexpected exception"
    for side in ("old", "new"):
        expected_ids = [f"{side}:{i}" for i in range(len(fixture[side]))]
        relations = observed["relations"]
        actual_ids = [i for r in relations for i in r[side + "_ids"]]
        assert Counter(actual_ids) == Counter(expected_ids), "occurrence count changed"
        states = {i:r["kind"] for r in relations for i in r[side + "_ids"]}
        assert [states[i] for i in expected_ids] == fixture[side + "_states"], "OCCURRENCE_SEMANTIC_MISMATCH"
        assert observed[side + "_texts"] == fixture[side], "original text changed"


def main():
    for path in sys.argv[1:]:
        fixture = json.loads(Path(path).read_text())
        # The validator must reject disappearance, unsupported confirmation,
        # and corrupt source text, not merely accept an implementation output.
        if "expected_exception" in fixture:
            validate(fixture, {"exception": "ValueError"})
            corrupt = {"exception": "None"}
        else:
            relations = [{"kind":state, "old_ids":[f"old:{i}"], "new_ids":[]}
                         for i,state in enumerate(fixture["old_states"])]
            relations += [{"kind":state, "old_ids":[], "new_ids":[f"new:{i}"]}
                          for i,state in enumerate(fixture["new_states"])]
            good = {"relations":relations, "old_texts":fixture["old"], "new_texts":fixture["new"]}
            validate(fixture, good)
            corrupt = {**good, "relations":[]}
        try:
            validate(fixture, corrupt)
        except AssertionError:
            continue
        raise AssertionError("oracle accepted corrupted evidence")
    print("OCCURRENCE_ORACLE_REJECTS_CORRUPTION")


if __name__ == "__main__":
    main()
