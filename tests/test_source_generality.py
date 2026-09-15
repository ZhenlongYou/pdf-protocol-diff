"""Source-level and behavioral guards for the document-agnostic core."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.pdf_extract import _clean_extracted_page_text
from protocol_pdf_diff.sectioning import detect_heading
from protocol_pdf_diff.text_utils import normalize_line


class SourceGeneralityTests(unittest.TestCase):
    """Protect behavior that must not depend on a particular PDF family."""

    def test_invisible_pdf_control_characters_do_not_create_text_differences(self) -> None:
        """Known extraction artifacts must not become reported text edits."""

        clean_sentence = (
            "The interoperability profile shall preserve calibration evidence "
            "for every documented configuration."
        )
        artifact_sentence = (
            "The inter\u00adoperability pro\u200bfile shall preserve calibration "
            "evidence for every docu\u2060mented configu\ufeffration."
        )
        shared_context = (
            "Each implementation shall retain traceable requirements, limits, and "
            "review records throughout validation. "
        ) * 8
        old = ExtractionResult(
            pdf_path=Path("old-invisible-controls.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{shared_context}\n{clean_sentence}")],
        )
        new = ExtractionResult(
            pdf_path=Path("new-invisible-controls.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{shared_context}\n{artifact_sentence}")],
        )

        result = compare_extractions(old, new, DiffOptions())

        self.assertEqual([], result.changes)

    def test_semantic_invisible_controls_are_preserved(self) -> None:
        """Language, direction, and mathematical controls can carry real meaning."""

        semantic_controls = (
            "\u200c\u200d"  # zero-width non-joiner and joiner
            "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e"  # bidi controls
            "\u2061\u2062\u2063\u2064"  # invisible mathematical operators
            "\u2066\u2067\u2068\u2069"  # bidi isolates
        )
        source = f"A{semantic_controls}B"

        self.assertEqual(source, normalize_line(source))

    def test_case_sensitive_technical_tokens_remain_visible(self) -> None:
        """Case changes in identifiers and normative keywords can change meaning."""

        for old_token, new_token in (
            ("MODE_FAST", "mode_fast"),
            ("RegisterX", "registerx"),
            ("MUST", "must"),
        ):
            with self.subTest(old_token=old_token, new_token=new_token):
                result = _compare_body_lines(
                    f"The recorded technical token is {old_token}.",
                    f"The recorded technical token is {new_token}.",
                )
                self.assertTrue(result.changes)

        ordinary_case = _compare_body_lines(
            "Scope requirements remain unchanged.",
            "scope requirements remain unchanged.",
        )
        self.assertEqual([], ordinary_case.changes)
        for titlecase_word in ("Use", "Set", "Add", "New"):
            with self.subTest(titlecase_word=titlecase_word):
                old_text = f"{titlecase_word} the receiver."
                new_text = f"{titlecase_word.casefold()} the receiver."
                self.assertEqual([], _compare_body_lines(old_text, new_text).changes)

    def test_titlecase_changes_in_explicit_identifier_context_remain_visible(self) -> None:
        """Assignments and call syntax prove that a token is technical, not prose."""

        for old_text, new_text in (
            ("Formula foo = 1.", "Formula Foo = 1."),
            ("Return parse(value).", "Return Parse(value)."),
            ("mode = fast", "mode = Fast"),
            ("Call foo().", "Call Foo()."),
            ("Set enum Active.", "Set enum active."),
            ("Set mode Active.", "Set mode active."),
            ("State Idle", "State idle"),
            ("Value=True", "Value=true"),
            ("Path /Config/settings", "Path /config/settings"),
            ("Path /Config.", "Path /config."),
            (r"Path C:\Config\Settings.", r"Path C:\config\Settings."),
            (r"Path \\Server\Share.", r"Path \\server\Share."),
            (
                r"Registry HKLM\Software\Vendor.",
                r"Registry HKLM\software\Vendor.",
            ),
            ("Use `Active`.", "Use `active`."),
            ("Command=--force", "Command=force"),
            ("Class: Foo", "Class: foo"),
            ("Mode: active", "mode: active"),
            ("Function: Parse", "Function: parse"),
            ("Identifier: Widget", "Identifier: widget"),
            ("Command: Read", "Command: read"),
            ("File: Config", "File: config"),
            (
                "The XML element <Mode> shall be present.",
                "The XML element <mode> shall be present.",
            ),
            (
                "The variable $Config shall be set.",
                "The variable $config shall be set.",
            ),
            ("Match /Active/ exactly.", "Match /active/ exactly."),
            ("The YAML key Mode: enabled.", "The YAML key mode: enabled."),
            ("Use annotation @Mode.", "Use annotation @mode."),
            ("Use URI fragment #Mode.", "Use URI fragment #mode."),
            ("Define macro Mode.", "Define macro mode."),
            ("Call f(foo, Active).", "Call f(foo, active)."),
            ("Formula y = offset + Active.", "Formula y = offset + active."),
            ("Use list [foo, Active].", "Use list [foo, active]."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_identifier_token_boundaries_default_to_literal_outside_measurements(self) -> None:
        """Unknown labeled values stay literal; only proven measurements fold spacing."""

        for label in (
            "Opcode",
            "Encoding",
            "Command",
            "Register",
            "Address",
            "Name",
            "Filename",
        ):
            with self.subTest(label=label):
                self.assertTrue(
                    _compare_body_lines(f"{label} 1A", f"{label} 1 A").changes
                )

        for old_text, new_text in (
            ("Voltage limit 5V", "Voltage limit 5 V"),
            ("Current maximum 1A", "Current maximum 1 A"),
        ):
            with self.subTest(old_text=old_text):
                self.assertEqual([], _compare_body_lines(old_text, new_text).changes)

    def test_micro_glyph_folds_only_in_proven_measurement_units(self) -> None:
        """Greek mu is not the Latin letter u inside symbols or identifiers."""

        self.assertTrue(
            _compare_body_lines("Symbol μcode", "Symbol ucode").changes
        )
        self.assertEqual(
            [],
            _compare_body_lines(
                "Delay limit 5 μs",
                "Delay limit 5 us",
            ).changes,
        )

    def test_spaced_math_operator_changes_remain_visible(self) -> None:
        """A plus/minus edit is semantic even when both operands are unchanged."""

        result = _compare_body_lines(
            "The output shall use A + B during calibration.",
            "The output shall use A - B during calibration.",
        )

        self.assertTrue(result.changes)

        self.assertTrue(_compare_body_lines("Value=5+3", "Value=5 3").changes)
        self.assertTrue(
            _compare_body_lines(
                "Formula valid = ready & enabled.",
                "Formula valid = ready and enabled.",
            ).changes
        )
        self.assertTrue(
            _compare_body_lines(
                "Expression ready & enabled.",
                "Expression ready and enabled.",
            ).changes
        )

    def test_single_letter_case_changes_remain_visible(self) -> None:
        """Single-letter units, variables, and states retain their observed case."""

        for old_text, new_text in (
            ("The limit is 5 V.", "The limit is 5 v."),
            ("Select variable X.", "Select variable x."),
            ("Set state A.", "Set state a."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

        for old_text, new_text in (
            ("A receiver shall operate.", "a receiver shall operate."),
            ("I shall record the result.", "i shall record the result."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertEqual([], _compare_body_lines(old_text, new_text).changes)

    def test_directional_arrow_changes_remain_visible(self) -> None:
        """Arrow glyph variants normalize by direction, never to missing direction."""

        for old_arrow, new_arrow in (("->", "<-"), ("→", "←"), ("->", "")):
            with self.subTest(old_arrow=old_arrow, new_arrow=new_arrow):
                result = _compare_body_lines(
                    f"The permitted transition is State A {old_arrow} State B.",
                    f"The permitted transition is State A {new_arrow} State B.",
                )
                self.assertTrue(result.changes)

        same_direction = _compare_body_lines(
            "The permitted transition is State A -> State B.",
            "The permitted transition is State A → State B.",
        )
        self.assertEqual([], same_direction.changes)

        equivalence_vs_bidirectional = _compare_body_lines(
            "State A <-> State B.",
            "State A <=> State B.",
        )
        self.assertTrue(equivalence_vs_bidirectional.changes)

    def test_less_than_negative_values_are_not_guessed_to_be_left_arrows(self) -> None:
        """ASCII ``<-`` is ambiguous before a number and must stay literal."""

        for old_text, new_text in (
            ("Require x < -5 dB.", "Require x <- 5 dB."),
            ("Require x < -5 dB.", "Require x ← 5 dB."),
            ("Limit x<-0.5 V.", "Limit x←0.5 V."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_word_internal_hyphen_is_not_globally_discarded(self) -> None:
        """A visible hyphen can change a word's meaning when it is not a line wrap."""

        for old_text, new_text in (("re-sign", "resign"), ("re-cover", "recover")):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_line_end_hyphen_is_not_removed_without_layout_evidence(self) -> None:
        """A text line break alone cannot prove that a visible hyphen is soft."""

        for old_text, new_text in (
            ("x-\ny", "xy"),
            ("A-\nb", "Ab"),
            ("signal-\nnoise", "signalnoise"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_grouping_and_interval_delimiters_remain_visible(self) -> None:
        """Parentheses and brackets carry formula and interval structure."""

        for old_text, new_text in (
            ("A and (B or C)", "(A and B) or C"),
            ("Y = A * (B + C)", "Y = (A * B) + C"),
            ("Range [1, 5]", "Range (1, 5)"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

        for old_text, new_text in (
            ("DATA[7:0]", "DATA[7 0]"),
            ("module.field", "module field"),
            ("(x,y)", "(x y)"),
            ("A.B.C", "A.B C"),
            ("1:2:3", "1:2 3"),
            ("Command MEAS?.", "Command MEAS."),
            ('Literal "ON"', "Literal ON"),
            ("Literal ‘ON’", "Literal ON"),
            ('Literal "MODE FAST"', "Literal MODE FAST"),
            ("Literal “MODE FAST”", "Literal MODE FAST"),
            ('Literal "ON/OFF"', "Literal ON/OFF"),
            ('Mode="Auto"', 'Mode="auto"'),
            ("Mode='Active'", "Mode='active'"),
            ("Use A.B then A B", "Use A B then A.B"),
            ('Literal "ON" then ON', 'Literal ON then "ON"'),
            ("tuple (x,y) then (x y)", "tuple (x y) then (x,y)"),
            ("A,B", "A B"),
            ("1,2", "1 2"),
            ("INIT;RUN", "INIT RUN"),
            ("A;B", "A B"),
            ("Allowed values are 1, 2.", "Allowed values are 1 2."),
            ("f(a, b)", "f(a b)"),
            ("Range [min, max]", "Range [min max]"),
            ("IPv6 address fe80::1", "IPv6 address fe80 1"),
            ("Namespace A::B", "Namespace A B"),
            ("Call obj..member", "Call obj member"),
            ("State A;;B", "State A B"),
            ("Values A,,B", "Values A B"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_punctuation_only_changes_are_ignored(self) -> None:
        """Standalone punctuation edits do not become report differences."""

        for old_text, new_text in (
            ("MEAS?", "MEAS"),
            ("ratio 1 : 4", "ratio 1 4"),
            ("Command Meas?", "Command Meas"),
            ("Poll STAT? then STAT", "Poll STAT then STAT?"),
            ("Modes are RX, TX, and LP.", "Modes are RX TX and LP."),
            ("Perform reset; continue.", "Perform reset continue."),
            ("Use args...", "Use args"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertFalse(_compare_body_lines(old_text, new_text).changes)

    def test_relation_set_and_logic_operator_changes_remain_visible(self) -> None:
        """Negation, membership, and logical operators are observable facts."""

        for old_text, new_text in (
            ("x != y", "x = y"),
            ("x ≠ y", "x = y"),
            ("x ∈ S", "x ∉ S"),
            ("A ∧ B", "A ∨ B"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_greek_and_unicode_letter_changes_remain_visible(self) -> None:
        """Technical tokens are Unicode-aware rather than Latin-only."""

        for old_text, new_text in (
            ("α = 0.10", "β = 0.10"),
            ("Δt = 5 ps", "δt = 5 ps"),
            ("λ1 = 3", "κ1 = 3"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_unit_and_modifier_symbols_are_not_silently_dropped(self) -> None:
        """Removing a visible unit or prime symbol changes the measured fact."""

        for old_text, new_text in (
            ("The tolerance is 5%.", "The tolerance is 5."),
            ("The load is 50 Ω.", "The load is 50."),
            ("The phase is 90°.", "The phase is 90."),
            ("Temperature is 25 °C.", "Temperature is 25 C."),
            ("Use x′.", "Use x."),
            ("Use x'.", "Use x."),
            ('The length is 5".', "The length is 5."),
            ("The length is 5'.", "The length is 5."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

        for old_text, new_text in (
            ("x²", "x2"),
            ("The area is 5 m².", "The area is 5 m2."),
            ("R₁", "R1"),
            ("x ∈ ℝ", "x ∈ R"),
            ("x ∈ ℂ", "x ∈ C"),
            ("Matrix 𝐀", "Matrix A"),
            ("Vector 𝐯", "Vector v"),
            ("Size 5' by 6\"", "Size 5\" by 6'"),
            ("x' + y\"", "x\" + y'"),
            ('Values 5" 5', 'Values 5 5"'),
            ("Values 5' 5", "Values 5 5'"),
            ('Lengths 5"; 5', 'Lengths 5; 5"'),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_technical_case_signatures_remain_bound_to_occurrence(self) -> None:
        """Moving uppercase meaning between repeated identifiers is a real change."""

        self.assertTrue(
            _compare_body_lines("Route RX to rx.", "Route rx to RX.").changes
        )
        self.assertTrue(_compare_body_lines("Route X to x.", "Route x to X.").changes)
        self.assertTrue(_compare_body_lines("Drive V to v.", "Drive v to V.").changes)

    def test_leading_zero_identifiers_remain_lexically_distinct(self) -> None:
        """Numeric-looking model, profile, and code values can use leading zeros."""

        for old_text, new_text in (
            ("Model 01", "Model 1"),
            ("Profile 001", "Profile 1"),
            ("Code 007", "Code 7"),
            ("Codes 01 then 1", "Codes 1 then 01"),
            ("Use 01/1", "Use 1/01"),
            ("Models 001 and 1", "Models 1 and 001"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_identifier_numbers_preserve_exact_visible_spelling(self) -> None:
        """Version/model/code labels turn numeric-looking values into identifiers."""

        for old_text, new_text in (
            ("Version 1.00", "Version 1.0"),
            ("Revision 1.20", "Revision 1.2"),
            ("Model 1e3", "Model 1000"),
            ("Code +5", "Code 5"),
            ("Code one", "Code 1"),
            ("ID one", "ID 1"),
            ("Version one", "Version 1"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

        for old_text, new_text in (
            ("Version v1.00", "Version v1.0"),
            ("Version 1.0.0", "Version 1.0.00"),
            ("Build r1.00", "Build r1.0"),
            ("Filename image1.00.bin", "Filename image1.0.bin"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_number_words_require_positive_count_context(self) -> None:
        """Number words in formulas, calls, and arbitrary identifiers stay literal."""

        for old_text, new_text in (
            ("Formula y = one + offset.", "Formula y = 1 + offset."),
            ("Return f(one).", "Return f(1)."),
            ("Set option one.", "Set option 1."),
            ("Select enum one.", "Select enum 1."),
            ("Use register one.", "Use register 1."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

        for old_text, new_text in (
            ("Capture seven waveforms.", "Capture 7 waveforms."),
            ("Retry after twenty one idle intervals.", "Retry after 21 idle intervals."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertEqual([], _compare_body_lines(old_text, new_text).changes)

    def test_number_words_in_generic_filenames_remain_literal(self) -> None:
        """Filename semantics are structural and cannot depend on an extension list."""

        for extension in ("bin", "hex", "cfg", "pcap", "yaml", "ini", "log"):
            with self.subTest(extension=extension):
                self.assertTrue(
                    _compare_body_lines(
                        f"Load one.{extension}",
                        f"Load 1.{extension}",
                    ).changes
                )

    def test_adjacent_numbers_are_not_guessed_to_be_exponents(self) -> None:
        """Only explicit multiplication/exponent evidence may join separated numbers."""

        for old_text, new_text in (
            ("Values 10 6", "Values 106"),
            ("Sequence 10 1", "Sequence 101"),
            ("Ports 10 4", "Ports 104"),
            ("Use 10 0 V", "Use 100 V"),
            ("Values 1. 2", "Values 1.2"),
            ("Steps 1. 2", "Steps 1.2"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_plain_multiplication_by_numbers_is_not_guessed_to_be_exponent_notation(self) -> None:
        """Only explicit exponent evidence may turn ``x10`` into scientific notation."""

        for old_text, new_text in (
            ("Value 2x100", "Value 2"),
            ("Use 4*100 V", "Use 4 V"),
            ("Matrix 3x101", "Matrix 30"),
            ("Value 5x106", "Value 5000000"),
            ("Result 5 × 10 - 6", "Result 5e-6"),
            ("Result 5 * 10 - 2", "Result 5e-2"),
            ("Formula y=5*10-6", "Formula y=0.000005"),
            ("Formula y=5*10+6", "Formula y=5000000"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_hexadecimal_literals_are_not_normalized_as_multiplication(self) -> None:
        """The ``0x`` prefix is structural in addresses, masks, and registers."""

        for old_text, new_text in (
            ("Address 0x10", "Address 0*10"),
            ("Mask 0xFF", "Mask 0*FF"),
            ("Register 0XCAFE", "Register 0×CAFE"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_negative_zero_sign_remains_visible(self) -> None:
        """Negative zero can carry IEEE or directional-encoding semantics."""

        for old_text, new_text in (
            ("Encoded value -0", "Encoded value 0"),
            ("Signed zero -0", "Signed zero +0"),
            ("Result -0e3", "Result 0"),
            ("Voltage -0 V", "Voltage 0 V"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_explicit_positive_sign_remains_visible(self) -> None:
        """A leading plus is observable in signed limits, encodings, and ranges."""

        for old_text, new_text in (
            ("Offset +5 V", "Offset 5 V"),
            ("Tolerance +0 V", "Tolerance 0 V"),
            ("Range -5 to +5 V", "Range -5 to 5 V"),
            ("Value +1e3", "Value 1000"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_same_line_decimal_punctuation_is_not_guessed_to_be_a_split_value(self) -> None:
        """Only a real extraction line break plus unit evidence may repair a decimal."""

        self.assertTrue(_compare_body_lines("Limit 3. 5 V", "Limit 3.5 V").changes)
        for old_text, new_text in (
            ("Use register 1.\n0 V is prohibited.", "Use register 1.0 V is prohibited."),
            ("Set mode 2.\n5 dB is the next limit.", "Set mode 2.5 dB is the next limit."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_measurement_unit_and_short_technical_case_is_semantic(self) -> None:
        """SI/unit case and compact Rx/Tx-style tokens retain their observed case."""

        for old_text, new_text in (
            ("Rate is 5 Mb/s", "Rate is 5 mb/s"),
            ("Frequency is 5 Hz", "Frequency is 5 hz"),
            ("Pressure is 5 Pa", "Pressure is 5 pa"),
            ("Capacity is 5 Ah", "Capacity is 5 ah"),
            ("Select Rx", "Select rx"),
            ("Select Tx", "Select tx"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_leading_list_marker_style_is_presentation_only(self) -> None:
        """Bullet glyph and numbered-list delimiter changes do not alter content."""

        for old_text, new_text in (
            ("• Enable mode", "● Enable mode"),
            ("● Enable mode", "Enable mode"),
            ("1. Enable mode", "1) Enable mode"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertEqual([], _compare_body_lines(old_text, new_text).changes)

        for old_text, new_text in (("- 5 V", "5 V"), ("* x", "x")):
            with self.subTest(old_text=old_text, new_text=new_text):
                self.assertTrue(_compare_body_lines(old_text, new_text).changes)

    def test_parent_heading_change_is_visible_through_child_path(self) -> None:
        """An empty container heading remains a fact through its descendant path."""

        shared = (
            "Every implementation shall preserve every declared requirement and traceable record. "
        ) * 8
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-parent.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 Transmit path\n1.1 Requirement\n{shared}",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-parent.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 Receive path\n1.1 Requirement\n{shared}",
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertTrue(result.changes)

    def test_page_fallback_preserves_technical_unit_case(self) -> None:
        """Heading-free fallback text must retain original case and units."""

        shared = (
            "Every recorded measurement shall remain traceable throughout validation. "
        ) * 9
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-fallback.pdf"),
                pages=[PageText(page_number=1, text=f"{shared}\nThe limit is 10 mW.")],
            ),
            ExtractionResult(
                pdf_path=Path("new-fallback.pdf"),
                pages=[PageText(page_number=1, text=f"{shared}\nThe limit is 10 MW.")],
            ),
            DiffOptions(),
        )

        visible = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )
        self.assertTrue(result.changes)
        self.assertIn("10 mW", visible)
        self.assertIn("10 MW", visible)

    def test_text_only_cleanup_preserves_inline_numeric_enumerations(self) -> None:
        """A 1..N sequence inside prose is data without coordinate gutter evidence."""

        old_line = "Supported channels: " + " ".join(str(value) for value in range(1, 13))
        new_line = "Supported channels: " + " ".join(str(value) for value in range(1, 14))

        self.assertIn(old_line, _clean_extracted_page_text(old_line))
        self.assertIn(new_line, _clean_extracted_page_text(new_line))

    def test_word_inflection_changes_are_not_guessed_to_be_equivalent(self) -> None:
        """Plural and verb inflections can alter a requirement's scope or force."""

        for old_word, new_word in (
            ("device", "devices"),
            ("supports", "support"),
            ("applies", "apply"),
        ):
            with self.subTest(old_word=old_word, new_word=new_word):
                result = _compare_body_lines(
                    f"The recorded requirement word is {old_word}.",
                    f"The recorded requirement word is {new_word}.",
                )
                self.assertTrue(result.changes)

    def test_adaptor_and_adapter_are_not_globally_equivalent(self) -> None:
        """A generic core must not silently merge domain terminology variants."""

        result = _compare_body_lines(
            "The assembly shall use the named Adaptor component.",
            "The assembly shall use the named Adapter component.",
        )

        self.assertTrue(result.changes)

    def test_standalone_single_letter_changes_are_visible(self) -> None:
        """A one-letter line may be a state, variable, or enum value."""

        for old_value, new_value in (("R", "T"), ("x", "y"), ("A", "B")):
            with self.subTest(old_value=old_value, new_value=new_value):
                result = _compare_body_lines(old_value, new_value)
                self.assertTrue(result.changes)

    def test_standalone_draft_status_changes_are_visible(self) -> None:
        """DRAFT is content unless extraction or layout evidence proves a watermark."""

        deleted = _compare_body_lines("DRAFT", "")
        replaced = _compare_body_lines("DRAFT", "FINAL")

        self.assertTrue(deleted.changes)
        self.assertTrue(replaced.changes)

    def test_generic_behavior_modules_have_no_publication_specific_literals(self) -> None:
        """Vendor names and sample-only aliases must not steer the shared core."""

        source_root = Path(__file__).resolve().parents[1] / "src" / "protocol_pdf_diff"
        presentation_or_fixture_modules = {"sample_data.py"}
        forbidden_patterns = {
            "OIF publication": re.compile(r"\boif(?:\s+cei)?\b"),
            "Optical Internetworking Forum": re.compile(
                r"\boptical\s+internetworking\s+forum\b"
            ),
            "PCIe publication": re.compile(r"\bpcie\b|\bpci\s+express\b"),
            "sample FFE residue": re.compile(r"\bffe\s+post\b|\bfx\s+bx\b"),
            "sample baud alias": re.compile(r"\br\s+baud\b"),
            "sample table family": re.compile(r"\bdevice\s+package\s+model\b"),
            "profile-only preset alias": re.compile(r"\bpreset\b"),
        }
        violations: list[str] = []
        for path in sorted(source_root.glob("*.py")):
            if path.name in presentation_or_fixture_modules:
                continue
            for line_number, literal in _behavior_string_literals(path):
                regex_neutral = re.sub(r"\\[a-zA-Z]", " ", literal.casefold())
                searchable = re.sub(r"[^a-z0-9]+", " ", regex_neutral)
                for label, pattern in forbidden_patterns.items():
                    if pattern.search(searchable):
                        violations.append(f"{path.name}:{line_number}: {label}")

        self.assertEqual([], violations, "\n".join(violations))

    def test_numbered_signal_title_is_not_assumed_to_be_sample_noise(self) -> None:
        """A short title remains a heading without layout evidence that it is a plot label."""

        heading = detect_heading("2 Signal 0")

        self.assertIsNotNone(heading)
        assert heading is not None
        self.assertEqual("2", heading.number)
        self.assertEqual("Signal 0", heading.title)


def _behavior_string_literals(path: Path) -> list[tuple[int, str]]:
    """Return string constants that execute as code, excluding documentation."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first_statement = node.body[0]
        if (
            isinstance(first_statement, ast.Expr)
            and isinstance(first_statement.value, ast.Constant)
            and isinstance(first_statement.value.value, str)
        ):
            docstring_nodes.add(id(first_statement.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


def _compare_body_lines(old_line: str, new_line: str):
    """Compare one changed body line inside a stable, high-similarity section."""

    shared_context = (
        "The implementation shall retain traceable requirements and review records. "
    ) * 12
    old = ExtractionResult(
        pdf_path=Path("old-generality-case.pdf"),
        pages=[PageText(page_number=1, text=f"1 Scope\n{shared_context}\n{old_line}")],
    )
    new = ExtractionResult(
        pdf_path=Path("new-generality-case.pdf"),
        pages=[PageText(page_number=1, text=f"1 Scope\n{shared_context}\n{new_line}")],
    )
    return compare_extractions(old, new, DiffOptions())


if __name__ == "__main__":
    unittest.main()
