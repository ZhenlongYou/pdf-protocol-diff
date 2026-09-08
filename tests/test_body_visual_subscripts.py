"""Regression tests for coordinate-proven subscripts in ordinary PDF prose."""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.pdf_extract import (
    _printed_line_number_grid_metrics,
    _repair_body_visual_subscript_order,
    _repair_body_visual_subscript_order_with_duplicate_fallback,
    _repair_unique_section_anchor_reading_order,
    extract_pdf_text,
)


def _word(
    text: str,
    x0: float,
    x1: float,
    top: float,
    bottom: float,
) -> dict[str, float | str]:
    """Build the validated coordinate-word shape used by the extractor."""

    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
    }


def _visible_characters(value: str) -> Counter[str]:
    return Counter(character for character in value if not character.isspace())


class BodyVisualSubscriptTests(unittest.TestCase):
    def test_coordinate_proven_ctle_gain_subscripts_rejoin_inline(self) -> None:
        """Lowered DC/DC2 suffixes stay attached to their visible g symbols."""

        words = [
            _word("parameters", 10.0, 60.0, 100.0, 112.0),
            _word("g", 64.0, 71.0, 100.0, 112.0),
            _word("and", 88.0, 105.0, 100.0, 112.0),
            _word("g", 109.0, 116.0, 100.0, 112.0),
            _word("are", 140.0, 157.0, 100.0, 112.0),
            _word("used.", 161.0, 188.0, 100.0, 112.0),
            _word("DC", 70.9, 84.0, 104.8, 114.4),
            _word("DC2", 115.9, 136.0, 104.8, 114.4),
        ]
        raw = "parameters g DC and g DC2 are used."

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual("parameters gDC and gDC2 are used.", repaired)
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_coordinate_proven_ctle_frequency_subscripts_rejoin_inline(self) -> None:
        """CTLE pole, zero, and low-frequency suffixes remain on their f bases."""

        words = [
            _word("values", 10.0, 42.0, 100.0, 112.0),
            _word("f", 46.0, 52.0, 100.0, 112.0),
            _word(",", 65.0, 68.0, 100.0, 112.0),
            _word("f", 72.0, 78.0, 100.0, 112.0),
            _word(",", 91.0, 94.0, 100.0, 112.0),
            _word("f", 98.0, 104.0, 100.0, 112.0),
            _word(",", 111.0, 114.0, 100.0, 112.0),
            _word("f", 118.0, 124.0, 100.0, 112.0),
            _word("apply.", 141.0, 175.0, 100.0, 112.0),
            _word("p1", 51.9, 64.0, 104.8, 114.4),
            _word("p2", 77.9, 90.0, 104.8, 114.4),
            _word("z", 103.9, 110.0, 104.8, 114.4),
            _word("LF", 123.9, 138.0, 104.8, 114.4),
        ]
        raw = "values f p1 , f p2 , f z , f LF apply."

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual("values fp1 , fp2 , fz , fLF apply.", repaired)
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_coordinate_proven_eecq_metric_subscripts_rejoin_inline(self) -> None:
        """EECQ metric names keep their visibly lowered suffixes in prose."""

        words = [
            _word("VMA", 10.0, 36.0, 100.0, 112.0),
            _word("is", 58.0, 67.0, 100.0, 112.0),
            _word("used", 71.0, 94.0, 100.0, 112.0),
            _word("in", 98.0, 107.0, 100.0, 112.0),
            _word("place", 111.0, 136.0, 100.0, 112.0),
            _word("of", 140.0, 150.0, 100.0, 112.0),
            _word("OMA", 154.0, 181.0, 100.0, 112.0),
            _word(".", 205.0, 208.0, 100.0, 112.0),
            _word("VMA", 212.0, 238.0, 100.0, 112.0),
            _word("reference.", 261.0, 310.0, 100.0, 112.0),
            _word("eecq", 35.9, 56.0, 104.8, 114.4),
            _word("outer", 180.9, 203.0, 104.8, 114.4),
            _word("eecq", 237.9, 259.0, 104.8, 114.4),
        ]
        raw = "VMA is used in place of OMA . VMA reference.\neecq outer eecq"

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "VMAeecq is used in place of OMAouter . VMAeecq reference.",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_coordinate_proven_sigma_and_ceeq_subscripts_rejoin_inline(self) -> None:
        """Legacy Symbol sigma and Ceeq keep their coordinate-proven suffixes."""

        words = [
            _word("\uf073", 10.0, 18.0, 100.0, 112.0),
            _word("is", 28.0, 37.0, 100.0, 112.0),
            _word("added", 41.0, 72.0, 100.0, 112.0),
            _word("and", 76.0, 93.0, 100.0, 112.0),
            _word("C", 97.0, 106.0, 100.0, 112.0),
            _word("matches", 124.0, 162.0, 100.0, 112.0),
            _word("C", 166.0, 175.0, 100.0, 112.0),
            _word("and", 190.0, 207.0, 100.0, 112.0),
            _word("C", 211.0, 220.0, 100.0, 112.0),
            _word(".", 260.0, 263.0, 100.0, 112.0),
            _word("G", 17.9, 26.0, 104.8, 114.4),
            _word("eeq", 105.9, 122.0, 104.8, 114.4),
            _word("eq", 174.9, 188.0, 104.8, 114.4),
            _word("eeq_db", 219.9, 258.0, 104.8, 114.4),
        ]
        raw = "\uf073 is added and C matches C and C .\nG eeq eq eeq_db"

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "\uf073G is added and Ceeq matches Ceq and Ceeq_db .",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_coordinate_proven_return_loss_suffix_rejoins(self) -> None:
        """The lowered `cd` belongs to RL; ordinary adjacent text stays separate."""

        words = [
            _word("limit", 10.0, 35.0, 100.0, 112.0),
            _word("RL", 40.0, 55.0, 100.0, 112.0),
            _word("(f)", 65.5, 78.0, 100.0, 112.0),
            _word("applies.", 82.0, 119.0, 100.0, 112.0),
            _word("cd", 54.9, 65.0, 104.8, 114.4),
        ]
        raw = "limit RL (f) applies.\ncd"

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual("limit RLcd (f) applies.", repaired)
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_signed_fraction_voltage_subscripts_rejoin_and_leave_line_number(self) -> None:
        """Lowered signed fractions belong to V, while the margin number remains separate."""

        words = [
            _word("where", 10.0, 35.0, 100.0, 112.0),
            _word("V", 40.0, 48.0, 100.0, 112.0),
            _word(",", 59.0, 61.0, 100.0, 112.0),
            _word("V", 66.0, 74.0, 100.0, 112.0),
            _word(",", 93.0, 95.0, 100.0, 112.0),
            _word("V", 100.0, 108.0, 100.0, 112.0),
            _word(",", 127.0, 129.0, 100.0, 112.0),
            _word("and", 134.0, 151.0, 100.0, 112.0),
            _word("V", 156.0, 164.0, 100.0, 112.0),
            _word("are", 174.0, 190.0, 100.0, 112.0),
            _word("defined.", 194.0, 232.0, 100.0, 112.0),
            _word("-1", 47.9, 58.0, 104.8, 114.4),
            _word("-1/3", 73.9, 92.0, 104.8, 114.4),
            _word("1/3", 107.9, 126.0, 104.8, 114.4),
            _word("1", 163.9, 171.0, 104.8, 114.4),
            _word("42", 520.0, 532.0, 104.8, 114.4),
        ]
        raw = "where V , V , V , and V are defined.\n-1 -1/3 1/3 1 42"

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "where V-1 , V-1/3 , V1/3 , and V1 are defined.\n42",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_coordinate_proven_subscripts_rejoin_without_losing_characters(self) -> None:
        words = [
            _word("JH", 10.0, 20.0, 100.0, 112.0),
            _word(",", 31.0, 33.0, 100.0, 112.0),
            _word("JH", 40.0, 50.0, 100.0, 112.0),
            _word(",", 67.0, 69.0, 100.0, 112.0),
            _word("and", 72.0, 87.0, 100.0, 112.0),
            _word("EOJ", 90.0, 110.0, 100.0, 112.0),
            _word("measurements", 123.0, 180.0, 100.0, 112.0),
            _word("4.3u", 19.9, 30.0, 104.8, 114.4),
            _word("RMS", 49.9, 66.0, 104.8, 114.4),
            _word("03", 109.9, 122.0, 104.8, 114.4),
            _word("the", 10.0, 22.0, 130.0, 142.0),
            _word("Q", 25.0, 33.0, 130.0, 142.0),
            _word("becomes", 41.0, 75.0, 130.0, 142.0),
            _word("Q", 79.0, 87.0, 130.0, 142.0),
            _word("4", 32.9, 40.0, 134.8, 144.4),
            _word("3", 86.9, 94.0, 134.8, 144.4),
        ]
        raw = (
            "JH , JH , and EOJ measurements\n"
            "4.3u RMS 03\n"
            "the Q becomes Q\n"
            "4 3"
        )

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "JH4.3u , JHRMS , and EOJ03 measurements\n"
            "the Q4 becomes Q3",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_ambiguous_suffix_shared_by_two_bases_is_not_reordered(self) -> None:
        words = [
            _word("JH", 10.0, 20.0, 100.0, 112.0),
            _word("Q", 20.5, 23.0, 100.0, 112.0),
            _word("4", 23.5, 29.0, 104.8, 114.4),
        ]
        raw = "JH Q\n4"

        self.assertEqual(raw, _repair_body_visual_subscript_order(raw, words))

    def test_smaller_word_on_an_ordinary_next_line_is_not_reordered(self) -> None:
        words = [
            _word("JH", 10.0, 20.0, 100.0, 112.0),
            _word("4u", 20.0, 30.0, 113.0, 122.6),
        ]
        raw = "JH\n4u"

        self.assertEqual(raw, _repair_body_visual_subscript_order(raw, words))

    def test_incomplete_coordinate_coverage_revokes_reordering(self) -> None:
        words = [
            _word("JH", 10.0, 20.0, 100.0, 112.0),
            _word("4u", 20.0, 30.0, 104.8, 114.4),
        ]
        raw = "JH clause\n4u"

        self.assertEqual(raw, _repair_body_visual_subscript_order(raw, words))

    def test_repeated_identical_base_lines_revoke_occurrence_binding(self) -> None:
        words = [
            _word("JH", 10.0, 20.0, 100.0, 112.0),
            _word("4u", 20.0, 30.0, 104.8, 114.4),
            _word("JH", 10.0, 20.0, 140.0, 152.0),
        ]
        raw = "JH\n4u\nJH"

        self.assertEqual(raw, _repair_body_visual_subscript_order(raw, words))

    def test_repeated_suffix_lines_bind_only_through_unique_adjacent_bases(self) -> None:
        words = [
            _word("First", 10.0, 27.0, 100.0, 112.0),
            _word("JH", 30.0, 40.0, 100.0, 112.0),
            _word("and", 53.0, 70.0, 100.0, 112.0),
            _word("JH", 80.0, 90.0, 100.0, 112.0),
            _word("3u", 39.9, 50.0, 104.8, 114.4),
            _word("RMS", 89.9, 105.0, 104.8, 114.4),
            _word("Second", 10.0, 29.0, 130.0, 142.0),
            _word("JH", 30.0, 40.0, 130.0, 142.0),
            _word("and", 53.0, 70.0, 130.0, 142.0),
            _word("JH", 80.0, 90.0, 130.0, 142.0),
            _word("3u", 39.9, 50.0, 134.8, 144.4),
            _word("RMS", 89.9, 105.0, 134.8, 144.4),
        ]
        raw = "First JH and JH\n3u RMS\nSecond JH and JH\n3u RMS"

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "First JH3u and JHRMS\nSecond JH3u and JHRMS",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_subscripts_already_interleaved_on_one_raw_line_rejoin(self) -> None:
        words = [
            _word("The", 10.0, 25.0, 100.0, 112.0),
            _word("parameters", 28.0, 75.0, 100.0, 112.0),
            _word("J", 78.0, 84.0, 100.0, 112.0),
            _word(",", 105.5, 108.0, 100.0, 112.0),
            _word("J4u", 111.0, 130.0, 100.0, 112.0),
            _word(",", 141.5, 144.0, 100.0, 112.0),
            _word("and", 147.0, 164.0, 100.0, 112.0),
            _word("EOJ", 167.0, 188.0, 100.0, 112.0),
            _word("are", 202.0, 220.0, 100.0, 112.0),
            _word("defined.", 223.0, 260.0, 100.0, 112.0),
            _word("RMS", 83.9, 105.0, 104.8, 114.4),
            _word("03", 129.9, 141.0, 104.8, 114.4),
            _word("03", 187.9, 199.0, 104.8, 114.4),
        ]
        raw = "The parameters J RMS , J4u 03 , and EOJ 03 are defined."

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "The parameters JRMS , J4u03 , and EOJ03 are defined.",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_same_baseline_small_text_is_not_mistaken_for_a_subscript(self) -> None:
        raw = "Clause\n3"
        for bottom in (112.0, 113.0):
            with self.subTest(bottom=bottom):
                words = [
                    _word("Clause", 10.0, 48.0, 100.0, 112.0),
                    _word("3", 51.0, 57.0, 104.0, bottom),
                ]
                self.assertEqual(
                    raw,
                    _repair_body_visual_subscript_order(raw, words),
                )

    def test_touching_lowered_suffix_at_real_pdf_boundary_is_rejoined(self) -> None:
        """A genuine touching subscript must survive small revision layout drift."""

        raw = "SNR\nTX"
        words = [
            _word("SNR", 10.0, 30.0, 630.9677, 642.9677),
            _word("TX", 29.9, 40.0, 636.1798675, 644.1797675),
        ]

        self.assertEqual(
            "SNRTX",
            _repair_body_visual_subscript_order(raw, words),
        )

    def test_exact_duplicate_diagram_words_do_not_block_unique_body_subscript(self) -> None:
        """Unrelated overlapping figure labels must not veto a lossless body repair."""

        raw = "The output uses SNR\nISI\nSSccooppee"
        deduplicated_words = [
            _word("The", 10.0, 25.0, 100.0, 112.0),
            _word("output", 28.0, 60.0, 100.0, 112.0),
            _word("uses", 63.0, 84.0, 100.0, 112.0),
            _word("SNR", 87.0, 107.0, 100.0, 112.0),
            _word("ISI", 106.9, 119.0, 104.65, 114.25),
            _word("Scope", 10.0, 40.0, 140.0, 152.0),
        ]
        raw_words = [
            *deduplicated_words,
            _word("Scope", 10.0, 40.0, 140.0, 152.0),
        ]

        class _Page:
            def extract_words(self, **_kwargs: object) -> list[dict[str, float | str]]:
                return raw_words

        repaired = _repair_body_visual_subscript_order_with_duplicate_fallback(
            raw,
            _Page(),
            deduplicated_words,
            ["第 17 页跳过重复坐标词。"],
            None,
        )

        self.assertEqual("The output uses SNRISI\nSSccooppee", repaired)
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_duplicate_fallback_does_not_join_ordinary_mode_enumeration(self) -> None:
        """A lowered ordinary letter remains prose even with duplicate-word fallback."""

        raw = "Operating Mode\nA\nSSccooppee"
        deduplicated_words = [
            _word("Operating", 10.0, 55.0, 100.0, 112.0),
            _word("Mode", 58.0, 83.0, 100.0, 112.0),
            _word("A", 82.9, 89.0, 104.8, 114.4),
            _word("Scope", 10.0, 40.0, 140.0, 152.0),
        ]
        raw_words = [
            *deduplicated_words,
            _word("Scope", 10.0, 40.0, 140.0, 152.0),
        ]

        class _Page:
            def extract_words(self, **_kwargs: object) -> list[dict[str, float | str]]:
                return raw_words

        repaired = _repair_body_visual_subscript_order_with_duplicate_fallback(
            raw,
            _Page(),
            deduplicated_words,
            ["第 17 页跳过重复坐标词。"],
            None,
        )

        self.assertEqual(raw, repaired)

    def test_punctuation_wrapped_trailing_symbols_use_the_same_geometry(self) -> None:
        words = [
            _word("clauses", 10.0, 45.0, 100.0, 112.0),
            _word("179.9.4.7.1(JH", 50.0, 130.0, 100.0, 112.0),
            _word("),", 150.0, 158.0, 100.0, 112.0),
            _word("and", 162.0, 180.0, 100.0, 112.0),
            _word("(EOJ", 184.0, 230.0, 100.0, 112.0),
            _word(")", 244.0, 250.0, 100.0, 112.0),
            _word("RMS", 129.9, 149.0, 104.8, 114.4),
            _word("03", 229.9, 243.0, 104.8, 114.4),
        ]
        raw = "clauses 179.9.4.7.1(JH ), and (EOJ )\nRMS 03"

        repaired = _repair_body_visual_subscript_order(raw, words)

        self.assertEqual(
            "clauses 179.9.4.7.1(JHRMS ), and (EOJ03 )",
            repaired,
        )
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "local 058 real-PDF regression corpus is unavailable",
    )
    def test_real_058_page_17_rejoins_body_subscripts(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
            17,
            17,
        )
        text = extraction.pages[0].text

        self.assertIn("JH4.3u , JHRMS , and EOJ03 measurements", text)
        self.assertIn("179.9.4.7.1(JHRMS )", text)
        self.assertIn("179.9.4.7.2(JH4u )", text)
        self.assertIn("(EOJ03 ) respectively.", text)
        self.assertIn("The JH4.3u is described", text)
        self.assertIn("the JH4u is replaced with JH4.3u", text)
        self.assertIn("Q4 parameter is replaced with a Q4.3", text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "local 235 real-PDF regression corpus is unavailable",
    )
    def test_real_235_page_18_rejoins_body_subscripts(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf",
            18,
            18,
        )
        text = extraction.pages[0].text

        self.assertIn("JH3u , JHRMS , and EOJ03 measurements", text)
        self.assertIn("179.9.4.7.1(JHRMS )", text)
        self.assertIn("179.9.4.7.2(JH4u )", text)
        self.assertIn("(EOJ03 ) respectively.", text)
        self.assertIn("The JH3u is described", text)
        self.assertIn("the JH4u is replaced with JH3u", text)
        self.assertIn("Q4 parameter is replaced with a Q3", text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "local 235 real-PDF regression corpus is unavailable",
    )
    def test_real_235_page_21_rejoins_snrtx_subscript(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf",
            21,
            21,
        )

        self.assertIn(
            "used for SNRTX in the COM calculation",
            extraction.pages[0].text,
        )
        self.assertNotIn("levels are set TX such", extraction.pages[0].text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "local 532 new-revision regression PDF is unavailable",
    )
    def test_real_532_page_17_rejoins_snrisi_despite_duplicate_figure_words(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            16,
            18,
        )
        text = extraction.pages[1].text

        self.assertIn("The host output SNDR, SNRISI and output jitter", text)
        self.assertNotIn("\nISI\n", text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file(),
        "local 058 old-revision regression PDF is unavailable",
    )
    def test_real_058_old_revision_rejoins_formula_subscripts(self) -> None:
        page_17 = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            17,
            17,
        ).pages[0].text
        page_22 = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            22,
            22,
        ).pages[0].text

        self.assertIn("32.3.1.7.1 J4.3u and JRMS Jitter", page_17)
        self.assertIn("obtain a set Si =", page_17)
        self.assertIn("{ti (1), ti (2), ...}", page_17)
        self.assertIn("each set Si , Tavgi", page_17)
        self.assertIn("set S0i = {ti (1) - Tavgi", page_17)
        self.assertIn("probability distribution fJ (t)", page_17)
        self.assertIn("JRMS is defined as the standard deviation of fJ (t)", page_17)
        self.assertIn(
            "measured for J4.3u03 and JRMS03 include the effects",
            page_22,
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file(),
        "local 058 old-revision regression PDF is unavailable",
    )
    def test_real_058_old_threshold_voltage_subscripts_are_not_orphaned(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            13,
            17,
        )
        text = extraction.pages[2].text

        self.assertIn("where V-1 , V-1/3 , V1/3 , and V1 are as defined", text)
        self.assertNotIn("\n-1 -1/3 1/3", text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "local 058 real-PDF regression corpus is unavailable",
    )
    def test_real_058_return_loss_subscript_matches_across_revisions(self) -> None:
        """The visual `RL_cd` suffix is one symbol on both shifted source pages."""

        old_text = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            20,
            20,
        ).pages[0].text
        new_text = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
            19,
            19,
        ).pages[0].text

        for text in (old_text, new_text):
            self.assertIn("return loss limit RLcd (f) is shown", text)
            self.assertNotIn("return loss limit RL cd (f) is shown", text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "local 532 real-PDF regression corpus is unavailable",
    )
    def test_real_532_output_jitter_subscripts_match_across_revisions(self) -> None:
        expected = (
            "The output jitter parameters JRMS , J4u03 , and EOJ03 "
            "are defined in 179.9.4.6."
        )
        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf",
            16,
            16,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            15,
            15,
        )

        self.assertIn(expected, old_extraction.pages[0].text)
        self.assertIn(expected, new_extraction.pages[0].text)


class SectionAnchorReadingOrderTests(unittest.TestCase):
    formula_block = (
        "SCC22 = 0 1 2 3 4 5 6 7 8 9 ≥ 10 × 11 + 12 - 13 / 14; " * 4
    ).strip()

    def _misordered_page(
        self,
        moved_text: str,
    ) -> tuple[str, list[dict[str, float | str]], str]:
        heading = "31.3.10 Steady-State Voltage and Linear Fit Pulse Peak Ratio"
        body = "The receiver response is measured after the formula."
        words = [
            _word(moved_text, 72.0, 520.0, 100.0, 112.0),
            _word("31.3.10", 72.0, 120.0, 200.0, 212.0),
            _word("Steady-State", 124.0, 190.0, 200.0, 212.0),
            _word("Voltage", 194.0, 235.0, 200.0, 212.0),
            _word("and", 239.0, 257.0, 200.0, 212.0),
            _word("Linear", 261.0, 295.0, 200.0, 212.0),
            _word("Fit", 299.0, 316.0, 200.0, 212.0),
            _word("Pulse", 320.0, 351.0, 200.0, 212.0),
            _word("Peak", 355.0, 381.0, 200.0, 212.0),
            _word("Ratio", 385.0, 415.0, 200.0, 212.0),
            _word("The", 72.0, 90.0, 220.0, 232.0),
            _word("receiver", 94.0, 136.0, 220.0, 232.0),
            _word("response", 140.0, 185.0, 220.0, 232.0),
            _word("is", 189.0, 199.0, 220.0, 232.0),
            _word("measured", 203.0, 250.0, 220.0, 232.0),
            _word("after", 254.0, 280.0, 220.0, 232.0),
            _word("the", 284.0, 301.0, 220.0, 232.0),
            _word("formula.", 305.0, 350.0, 220.0, 232.0),
        ]
        raw = f"{heading}\n{body}\n{moved_text}"
        coordinate_order = f"{moved_text}\n{heading}\n{body}"
        return raw, words, coordinate_order

    def test_large_lossless_block_crossing_one_unique_heading_uses_y_first_order(self) -> None:
        raw, words, coordinate_order = self._misordered_page(self.formula_block)

        repaired = _repair_unique_section_anchor_reading_order(raw, words)

        self.assertEqual(coordinate_order, repaired)
        self.assertEqual(_visible_characters(raw), _visible_characters(repaired))

    def test_small_heading_offset_does_not_replace_native_order(self) -> None:
        raw, words, _coordinate_order = self._misordered_page("F" * 80)

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(raw, words),
        )

    def test_coordinate_character_mismatch_revokes_heading_reorder(self) -> None:
        raw, words, _coordinate_order = self._misordered_page(self.formula_block)
        raw = f"{raw}X"

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(raw, words),
        )

    def test_repeated_multilevel_heading_is_not_a_unique_anchor(self) -> None:
        raw, words, _coordinate_order = self._misordered_page(self.formula_block)
        duplicate_heading_words = [
            dict(word, top=float(word["top"]) + 60.0, bottom=float(word["bottom"]) + 60.0)
            for word in words[1:10]
        ]
        heading = "31.3.10 Steady-State Voltage and Linear Fit Pulse Peak Ratio"
        raw = f"{raw}\n{heading}"

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(
                raw,
                [*words, *duplicate_heading_words],
            ),
        )

    def test_heading_repair_rejects_unrelated_post_anchor_reordering(self) -> None:
        heading = "31.3.10 Steady-State Voltage and Linear Fit Pulse Peak Ratio"
        first = "Alpha requirement remains first."
        second = "Beta requirement remains second."
        moved = self.formula_block
        raw = f"{heading}\n{first}\n{second}\n{moved}"
        words = [
            _word(moved, 72.0, 520.0, 100.0, 112.0),
            _word(heading, 72.0, 500.0, 200.0, 212.0),
            _word(second, 72.0, 320.0, 220.0, 232.0),
            _word(first, 72.0, 320.0, 240.0, 252.0),
        ]

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(raw, words),
        )

    def test_heading_repair_never_moves_an_ordinary_prose_block(self) -> None:
        moved = (
            "The receiver operating points are x = 1, 2, 3, 4 and remain "
            "acceptable for all supported channels. The explanatory values are "
            "examples and this ordinary requirement must retain its section."
        )
        raw, words, _coordinate_order = self._misordered_page(moved)

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(raw, words),
        )

    def test_heading_repair_rejects_normative_insertion_loss_axis_sentence(self) -> None:
        moved = (
            "The insertion loss requirements shall be 1 2 3 4 5 6 7 8 9 10 dB "
            "over the frequency range, and the receiver must preserve every "
            "specified limit for all supported channels and operating modes."
        )
        raw, words, _coordinate_order = self._misordered_page(moved)

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(raw, words),
        )

    def test_heading_repair_rejects_numbered_normative_axis_sentence(self) -> None:
        moved = (
            "1. The insertion loss requirements shall be 1 2 3 4 5 6 7 8 9 10 dB "
            "over the frequency range, and the receiver must preserve every "
            "specified limit for all supported channels and operating modes."
        )
        raw, words, _coordinate_order = self._misordered_page(moved)

        self.assertEqual(
            raw,
            _repair_unique_section_anchor_reading_order(raw, words),
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "local 532 real-PDF regression corpus is unavailable",
    )
    def test_real_532_page_13_keeps_scc22_formula_before_section_31_3_10(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            13,
            13,
        )
        text = extraction.pages[0].text

        self.assertLess(text.index("SCC22"), text.index("31.3.10 Steady-State"))
        self.assertEqual(1, text.count("31.3.10 Steady-State"))


class PrintedLineNumberDuplicateTests(unittest.TestCase):
    page_width = 600.0
    page_height = 800.0
    origin = 90.0
    pitch = 12.0

    def _grid_word(
        self,
        value: int,
        *,
        center: float | None = None,
    ) -> dict[str, float | str]:
        resolved_center = (
            self.origin + (value - 1) * self.pitch
            if center is None
            else center
        )
        return _word(
            str(value),
            548.0,
            558.0,
            resolved_center - 5.0,
            resolved_center + 5.0,
        )

    def test_duplicate_technical_values_do_not_displace_the_true_print_grid(self) -> None:
        true_grid = [self._grid_word(value) for value in range(1, 50)]
        duplicate_technical_values = [
            self._grid_word(2, center=330.0),
            self._grid_word(2, center=470.0),
        ]
        cluster = [*true_grid, *duplicate_technical_values]

        metrics = _printed_line_number_grid_metrics(
            cluster,
            all_words=cluster,
            page_width=self.page_width,
            page_height=self.page_height,
        )

        self.assertIsNotNone(metrics)
        assert metrics is not None
        boxes, origin, pitch = metrics
        self.assertEqual(49, len(boxes))
        self.assertEqual(
            (
                float(true_grid[1]["x0"]),
                float(true_grid[1]["top"]),
                float(true_grid[1]["x1"]),
                float(true_grid[1]["bottom"]),
            ),
            boxes[1],
        )
        self.assertAlmostEqual(self.origin, origin, places=6)
        self.assertAlmostEqual(self.pitch, pitch, places=6)

    def test_too_few_singleton_values_cannot_choose_between_two_grids(self) -> None:
        true_grid = [self._grid_word(value) for value in range(1, 50)]
        competing_candidates = [
            self._grid_word(value, center=250.0 + value * 4.0)
            for value in range(1, 31)
        ]
        cluster = [*true_grid, *competing_candidates]

        self.assertIsNone(
            _printed_line_number_grid_metrics(
                cluster,
                all_words=cluster,
                page_width=self.page_width,
                page_height=self.page_height,
            )
        )

    def test_singleton_anchors_that_do_not_fit_one_grid_remain_unfiltered(self) -> None:
        cluster = [self._grid_word(value) for value in range(1, 50)]
        cluster[24] = self._grid_word(25, center=self.origin + 24 * self.pitch + 9.0)

        self.assertIsNone(
            _printed_line_number_grid_metrics(
                cluster,
                all_words=cluster,
                page_width=self.page_width,
                page_height=self.page_height,
            )
        )

    def test_short_numbered_list_is_not_a_print_grid(self) -> None:
        cluster = [self._grid_word(value) for value in range(1, 21)]

        self.assertIsNone(
            _printed_line_number_grid_metrics(
                cluster,
                all_words=cluster,
                page_width=self.page_width,
                page_height=self.page_height,
            )
        )

    def test_distinct_observed_print_grid_lengths_are_supported(self) -> None:
        for length in (32, 40, 50, 56):
            with self.subTest(length=length):
                cluster = [self._grid_word(value) for value in range(1, length + 1)]
                metrics = _printed_line_number_grid_metrics(
                    cluster, all_words=cluster, page_width=self.page_width,
                    page_height=self.page_height,
                )
                self.assertIsNotNone(metrics)
                self.assertEqual(length, len(metrics[0]))

    def test_value_outside_observed_print_grid_revokes_filtering(self) -> None:
        # A valid 50-line publication is supported; an off-grid technical value is not.
        cluster = [self._grid_word(value) for value in range(1, 50)]
        cluster.append(self._grid_word(101))

        self.assertIsNone(
            _printed_line_number_grid_metrics(
                cluster,
                all_words=cluster,
                page_width=self.page_width,
                page_height=self.page_height,
            )
        )

    def test_split_missing_two_digit_grid_labels_use_exact_fragment_boxes(self) -> None:
        cluster = [
            self._grid_word(value)
            for value in range(1, 50)
            if value not in {15, 16}
        ]
        split_fragments = [
            _word("1", 548.0, 552.9, self.origin + 14 * self.pitch - 5.0, self.origin + 14 * self.pitch + 5.0),
            _word("5", 553.0, 558.0, self.origin + 14 * self.pitch - 5.0, self.origin + 14 * self.pitch + 5.0),
            _word("1", 548.0, 552.9, self.origin + 15 * self.pitch - 5.0, self.origin + 15 * self.pitch + 5.0),
            _word("6", 553.0, 558.0, self.origin + 15 * self.pitch - 5.0, self.origin + 15 * self.pitch + 5.0),
        ]

        metrics = _printed_line_number_grid_metrics(
            cluster,
            all_words=[*cluster, *split_fragments],
            page_width=self.page_width,
            page_height=self.page_height,
        )

        self.assertIsNotNone(metrics)
        assert metrics is not None
        boxes, _origin, _pitch = metrics
        self.assertEqual(51, len(boxes))
        for fragment in split_fragments:
            self.assertIn(
                (
                    float(fragment["x0"]),
                    float(fragment["top"]),
                    float(fragment["x1"]),
                    float(fragment["bottom"]),
                ),
                boxes,
            )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "local 235 real-PDF regression corpus is unavailable",
    )
    def test_real_235_page_22_removes_print_grid_but_keeps_technical_numbers(self) -> None:
        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf"
        )
        text = extraction.pages[21].text
        standalone_numbers = {
            int(line)
            for line in text.splitlines()
            if line.isdigit() and 1 <= int(line) <= 49
        }

        self.assertEqual(set(), standalone_numbers)
        self.assertIn("9. This sinusoidal jitter", text)
        self.assertIn("13 as specified in Table 33-8.", text)
        self.assertNotIn("(33-9) 1 1 5 6", text)
        self.assertNotIn("JH and JH\n3u RMS", text)
        self.assertIn("JH3u and JHRMS", text)


if __name__ == "__main__":
    unittest.main()
