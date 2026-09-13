"""Regression budgets count redundant work, not machine-dependent wall time."""

from __future__ import annotations

import math
import random
import threading
import unittest
from unittest.mock import patch

from protocol_pdf_diff import compare, pdf_extract, text_utils
from protocol_pdf_diff.comparison_session import comparison_scope, memoize_comparison
from docs.verification.long_pdf_legacy_oracle import bind, skeleton_oracle, section_match_oracle


class LongPdfPerformanceTests(unittest.TestCase):
    def test_plain_words_are_normalized_once_per_stream(self):
        words = ["ordinary"] * 80
        with patch.object(text_utils, "_normalize_number_word_token",
                          wraps=text_utils._normalize_number_word_token) as normalize:
            self.assertEqual(words, text_utils.canonicalize_number_word_tokens(words))
        self.assertLessEqual(normalize.call_count, len(words) * 2)

    def test_subscript_candidates_keep_geometry_and_ambiguity(self):
        # Widely separated ordinary words cannot be touching lowered suffixes.
        words = [{"text": "ordinary", "x0": (i % 12) * 55.0,
                  "x1": (i % 12) * 55.0 + 40, "top": (i // 12) * 20.0,
                  "bottom": (i // 12) * 20.0 + 10} for i in range(192)]
        text = " ".join(w["text"] for w in words)
        with patch.object(pdf_extract, "_body_words_form_visual_subscript",
                          wraps=pdf_extract._body_words_form_visual_subscript) as predicate:
            self.assertEqual(text, pdf_extract._repair_body_visual_subscript_order(text, words))
        self.assertLess(predicate.call_count, 192 * 8)

    def test_number_grammar_matches_frozen_baseline_and_known_values(self):
        parse_old, canonical_old, _ = bind()
        vocabulary = ["ordinary", "a", "half", "zero", "one", "two", "twenty", "hundred",
                      "thousand", "million", "billion", "and", "point", "channels", "requires", "1", "two-way", "THREE"]
        rng = random.Random(52053)
        cases = ["one hundred and five", "half a million", "two thousand five hundred",
                 "a hundred channels", "mode one", "requires twenty one channels", "one point five"]
        streams = [x.split() for x in cases] + [rng.choices(vocabulary, k=rng.randrange(1, 24)) for _ in range(250)]
        for tokens in streams:
            before = list(tokens)
            for index in range(len(tokens) + 1):
                self.assertEqual(parse_old(tokens, index), text_utils.parse_number_word_phrase(tokens, index), (tokens, index))
            self.assertEqual(canonical_old(tokens), text_utils.canonicalize_number_word_tokens(tokens), tokens)
            self.assertEqual(before, tokens)
        for phrase, expected in [("one hundred and five", ("105", 4)), ("half a million", ("500000", 3)),
                                 ("two thousand five hundred", ("2500", 4))]:
            self.assertEqual(expected, text_utils.parse_number_word_phrase(phrase.split(), 0))

    def test_indexed_geometry_matches_legacy_all_pairs_at_boundaries(self):
        _, _, brute_force = bind()
        def word(text, x0, x1, top, bottom):
            return dict(text=text, x0=x0, x1=x1, top=top, bottom=bottom)
        cases = [[word("V", -8.5, -3.5, 10, 20), word("1", 1e-16, 3, 14, 21)]]
        for base_end in (-100., -3.5, 0., 100., 1e12):
            for gap in (-2., 3.5):
                for suffix_start in (math.nextafter(base_end + gap, -math.inf), base_end + gap,
                                     math.nextafter(base_end + gap, math.inf)):
                    pair = [word("V", base_end - 5, base_end, 10, 20), word("1", suffix_start, suffix_start + 3, 14, 21)]
                    cases.extend([pair, pair + [word("2", suffix_start + .1, suffix_start + 3.1, 14, 21)]])
        for coordinate in (math.nan, math.inf, -math.inf):
            cases.append([word("V", 10, 15, 10, 20), word("1", coordinate, 18, 14, 21)])
        for words in cases:
            text = "\n".join(str(w["text"]) for w in words)
            self.assertEqual(brute_force(text, words), pdf_extract._repair_body_visual_subscript_order(text, words), words)
        self.assertEqual("V1", pdf_extract._repair_body_visual_subscript_order("V\n1", cases[0]))

    def test_skeleton_shortcuts_match_historical_predicate(self):
        historical = skeleton_oracle()
        values = ["", "Voltage = 10 mV", "Voltage = 20 mV", "Table 1", "Table 2",
                  "Mode: FAST", "Mode: SLOW", "ALPHA requires two channels.",
                  "OMEGA requires three channels.", "12.5 mV", "12.6 mV", "1212121212", "1212121213",
                  "Lane 2 shall use one hundred channels.", "Lane 2 shall use 100 channels.",
                  "The transmitter shall support the specified channel configuration."]
        for left in values:
            for right in values:
                self.assertEqual(historical(left, right), compare._review_units_share_sentence_skeleton(left, right), (left, right))
        # The original lexical success rule already proves these are peers;
        # no costly character alignment is needed to reach that same verdict.
        with patch.object(compare.difflib.SequenceMatcher, "ratio", side_effect=AssertionError("redundant alignment")):
            self.assertTrue(compare._review_units_share_sentence_skeleton(
                "The transmitter shall use alpha configuration.", "The transmitter shall use beta configuration."))
            self.assertFalse(compare._sequence_ratio_at_least("a" * 2000, "b" * 2000, .9))
        import difflib
        rng = random.Random(907)
        for _ in range(300):
            left = "".join(rng.choices("abc12 ", k=rng.randrange(0, 80)))
            right = "".join(rng.choices("abc12 ", k=rng.randrange(0, 80)))
            self.assertEqual(difflib.SequenceMatcher(None, left, right, autojunk=False).ratio() >= .9,
                             compare._sequence_ratio_at_least(left, right, .9))

    def test_bit_parallel_lcs_matches_independent_grid_oracle(self):
        def grid_lcs(a, b):
            row = [0] * (len(b) + 1)
            for x in a:
                previous = row
                row = [0]
                for j, y in enumerate(b, 1):
                    row.append(previous[j - 1] + 1 if x == y else max(previous[j], row[-1]))
            return row[-1]
        rng = random.Random(90789)
        for _ in range(250):
            a = "".join(rng.choices("abc12 α中", k=rng.randrange(0, 60)))
            b = "".join(rng.choices("abc12 α中", k=rng.randrange(0, 60)))
            self.assertEqual(grid_lcs(a, b), compare._lcs_match_count(a, b))
        # Same character counts defeat the old quick bound; ordering still
        # proves this pair below threshold, without expensive full alignment.
        with patch.object(compare, "exact_sequence_ratio", side_effect=AssertionError("unbounded key alignment")):
            self.assertFalse(compare._sequence_ratio_at_least("a" * 2200 + "b" * 2200,
                                                            "b" * 2200 + "a" * 2200, .9))

    def test_title_threshold_keeps_original_direction_and_value(self):
        titles = ["", "Clause 2 Jitter", "Clause 3 Jitter", "Mode: FAST", "Mode: SLOW",
                  "1.2 Receiver specification", "1.3 Receiver specifications", "SSPRQ", "SSPRQ", "tide", "diet"]
        with comparison_scope():
            for left in titles:
                for right in titles:
                    for threshold in (.70, .92):
                        self.assertEqual(compare._review_similarity(left, right) >= threshold,
                                         compare._review_similarity_at_least(left, right, threshold))

    def test_lcs_upper_bound_cannot_certify_a_match(self):
        left, right = "abc" * 1500, "bca" * 1500
        with patch.object(compare, "_lcs_match_count", return_value=len(left)), \
             patch.object(compare, "exact_sequence_ratio", return_value=.8) as exact:
            self.assertFalse(compare._sequence_ratio_at_least(left, right, .9))
            exact.assert_called_once()

    def test_exact_long_matching_blocks_equal_independent_difflib(self):
        import difflib
        import itertools
        from protocol_pdf_diff import exact_match
        words = ["".join(value) for n in range(8) for value in itertools.product("ab", repeat=n)]
        # Force the new search even for short strings; comparing scores alone
        # would miss changed tie positions and downstream partition choices.
        with patch.object(exact_match, "_longest_match", exact_match._automaton_match):
            for left in words:
                for right in words:
                    self.assertEqual(difflib.SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks(),
                                     exact_match.matching_blocks(left, right), (left, right))
        rng = random.Random(907)
        for _ in range(2000):
            left = "".join(rng.choices("abcβ中", k=rng.randrange(80)))
            right = "".join(rng.choices("abcβ中", k=rng.randrange(80)))
            alo, ahi = sorted(rng.choices(range(len(left) + 1), k=2))
            blo, bhi = sorted(rng.choices(range(len(right) + 1), k=2))
            self.assertEqual(difflib.SequenceMatcher(None, left, right, autojunk=False).find_longest_match(alo, ahi, blo, bhi),
                             exact_match._automaton_match(left, right, alo, ahi, blo, bhi))
        for count in (4095, 4096, 4097):
            left = ("ab中β" * (count // 4 + 1))[:count - 1] + "x"
            right = ("b中βa" * (count // 4 + 1))[:count - 1] + "y"
            self.assertEqual((count, count), (len(left), len(right)))
            original = difflib.SequenceMatcher(None, left, right, autojunk=False)
            self.assertEqual(original.get_matching_blocks(), exact_match.matching_blocks(left, right))
            self.assertEqual(original.ratio(), exact_match.ratio(left, right))

    def test_long_repetitive_score_avoids_quadratic_aligner(self):
        from protocol_pdf_diff import exact_match
        # Independent expected value: the sole common run contains 12000 a's.
        with patch.object(exact_match, "SequenceMatcher", side_effect=AssertionError("quadratic long alignment")):
            self.assertEqual(2.0 * 12000 / 24001, exact_match.ratio("a" * 12000, "a" * 12000 + "b"))
            self.assertEqual(1.0, exact_match.ratio("a" * 12000, "a" * 12000))

    def test_coordinate_cleanup_preserves_frozen_results_without_unused_scan(self):
        from protocol_pdf_diff import figure_filters as filters
        # The historical character-bag oracle encoded the escaped deletion bug.
        # Keep a hand-specified semantic contract and the independent cache test.
        values = ["", "Time Undershoot VMA 1 All receivers shall support this mode.",
                  "2. Capture the specified waveform.", "Figure 3. Alpha Beta Gamma Delta"]
        for sentence in (values[2], "The receiver shall use alpha mode."):
            self.assertEqual(sentence, filters._strip_one_coordinate_figure_prefix(
                sentence, sentence, allow_interleaved_prefix=False))
        self.assertEqual("", filters._strip_one_coordinate_figure_prefix(
            "Alpha Beta Gamma Delta", "Alpha Beta Gamma Delta", allow_interleaved_prefix=False))
        # Visible and audit views reuse only immutable string results, inside
        # the same comparison. List-token outputs are deliberately not cached.
        with comparison_scope(), patch.object(filters, "_figure_text_tokens", wraps=filters._figure_text_tokens) as tokenize:
            first = filters._strip_one_coordinate_figure_prefix(values[1], values[3], allow_interleaved_prefix=True)
            calls = tokenize.call_count
            self.assertEqual(first, filters._strip_one_coordinate_figure_prefix(values[1], values[3], allow_interleaved_prefix=True))
            self.assertEqual(calls, tokenize.call_count)

    def test_fallback_preserves_matches_and_reuses_filtered_bodies(self):
        from protocol_pdf_diff.models import Section, DiffOptions
        def section(index, side, word):
            return Section(f"{side}-{index}", f"{side} heading {index}", f"{side} heading {index}",
                           1, (f"{side}{index}",), (str(index + (100 if side == "new" else 0)),),
                           index + 1, index + 1, f"{word} defines the interface behavior and limits.")
        old = [section(i, "old", chr(97+i) * 20) for i in range(6)]
        new = [section(i, "new", chr(107+i) * 20) for i in range(7)]
        historical = section_match_oracle()
        with comparison_scope():
            expected = historical(old, new, DiffOptions())
        with comparison_scope(), patch.object(compare, "_section_matching_body", wraps=compare._section_matching_body) as filtered:
            actual = compare._match_sections(old, new, DiffOptions())
        self.assertEqual(expected, actual)
        self.assertLessEqual(filtered.call_count, len(old) + len(new))
        for left in ("", "abc", "requirements " * 140):
            for right in ("", "def", "specification " * 150):
                for threshold in (0, .3, .72, 1):
                    exact = compare._section_similarity(left, right)
                    bounded = compare._section_similarity_for_threshold(left, right, threshold)
                    if bounded is None: self.assertLess(exact, threshold)
                    else: self.assertEqual(exact, bounded)

    def test_cache_lifetime_exception_and_thread_isolation(self):
        calls = []
        @memoize_comparison(maxsize=2)
        def value(key):
            calls.append((threading.get_ident(), key))
            return len(calls)
        with comparison_scope():
            first = value("pdf")
            with comparison_scope():
                self.assertEqual(first, value("pdf"))
            child_values = []
            def other_thread():
                with comparison_scope(): child_values.append(value("pdf"))
            thread = threading.Thread(target=other_thread); thread.start(); thread.join()
            self.assertNotEqual(first, child_values[0])
            value("second"); value("third")
            self.assertNotEqual(first, value("pdf"))
        with self.assertRaises(ValueError):
            with comparison_scope():
                value("exception")
                raise ValueError()
        count = len(calls)
        with comparison_scope(): value("exception")
        self.assertEqual(count + 1, len(calls))

    def test_table_ocr_has_a_finite_recognition_budget(self):
        with patch.object(pdf_extract.shutil, "which", return_value="tesseract"), \
             patch("pytesseract.image_to_string", return_value="value") as recognize:
            self.assertEqual("value", pdf_extract._ocr_table_image(object())[0])
        timeout = recognize.call_args.kwargs.get("timeout", 0)
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 60)


if __name__ == "__main__":
    unittest.main()
