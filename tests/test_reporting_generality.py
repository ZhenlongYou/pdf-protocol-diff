"""Document-independent semantic visibility checks for rendered reports."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from protocol_pdf_diff import (
    reporting as reporting_module,  # 直接验证报告层逻辑表配对，不经 JSON 渲染掩盖组结构。
)
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import (
    DiffOptions,
    ExtractionResult,
    PageText,
    Section,
    TableVisual,
)
from protocol_pdf_diff.reporting import _inline_diff_html, write_reports


class ReportingGeneralityTests(unittest.TestCase):
    """Visible technical notation must survive report-layer normalization."""

    def _table_changes(self, old_value: str, new_value: str) -> list[dict[str, object]]:
        return self._table_changes_for_rows(
            [f"表格行: T1 | Parameter=Expression | Value={old_value}"],
            [f"表格行: T1 | Parameter=Expression | Value={new_value}"],
        )

    def _table_changes_for_rows(
        self,
        old_rows: list[str],
        new_rows: list[str],
        *,
        title: str = "Table 1 Generic expression",
        new_title: str | None = None,
    ) -> list[dict[str, object]]:
        body = "Each reported value shall remain traceable to its condition and unit. " * 9

        def extraction(name: str, rows: list[str], table_title: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[
                    TableVisual(
                        page_number=1,
                        table_number=1,
                        title=table_title,
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri="",
                        row_texts=rows,
                        grid_summary="structured rows",
                    )
                ],
            )

        options = DiffOptions()
        result = compare_extractions(
            extraction("old.pdf", old_rows, title),
            extraction("new.pdf", new_rows, new_title or title),
            options,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
        return payload["table_changes"]

    def test_inline_highlight_distinguishes_arrow_direction(self) -> None:
        old_html, new_html = _inline_diff_html(
            "Route A -> B.",
            "Route A <- B.",
        )

        self.assertIn('<mark class="del">-&gt;</mark>', old_html)
        self.assertIn('<mark class="ins">&lt;-</mark>', new_html)

    def test_standalone_domain_acronym_change_remains_visible_in_all_reader_reports(self) -> None:
        """A shared reader must not fold a changed term merely because SerDes uses it."""

        shared = "The interface description remains stable for every implementation. " * 10
        options = DiffOptions(visual_watchdog=False)
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-generic-standard.pdf"),
                pages=[PageText(1, f"1 Interface terminology\n{shared}\nMCB")],
            ),
            ExtractionResult(
                pdf_path=Path("new-generic-standard.pdf"),
                pages=[PageText(1, f"1 Interface terminology\n{shared}\nHCB")],
            ),
            options,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            reader_surfaces = {
                surface: outputs[surface].read_text(encoding="utf-8")
                for surface in ("html", "markdown", "text")
            }

        for surface, report_text in reader_surfaces.items():
            with self.subTest(surface=surface):
                self.assertIn("MCB", report_text)
                self.assertIn("HCB", report_text)
                self.assertNotIn("图示中的短标签已合并折叠", report_text)

    def test_inline_highlight_does_not_treat_negative_inequality_as_arrow(self) -> None:
        for old_text in ("Require x < -5.", "Require x<-5."):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, "Require x←5.")
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_keeps_operators_between_unicode_variables(self) -> None:
        old_html, new_html = _inline_diff_html(
            "Use α + β.",
            "Use α - β.",
        )

        self.assertIn('<mark class="del">+</mark>', old_html)
        self.assertIn('<mark class="ins">-</mark>', new_html)

    def test_inline_highlight_preserves_ordinary_multiplication(self) -> None:
        for old_text, new_text in (
            ("Use 2x100.", "Use 2."),
            ("Use 4*100 V.", "Use 4 V."),
            ("Use 3x101.", "Use 30."),
            ("Use 2xT.", "Use 2 T."),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                if new_text.endswith("30."):
                    self.assertIn("<mark", new_html)

        for old_text, new_text in (
            ("Use 2x100.", "Use 2×100."),
            ("Use 4*100.", "Use 4×100."),
            ("Use 5x10-6.", "Use 5×10-6."),
        ):
            with self.subTest(old_text=old_text, equivalent=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertNotIn("<mark", old_html)
                self.assertNotIn("<mark", new_html)

        for malformed in (
            "2**10-6",
            "2xx10-6",
            "2××10-6",
            "2×x10-6",
            "2*x10-6",
        ):
            with self.subTest(malformed=malformed):
                old_html, new_html = _inline_diff_html(
                    f"Use {malformed}.",
                    "Use 0.000002.",
                )
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_protects_hexadecimal_literals(self) -> None:
        for old_text, new_text in (
            ("Address 0x10.", "Address 0*10."),
            ("Mask 0xFF.", "Mask 0*FF."),
            ("Register 0XCAFE.", "Register 0×CAFE."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, _new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)

    def test_inline_highlight_preserves_formula_operators(self) -> None:
        for old_text, new_text in (
            ("Value=5+3.", "Value=5 3."),
            (
                "Formula valid = ready & enabled.",
                "Formula valid = ready and enabled.",
            ),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, _new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)

    def test_inline_highlight_preserves_explicit_positive_sign(self) -> None:
        for old_text, new_text in (
            ("Voltage +5 V.", "Voltage 5 V."),
            ("Encoded value +0.", "Encoded value 0."),
            ("Range -5 to +5.", "Range -5 to 5."),
            ("Result +1e3.", "Result 1000."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_assignment_and_call_case(self) -> None:
        for old_text, new_text in (
            ("Formula foo=1.", "Formula Foo=1."),
            ("Return parse(value).", "Return Parse(value)."),
            ("Set mode=fast.", "Set mode=Fast."),
            ("Call foo().", "Call Foo()."),
            ("Call f(foo, Active).", "Call f(foo, active)."),
            ("Formula y = offset + Active.", "Formula y = offset + active."),
            ("Use list [foo, Active].", "Use list [foo, active]."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

        old_html, new_html = _inline_diff_html(
            "Use the receiver.",
            "use the receiver.",
        )
        self.assertNotIn("<mark", old_html)
        self.assertNotIn("<mark", new_html)

    def test_inline_highlight_preserves_spaced_separators(self) -> None:
        for old_text, new_text in (
            ("Use 1, 2.", "Use 1 2."),
            ("Select RX, TX.", "Select RX TX."),
            ("reset; continue", "reset continue"),
            ("Evaluate f(a, b).", "Evaluate f(a b)."),
            ("Range [min, max].", "Range [min max]."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, _new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)

    def test_inline_highlight_preserves_punctuation_runs(self) -> None:
        for old_text, new_text in (
            ("Address fe80::1.", "Address fe80 1."),
            ("Use A::B.", "Use A B."),
            ("Read obj..member.", "Read obj member."),
            ("Pass args...", "Pass args"),
            ("Use A;;B.", "Use A B."),
            ("Use A,,B.", "Use A B."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, _new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)

    def test_inline_number_words_fold_only_in_positive_count_context(self) -> None:
        for old_text, new_text in (
            ("Capture seven waveforms.", "Capture 7 waveforms."),
            ("Retry twenty one idle intervals.", "Retry 21 idle intervals."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertNotIn("<mark", old_html)
                self.assertNotIn("<mark", new_html)

        for old_text, new_text in (
            ("Set y = one + offset.", "Set y = 1 + offset."),
            ("Return f(one).", "Return f(1)."),
            ("Select option one.", "Select option 1."),
            ("Set enum one.", "Set enum 1."),
            ("Read register one.", "Read register 1."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_complete_dotted_identifiers(self) -> None:
        for old_text, new_text in (
            ("Version v1.00.", "Version v1.0."),
            ("Version 1.0.0.", "Version 1.0.00."),
            ("Build r1.00.", "Build r1.0."),
            ("Filename image1.00.bin.", "Filename image1.0.bin."),
            ("Load image1.00.bin.", "Load image1.0.bin."),
            ("Call api.v1.00.endpoint.", "Call api.v1.0.endpoint."),
            ("Use profile_v1.00.", "Use profile_v1.0."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_literal_and_enum_case(self) -> None:
        for old_text, new_text in (
            ('Mode="Auto".', 'Mode="auto".'),
            ("Mode='Active'.", "Mode='active'."),
            ("Set enum Active.", "Set enum active."),
            ("Set mode Active.", "Set mode active."),
            ("State Idle.", "State idle."),
            ("Value=True.", "Value=true."),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_paths_backticks_and_cli_options(self) -> None:
        for old_text, new_text in (
            ("Load /Config now.", "Load /config now."),
            (r"Load C:\Config\Settings now.", r"Load C:\config\settings now."),
            (r"Open \\Server\Share.", r"Open \\server\share."),
            (r"Read HKLM\Software\Vendor.", r"Read HKLM\software\vendor."),
            ("Code `Active`.", "Code `active`."),
            ("Run --force.", "Run force."),
            ("Run -abc.", "Run abc."),
            ("Use <Mode>.", "Use <mode>."),
            ("Read $Config.", "Read $config."),
            ("Read @Config.", "Read @config."),
            ("Read #Config.", "Read #config."),
            ("Match /Active/.", "Match /active/."),
            ("Class: Foo", "Class: foo"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                if "--force" not in old_text:
                    self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_identifier_token_boundaries(self) -> None:
        for old_text, new_text in (
            ("Code 1A.", "Code 1 A."),
            ("Model 1A.", "Model 1 A."),
            ("Version 1A.", "Version 1 A."),
            ("Opcode 1A.", "Opcode 1 A."),
            ("Encoding 1A.", "Encoding 1 A."),
            ("Command 1A.", "Command 1 A."),
            ("Register 1A.", "Register 1 A."),
            ("Address 1A.", "Address 1 A."),
            ("Name 1A.", "Name 1 A."),
            ("Filename 1A.", "Filename 1 A."),
            ("Symbol μcode.", "Symbol ucode."),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

        old_html, new_html = _inline_diff_html("Voltage 5V.", "Voltage 5 V.")
        self.assertNotIn("<mark", old_html)
        self.assertNotIn("<mark", new_html)

        old_html, new_html = _inline_diff_html("Delay 5 μs.", "Delay 5 us.")
        self.assertNotIn("<mark", old_html)
        self.assertNotIn("<mark", new_html)

        for flattened in ("5x10-6", "5*10-6", "5×10-6"):
            with self.subTest(flattened=flattened):
                old_html, new_html = _inline_diff_html(
                    f"Use {flattened}.",
                    "Use 0.000005.",
                )
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_negative_zero(self) -> None:
        for old_text, new_text in (
            ("Encoded value -0.", "Encoded value 0."),
            ("Signed zero -0.", "Signed zero +0."),
            ("Result -0e3.", "Result 0."),
            ("Voltage -0 V.", "Voltage 0 V."),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_distinguishes_bidirectional_arrow_kinds(self) -> None:
        old_html, new_html = _inline_diff_html(
            "Map A <-> B.",
            "Map A <=> B.",
        )

        self.assertIn('<mark class="del">&lt;-&gt;</mark>', old_html)
        self.assertIn('<mark class="ins">&lt;=&gt;</mark>', new_html)

    def test_inline_highlight_accepts_equivalent_arrow_glyphs(self) -> None:
        old_html, new_html = _inline_diff_html(
            "Map A -> B and B <-> C.",
            "Map A → B and B ↔ C.",
        )

        self.assertNotIn("<mark", old_html)
        self.assertNotIn("<mark", new_html)

    def test_inline_highlight_splits_chinese_and_latin_word_runs(self) -> None:
        old_html, new_html = _inline_diff_html(
            "设置七sampleRate。",
            "设置八sampleRate。",
        )

        self.assertIn('<mark class="del">七</mark>sampleRate', old_html)
        self.assertIn('<mark class="ins">八</mark>sampleRate', new_html)
        self.assertNotIn('<mark class="del">设置', old_html)
        self.assertNotIn('<mark class="ins">设置', new_html)

    def test_inline_highlight_canonicalizes_chinese_count_beside_latin_text(self) -> None:
        old_html, new_html = _inline_diff_html(
            "捕获七个sampleRate。",
            "捕获 7 个sampleRate。",
        )

        self.assertNotIn("<mark", old_html)
        self.assertNotIn("<mark", new_html)

    def test_inline_highlight_keeps_generic_technical_symbols(self) -> None:
        cases = (
            ("Use f(x).", "Use fx.", "(", None),
            ("Use [0, 1].", "Use (0, 1).", "[", "("),
            ("Require x ∈ S.", "Require x ∉ S.", "∈", "∉"),
            ("Require A ∧ B.", "Require A ∨ B.", "∧", "∨"),
            ("Use α.", "Use β.", "α", "β"),
            ("Delay is Δt.", "Delay is δt.", "Δt", "δt"),
            ("Load is 50 Ω.", "Load is 50.", "Ω", None),
            ("Tolerance is 10%.", "Tolerance is 10.", "%", None),
            ("Temperature is 25 °C.", "Temperature is 25 C.", "°", None),
            ("Length is 5′.", "Length is 5.", "′", None),
        )

        for old_text, new_text, old_symbol, new_symbol in cases:
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn(f'<mark class="del">{old_symbol}</mark>', old_html)
                if new_symbol is not None:
                    self.assertIn(f'<mark class="ins">{new_symbol}</mark>', new_html)

    def test_inline_highlight_preserves_single_variable_case_only(self) -> None:
        variable_old_html, variable_new_html = _inline_diff_html(
            "Apply 5 V to node X and variable Y.",
            "Apply 5 v to node x and variable y.",
        )
        prose_old_html, prose_new_html = _inline_diff_html(
            "A module states I remain ready. Scope remains unchanged.",
            "a module states i remain ready. scope remains unchanged.",
        )

        self.assertIn('<mark class="del">V</mark>', variable_old_html)
        self.assertIn('<mark class="ins">v</mark>', variable_new_html)
        self.assertIn('<mark class="del">X</mark>', variable_old_html)
        self.assertIn('<mark class="ins">x</mark>', variable_new_html)
        self.assertIn('<mark class="del">Y</mark>', variable_old_html)
        self.assertIn('<mark class="ins">y</mark>', variable_new_html)
        self.assertNotIn("<mark", prose_old_html)
        self.assertNotIn("<mark", prose_new_html)

    def test_inline_highlight_keeps_relation_operator_changes(self) -> None:
        old_html, new_html = _inline_diff_html(
            "Require x != y.",
            "Require x = y.",
        )

        self.assertIn('<mark class="del">!=</mark>', old_html)
        self.assertIn('<mark class="ins">=</mark>', new_html)

    def test_inline_highlight_preserves_unit_and_endpoint_case(self) -> None:
        for old_text, new_text, old_token, new_token in (
            ("Rate is 5 Mb/s.", "Rate is 5 mb/s.", "Mb/s", "mb/s"),
            ("Frequency is 5 Hz.", "Frequency is 5 hz.", "Hz", "hz"),
            ("Pressure is 5 Pa.", "Pressure is 5 pa.", "Pa", "pa"),
            ("Capacity is 5 Ah.", "Capacity is 5 ah.", "Ah", "ah"),
            ("Enable Rx path.", "Enable rx path.", "Rx", "rx"),
            ("Enable Tx path.", "Enable tx path.", "Tx", "tx"),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn(f'<mark class="del">{old_token}</mark>', old_html)
                self.assertIn(f'<mark class="ins">{new_token}</mark>', new_html)

    def test_inline_highlight_preserves_case_and_modifier_positions(self) -> None:
        for old_text, new_text in (
            ("Route RX to rx.", "Route rx to RX."),
            ("Route X to x.", "Route x to X."),
            ("Drive V to v.", "Drive v to V."),
            ('Size 5\' by 6".', 'Size 5" by 6\'.'),
            ('Use x\' + y".', 'Use x" + y\'.'),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_contextual_punctuation(self) -> None:
        for old_text, new_text, punctuation in (
            ("Use DATA[7:0].", "Use DATA[7 0].", ":"),
            ("Use A.B.C.", "Use A.B C.", "."),
            ("Use ratio 1:2:3.", "Use ratio 1:2 3.", ":"),
            ("Use ratio 1 : 2.", "Use ratio 1 2.", ":"),
            ("Send MEAS?.", "Send MEAS.", "?"),
            ('Select "ON".', "Select ON.", "&quot;"),
            ("Command Meas?.", "Command Meas.", "?"),
            ("Command read?.", "Command read.", "?"),
            ("Query value?.", "Query value.", "?"),
            ("Use A,B.", "Use A B.", ","),
            ("Use 1,2.", "Use 1 2.", ","),
            ("Use INIT;RUN.", "Use INIT RUN.", ";"),
            ("Use A;B.", "Use A B.", ";"),
        ):
            with self.subTest(old_text=old_text):
                old_html, _new_html = _inline_diff_html(old_text, new_text)
                self.assertIn(
                    f'<mark class="del">{punctuation}</mark>',
                    old_html,
                )

    def test_inline_highlight_preserves_punctuation_occurrence_positions(self) -> None:
        for old_text, new_text in (
            ("Poll STAT? then STAT.", "Poll STAT then STAT?."),
            ("Use A.B then A B.", "Use A B then A.B."),
            ('Literal "ON" then ON.', 'Literal ON then "ON".'),
            ("Use (x,y) then (x y).", "Use (x y) then (x,y)."),
            ('Values 5" 5.', 'Values 5 5".'),
            ("Values 5' 5.", "Values 5 5'."),
            ('Lengths 5"; 5.', 'Lengths 5; 5".'),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_paired_literal_quotes(self) -> None:
        for old_text, new_text in (
            ("Literal ‘ON’.", "Literal ON."),
            ("Literal “MODE FAST”.", "Literal MODE FAST."),
            ('Literal "MODE FAST".', "Literal MODE FAST."),
            ('Literal "ON/OFF".', "Literal ON/OFF."),
        ):
            with self.subTest(old_text=old_text):
                old_html, _new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)

        for old_text, new_text in (
            ('Value "one mode".', 'Value "1 mode".'),
            ('Literal "seven lanes".', 'Literal "7 lanes".'),
            ("Code 'one state'.", "Code '1 state'."),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_lexical_number_positions(self) -> None:
        for old_text, new_text in (
            ("Model 01 is active.", "Model 1 is active."),
            ("Version 1.0 is active.", "Version 1 is active."),
            ("Codes 01 then 1.", "Codes 1 then 01."),
            ("Use 01/1.", "Use 1/01."),
            ("Models 001 and 1.", "Models 1 and 001."),
            ("Use re-sign.", "Use resign."),
            ("Version 1.00 is active.", "Version 1.0 is active."),
            ("Revision 1.20 is active.", "Revision 1.2 is active."),
            ("Model 1e3 is active.", "Model 1000 is active."),
            ("Code +5 is active.", "Code 5 is active."),
            ("Version: 1.00 is active.", "Version: 1.0 is active."),
            ("Version=1.00 is active.", "Version=1.0 is active."),
            ("Model: 1e3 is active.", "Model: 1000 is active."),
            ("Code=+5 is active.", "Code=5 is active."),
            ("Revision: 1.20 is active.", "Revision: 1.2 is active."),
            ("Load one.bin.", "Load 1.bin."),
            ("Load one.yaml.", "Load 1.yaml."),
            ("Code one is active.", "Code 1 is active."),
            ("ID one is active.", "ID 1 is active."),
            ("Version one is active.", "Version 1 is active."),
            ("Version: one is active.", "Version: 1 is active."),
            ("Code=one is active.", "Code=1 is active."),
            ("ID: one is active.", "ID: 1 is active."),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_preserves_math_script_and_style_glyphs(self) -> None:
        for old_text, new_text in (
            ("Use x².", "Use x2."),
            ("Use R₁.", "Use R1."),
            ("Use x ∈ ℝ.", "Use x ∈ R."),
            ("Use x ∈ ℂ.", "Use x ∈ C."),
            ("Matrix 𝐀 is active.", "Matrix A is active."),
            ("Vector 𝐯 is active.", "Vector v is active."),
        ):
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn("<mark", old_html)
                self.assertIn("<mark", new_html)

    def test_inline_highlight_keeps_ascii_measurement_modifiers(self) -> None:
        cases = (
            ("Use x'.", "Use x.", "&#x27;"),
            ("Length is 5'.", "Length is 5.", "&#x27;"),
            ('Length is 5".', "Length is 5.", "&quot;"),
        )

        for old_text, new_text, escaped_modifier in cases:
            with self.subTest(old_text=old_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertIn(
                    f'<mark class="del">{escaped_modifier}</mark>',
                    old_html,
                )
                self.assertNotIn("<mark", new_html)

    def test_inline_highlight_normalizes_only_unambiguous_list_markers(self) -> None:
        for old_text, new_text in (
            ("• Enable mode", "● Enable mode"),
            ("• Enable mode", "Enable mode"),
            ("1. Enable mode", "1) Enable mode"),
        ):
            with self.subTest(old_text=old_text, new_text=new_text):
                old_html, new_html = _inline_diff_html(old_text, new_text)
                self.assertNotIn("<mark", old_html)
                self.assertNotIn("<mark", new_html)

        for marker in ("-", "*"):
            with self.subTest(marker=marker):
                old_html, new_html = _inline_diff_html(
                    f"{marker} Enable mode",
                    "Enable mode",
                )
                self.assertIn(f'<mark class="del">{marker}</mark>', old_html)
                self.assertNotIn("<mark", new_html)

    def test_table_report_keeps_generic_technical_notation_changes(self) -> None:
        cases = (
            ("A -> B", "A <- B"),
            ("A <-> B", "A <=> B"),
            ("f(x)", "fx"),
            ("[0, 1]", "(0, 1)"),
            ("x != y", "x = y"),
            ("x ∈ S", "x ∉ S"),
            ("A ∧ B", "A ∨ B"),
            ("α", "β"),
            ("Δt", "δt"),
            ("50 Ω", "50"),
            ("10%", "10"),
            ("25 °C", "25 C"),
            ("5′", "5"),
            ("V", "v"),
        )

        for old_value, new_value in cases:
            with self.subTest(old_value=old_value, new_value=new_value):
                changes = self._table_changes(old_value, new_value)
                self.assertEqual(1, len(changes))
                self.assertEqual(1, changes[0]["row_change_count"])

    def test_table_report_ignores_ordinary_initial_capitalization(self) -> None:
        self.assertEqual([], self._table_changes("Enabled by default", "enabled by default"))
        self.assertEqual(
            [],
            self._table_changes(
                "A module states I remain ready",
                "a module states i remain ready",
            ),
        )

    def test_table_report_handles_mixed_chinese_count_without_case_noise(self) -> None:
        self.assertEqual(
            [],
            self._table_changes(
                "捕获七个sampleRate",
                "捕获 7 个sampleRate",
            ),
        )

    def test_table_report_normalizes_only_unambiguous_list_markers(self) -> None:
        for old_value, new_value in (
            ("• Enable mode", "● Enable mode"),
            ("• Enable mode", "Enable mode"),
            ("1. Enable mode", "1) Enable mode"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual([], self._table_changes(old_value, new_value))
        for marker in ("-", "*"):
            with self.subTest(marker=marker):
                self.assertEqual(
                    1,
                    len(self._table_changes(f"{marker} Enable mode", "Enable mode")),
                )

    def test_table_report_accepts_equivalent_symbol_glyphs(self) -> None:
        equivalent_pairs = (
            ("A -> B", "A → B"),
            ("A <-> B", "A ↔ B"),
            ("x != y", "x ≠ y"),
            ("x <= y", "x ≤ y"),
        )

        for old_value, new_value in equivalent_pairs:
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual([], self._table_changes(old_value, new_value))

    def test_table_report_preserves_checkbox_state(self) -> None:
        changes = self._table_changes("☐", "■")

        self.assertEqual(1, len(changes))
        self.assertEqual(1, changes[0]["row_change_count"])

    def test_table_report_preserves_word_internal_hyphens(self) -> None:
        for old_value, new_value in (
            ("re-sign", "resign"),
            ("read-only", "readonly"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_multiplication_as_a_fact(self) -> None:
        equivalent_pairs = (
            ("2xT", "2×T"),
            ("2*T", "2×T"),
            ("fb*n", "fb×n"),
            ("A*B", "A×B"),
            ("5x10-6", "5×10-6"),
        )
        for old_value, new_value in equivalent_pairs:
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual([], self._table_changes(old_value, new_value))

        for old_value, new_value in (
            ("2xT", "2 T"),
            ("fb*n", "fb n"),
            ("A*B", "A B"),
            ("2x100", "2"),
            ("4*100 V", "4 V"),
            ("3x101", "30"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))
        for malformed in (
            "2**10-6",
            "2xx10-6",
            "2××10-6",
            "2×x10-6",
            "2*x10-6",
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    1,
                    len(self._table_changes(malformed, "0.000002")),
                )
        for flattened in ("5x10-6", "5*10-6", "5×10-6"):
            with self.subTest(flattened=flattened):
                self.assertEqual(
                    1,
                    len(self._table_changes(flattened, "0.000005")),
                )

    def test_table_report_protects_hexadecimal_literals(self) -> None:
        for old_value, new_value in (
            ("Address 0x10", "Address 0*10"),
            ("Mask 0xFF", "Mask 0*FF"),
            ("Register 0XCAFE", "Register 0×CAFE"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_formula_operators(self) -> None:
        for old_value, new_value in (
            ("Value=5+3", "Value=5 3"),
            (
                "Formula valid = ready & enabled",
                "Formula valid = ready and enabled",
            ),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_explicit_positive_sign(self) -> None:
        for old_value, new_value in (
            ("Voltage +5 V", "Voltage 5 V"),
            ("Encoded value +0", "Encoded value 0"),
            ("Range -5 to +5", "Range -5 to 5"),
            ("Result +1e3", "Result 1000"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_assignment_and_call_case(self) -> None:
        for old_value, new_value in (
            ("Formula foo=1", "Formula Foo=1"),
            ("Return parse(value)", "Return Parse(value)"),
            ("Set mode=fast", "Set mode=Fast"),
            ("Call foo()", "Call Foo()"),
            ("Call f(foo, Active)", "Call f(foo, active)"),
            ("Formula y = offset + Active", "Formula y = offset + active"),
            ("Use list [foo, Active]", "Use list [foo, active]"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_spaced_separators(self) -> None:
        for old_value, new_value in (
            ("1, 2", "1 2"),
            ("RX, TX", "RX TX"),
            ("reset; continue", "reset continue"),
            ("f(a, b)", "f(a b)"),
            ("[min, max]", "[min max]"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_punctuation_runs(self) -> None:
        for old_value, new_value in (
            ("Address fe80::1", "Address fe80 1"),
            ("A::B", "A B"),
            ("obj..member", "obj member"),
            ("args...", "args"),
            ("A;;B", "A B"),
            ("A,,B", "A B"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_number_words_fold_only_in_positive_count_context(self) -> None:
        for old_value, new_value in (
            ("Capture seven waveforms", "Capture 7 waveforms"),
            ("Retry twenty one idle intervals", "Retry 21 idle intervals"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual([], self._table_changes(old_value, new_value))

        for old_value, new_value in (
            ("Set y = one + offset", "Set y = 1 + offset"),
            ("Return f(one)", "Return f(1)"),
            ("Select option one", "Select option 1"),
            ("Set enum one", "Set enum 1"),
            ("Read register one", "Read register 1"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_complete_dotted_identifiers(self) -> None:
        for old_value, new_value in (
            ("Version v1.00", "Version v1.0"),
            ("Version 1.0.0", "Version 1.0.00"),
            ("Build r1.00", "Build r1.0"),
            ("Filename image1.00.bin", "Filename image1.0.bin"),
            ("Load image1.00.bin", "Load image1.0.bin"),
            ("Call api.v1.00.endpoint", "Call api.v1.0.endpoint"),
            ("Use profile_v1.00", "Use profile_v1.0"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_literal_and_enum_case(self) -> None:
        for old_value, new_value in (
            ('Mode="Auto"', 'Mode="auto"'),
            ("Mode='Active'", "Mode='active'"),
            ("Set enum Active", "Set enum active"),
            ("Set mode Active", "Set mode active"),
            ("State Idle", "State idle"),
            ("Value=True", "Value=true"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_single_token_case_for_any_field(self) -> None:
        for field_label in (
            "Value",
            "Register",
            "Command",
            "Opcode",
            "Encoding",
            "Function",
            "Type",
            "Profile",
            "Model",
            "Name",
            "Status",
        ):
            with self.subTest(field_label=field_label):
                old_row = f"表格行: T1 | Parameter=Setting | {field_label}=Active"
                new_row = f"表格行: T1 | Parameter=Setting | {field_label}=active"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

        self.assertGreaterEqual(
            len(
                self._table_changes_for_rows(
                    ["表格行: T1 | Parameter=Active | Value=1"],
                    ["表格行: T1 | Parameter=active | Value=1"],
                )
            ),
            1,
        )

    def test_table_report_preserves_numeric_lexeme_for_any_field(self) -> None:
        for field_label, old_value, new_value in (
            ("Firmware", "1.00", "1.0"),
            ("Release", "1e3", "1000"),
            ("Protocol", "1.20", "1.2"),
            ("Schema", "1.20", "1.2"),
            ("Generation", "1.20", "1.2"),
            ("Arbitrary Header", "1,000", "1000"),
        ):
            with self.subTest(field_label=field_label):
                old_row = f"表格行: T1 | Parameter=Setting | {field_label}={old_value}"
                new_row = f"表格行: T1 | Parameter=Setting | {field_label}={new_value}"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

    def test_table_report_preserves_paths_backticks_cli_and_row_formula_context(self) -> None:
        cases = (
            (
                "表格行: T1 | Parameter=Setting | Path=/Config",
                "表格行: T1 | Parameter=Setting | Path=/config",
            ),
            (
                r"表格行: T1 | Parameter=Setting | Path=C:\\Config\\Settings",
                r"表格行: T1 | Parameter=Setting | Path=C:\\config\\settings",
            ),
            (
                r"表格行: T1 | Parameter=Setting | Path=\\\\Server\\Share",
                r"表格行: T1 | Parameter=Setting | Path=\\\\server\\share",
            ),
            (
                r"表格行: T1 | Parameter=Setting | Path=HKLM\\Software\\Vendor",
                r"表格行: T1 | Parameter=Setting | Path=HKLM\\software\\vendor",
            ),
            (
                "表格行: T1 | Parameter=Setting | Code=`Active`",
                "表格行: T1 | Parameter=Setting | Code=`active`",
            ),
            (
                "表格行: T1 | Parameter=Setting | Command=--force",
                "表格行: T1 | Parameter=Setting | Command=force",
            ),
            (
                "表格行: T1 | Parameter=Setting | Flags=-abc",
                "表格行: T1 | Parameter=Setting | Flags=abc",
            ),
            (
                "表格行: T1 | Parameter=Setting | Value=<Mode>",
                "表格行: T1 | Parameter=Setting | Value=<mode>",
            ),
            (
                "表格行: T1 | Parameter=Setting | Variable=$Config",
                "表格行: T1 | Parameter=Setting | Variable=$config",
            ),
            (
                "表格行: T1 | Parameter=Setting | Regex=/Active/",
                "表格行: T1 | Parameter=Setting | Regex=/active/",
            ),
            (
                "表格行: T1 | Parameter=Setting | Regex=a.*b",
                "表格行: T1 | Parameter=Setting | Regex=a.+b",
            ),
            (
                "表格行: T1 | Parameter=Setting | Value=Class: Foo",
                "表格行: T1 | Parameter=Setting | Value=Class: foo",
            ),
            (
                "表格行: T1 | Parameter=Mask | Value=A & B",
                "表格行: T1 | Parameter=Mask | Value=A and B",
            ),
            (
                "表格行: T1 | Mask=A & B",
                "表格行: T1 | Mask=A and B",
            ),
            (
                "表格行: T1 | Parameter=Output | Value=A & B",
                "表格行: T1 | Parameter=Output | Value=A and B",
            ),
            (
                "表格行: T1 | Parameter=Result | Value=A & B",
                "表格行: T1 | Parameter=Result | Value=A and B",
            ),
        )
        for old_row, new_row in cases:
            with self.subTest(old_row=old_row):
                self.assertGreaterEqual(
                    len(self._table_changes_for_rows([old_row], [new_row])),
                    1,
                )

    def test_table_report_preserves_structured_operator_syntax(self) -> None:
        for field_label, old_value, new_value in (
            ("Regex", "ab?", "ab"),
            ("Regex", "a*", "a"),
            ("Pattern", "a+", "a"),
            ("Command", "read?", "read"),
            ("Query", "value?", "value"),
            ("Expression", "x*", "x"),
            ("Expression", "*x", "x"),
            ("Expression", "x++", "x+"),
            ("Expression", "x--", "x-"),
        ):
            with self.subTest(field_label=field_label, old_value=old_value):
                old_row = f"表格行: T1 | Parameter=Setting | {field_label}={old_value}"
                new_row = f"表格行: T1 | Parameter=Setting | {field_label}={new_value}"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

    def test_table_report_preserves_structured_composite_identifier_case(self) -> None:
        for field_label, old_value, new_value in (
            ("Type", "Foo<Bar>", "foo<Bar>"),
            ("Namespace", "Foo::Bar", "foo::Bar"),
            ("Package", "Foo.Bar", "foo.Bar"),
            ("URI", "https://host/Path", "https://host/path"),
            ("URN", "urn:Vendor:Mode", "urn:vendor:Mode"),
            ("Email", "mailto:User@example.com", "mailto:user@example.com"),
            ("Package", "com.example.Foo", "com.example.foo"),
        ):
            with self.subTest(field_label=field_label, old_value=old_value):
                old_row = f"表格行: T1 | Parameter=Setting | {field_label}={old_value}"
                new_row = f"表格行: T1 | Parameter=Setting | {field_label}={new_value}"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

    def test_table_report_preserves_structured_boundary_punctuation(self) -> None:
        for field_label, old_value, new_value in (
            ("FQDN", "example.com.", "example.com"),
            ("Path", "foo/", "foo"),
            ("File", ".env", "env"),
        ):
            with self.subTest(field_label=field_label, old_value=old_value):
                old_row = f"表格行: T1 | Parameter=Setting | {field_label}={old_value}"
                new_row = f"表格行: T1 | Parameter=Setting | {field_label}={new_value}"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

    def test_table_caption_preserves_composite_identifier_case(self) -> None:
        row = "表格行: T1 | Parameter=Voltage | Max=1.1 | Units=V"
        for old_title, new_title in (
            ("Table 1 Package Foo.Bar", "Table 1 Package foo.Bar"),
            ("Table 1 Type Foo<Bar>", "Table 1 Type foo<Bar>"),
            ("Table 1 /Config", "Table 1 /config"),
        ):
            with self.subTest(old_title=old_title):
                changes = self._table_changes_for_rows(
                    [row],
                    [row],
                    title=old_title,
                    new_title=new_title,
                )
                self.assertEqual(1, len(changes))
                self.assertTrue(changes[0]["caption_changed"])

    def test_table_report_preserves_identifier_token_boundaries(self) -> None:
        for field_label in (
            "Code",
            "Model",
            "Version",
            "Opcode",
            "Encoding",
            "Command",
            "Register",
            "Address",
            "Name",
            "Filename",
            "File",
            "Path",
        ):
            with self.subTest(field_label=field_label):
                old_row = f"表格行: T1 | Parameter=Setting | {field_label}=1A"
                new_row = f"表格行: T1 | Parameter=Setting | {field_label}=1 A"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

        for field_label in ("Symbol",):
            with self.subTest(field_label=field_label):
                old_row = f"表格行: T1 | Parameter=Signal | {field_label}=1A"
                new_row = f"表格行: T1 | Parameter=Signal | {field_label}=1 A"
                self.assertEqual(
                    1,
                    len(self._table_changes_for_rows([old_row], [new_row])),
                )

        self.assertEqual(
            1,
            len(
                self._table_changes_for_rows(
                    ["表格行: T1 | Parameter=Setting | Model=2xT"],
                    ["表格行: T1 | Parameter=Setting | Model=2XT"],
                )
            ),
        )

        self.assertEqual(
            [],
            self._table_changes_for_rows(
                ["表格行: T1 | Parameter=Voltage | Value=5V"],
                ["表格行: T1 | Parameter=Voltage | Value=5 V"],
            ),
        )
        self.assertEqual(
            1,
            len(
                self._table_changes_for_rows(
                    ["表格行: T1 | Parameter=Signal | Symbol=μcode"],
                    ["表格行: T1 | Parameter=Signal | Symbol=ucode"],
                )
            ),
        )
        self.assertEqual(
            [],
            self._table_changes_for_rows(
                ["表格行: T1 | Parameter=Delay | Value=5 μs"],
                ["表格行: T1 | Parameter=Delay | Value=5 us"],
            ),
        )

    def test_table_report_preserves_compact_pipe_operators(self) -> None:
        for old_value, new_value in (
            ("A|B", "A||B"),
            ("5|3", "5||3"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_decodes_escaped_cells_and_keeps_column_identity(self) -> None:
        for old_row, new_row in (
            (
                r"表格行: T1 | Parameter=Expression | Value=A\|B",
                r"表格行: T1 | Parameter=Expression | Value=A\|\|B",
            ),
            (
                r"表格行: T1 | Parameter=Expression | Value=A\=B",
                r"表格行: T1 | Parameter=Expression | Value=A B",
            ),
            (
                "表格行: T1 | Parameter=Expression | Value=1 | Value=2",
                "表格行: T1 | Parameter=Expression | Value=2 | Value=1",
            ),
            (
                "表格行: T1 | Parameter=Expression | Column 2= | Column 3=B",
                "表格行: T1 | Parameter=Expression | Column 2=B | Column 3=",
            ),
        ):
            with self.subTest(old_row=old_row, new_row=new_row):
                changes = self._table_changes_for_rows([old_row], [new_row])
                self.assertEqual(1, len(changes))

        duplicate_changes = self._table_changes_for_rows(
            ["表格行: T1 | Parameter=p | Value=1 | Value=2"],
            ["表格行: T1 | Parameter=p | Value=3 | Value=2"],
        )
        row_change = duplicate_changes[0]["row_changes"][0]
        self.assertIn("Value[1]=1", row_change["old_value"])
        self.assertIn("Value[2]=2", row_change["old_value"])
        self.assertIn("Value[1]=3", row_change["new_value"])
        self.assertIn("Value[2]=2", row_change["new_value"])

    def test_table_report_preserves_encoded_multiline_cell_boundary(self) -> None:
        changes = self._table_changes(
            r"ready\nenabled",
            "ready / enabled",
        )

        self.assertEqual(1, len(changes))
        row_change = changes[0]["row_changes"][0]
        self.assertIn("↵", row_change["old_value"])
        self.assertIn("ready / enabled", row_change["new_value"])
        self.assertNotEqual(row_change["old_value"], row_change["new_value"])

        sentinel_collision = self._table_changes(
            r"ready\nenabled",
            "ready table-line-boundary enabled",
        )
        self.assertEqual(1, len(sentinel_collision))

    def test_table_report_displays_added_and_duplicate_empty_fields(self) -> None:
        added_empty = self._table_changes_for_rows(
            ["表格行: T1 | Parameter=Setting | Value=1"],
            ["表格行: T1 | Parameter=Setting | Value=1 | Notes="],
        )
        self.assertEqual(1, len(added_empty))
        added_row = added_empty[0]["row_changes"][0]
        self.assertIn("Notes=（空白）", added_row["new_value"])
        self.assertNotIn("<empty>", added_row["new_value"])

        duplicate_empty = self._table_changes_for_rows(
            ["表格行: T1 | Parameter=Setting | Value= | Value=1"],
            ["表格行: T1 | Parameter=Setting | Value=1 | Value="],
        )
        self.assertEqual(1, len(duplicate_empty))
        duplicate_row = duplicate_empty[0]["row_changes"][0]
        self.assertIn("Value[1]=（空白）", duplicate_row["old_value"])
        self.assertIn("Value[2]=（空白）", duplicate_row["new_value"])

    def test_table_report_preserves_semantic_and_unknown_row_order(self) -> None:
        allow = "表格行: T1 | Rule=allow | Match=trusted"
        deny = "表格行: T2 | Rule=deny | Match=*"
        for title in (
            "Table 1 First-match policy rules",
            "Table 1 Unclassified records",
        ):
            with self.subTest(title=title):
                changes = self._table_changes_for_rows(
                    [allow, deny],
                    [deny, allow],
                    title=title,
                )
                self.assertGreaterEqual(len(changes), 1)

        address = "表格行: T1 | Parameter=address matches | Value=allow"
        otherwise = "表格行: T2 | Parameter=otherwise | Value=deny"
        self.assertGreaterEqual(
            len(
                self._table_changes_for_rows(
                    [address, otherwise],
                    [otherwise, address],
                    title="Table 1 Mappings",
                )
            ),
            1,
        )

        voltage = "表格行: T1 | Parameter=Voltage | Value=1 | Units=V"
        current = "表格行: T2 | Parameter=Current | Value=2 | Units=A"
        self.assertEqual(
            [],
            self._table_changes_for_rows(
                [voltage, current],
                [current, voltage],
                title="Table 1 Operating limits",
            ),
        )

        allow = "表格行: T1 | Parameter=address matches | Action=allow"
        deny = "表格行: T2 | Parameter=otherwise | Action=deny"
        self.assertGreaterEqual(
            len(
                self._table_changes_for_rows(
                    [allow, deny],
                    [deny, allow],
                    title="Table 1 Policy rules",
                )
            ),
            1,
        )

    def test_table_report_preserves_negative_zero(self) -> None:
        for old_value, new_value in (
            ("Encoded value -0", "Encoded value 0"),
            ("Signed zero -0", "Signed zero +0"),
            ("Result -0e3", "Result 0"),
            ("Voltage -0 V", "Voltage 0 V"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_unit_and_endpoint_case(self) -> None:
        for old_value, new_value in (
            ("5 Mb/s", "5 mb/s"),
            ("5 Hz", "5 hz"),
            ("5 Pa", "5 pa"),
            ("5 Ah", "5 ah"),
            ("Rx", "rx"),
            ("Tx", "tx"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_case_and_modifier_positions(self) -> None:
        for old_value, new_value in (
            ("Route RX to rx", "Route rx to RX"),
            ("Route X to x", "Route x to X"),
            ("Drive V to v", "Drive v to V"),
            ('Size 5\' by 6"', 'Size 5" by 6\''),
            ('x\' + y"', 'x" + y\''),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_contextual_punctuation(self) -> None:
        for old_value, new_value in (
            ("DATA[7:0]", "DATA[7 0]"),
            ("A.B.C", "A.B C"),
            ("1:2:3", "1:2 3"),
            ("1 : 2", "1 2"),
            ("MEAS?", "MEAS"),
            ('"ON"', "ON"),
            ("Command Meas?", "Command Meas"),
            ("Command read?", "Command read"),
            ("Query value?", "Query value"),
            ("A,B", "A B"),
            ("1,2", "1 2"),
            ("INIT;RUN", "INIT RUN"),
            ("A;B", "A B"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_punctuation_occurrence_positions(self) -> None:
        for old_value, new_value in (
            ("Poll STAT? then STAT", "Poll STAT then STAT?"),
            ("Use A.B then A B", "Use A B then A.B"),
            ('Literal "ON" then ON', 'Literal ON then "ON"'),
            ("(x,y) then (x y)", "(x y) then (x,y)"),
            ('Values 5" 5', 'Values 5 5"'),
            ("Values 5' 5", "Values 5 5'"),
            ('Lengths 5"; 5', 'Lengths 5; 5"'),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_paired_literal_quotes(self) -> None:
        for old_value, new_value in (
            ("Literal ‘ON’", "Literal ON"),
            ("Literal “MODE FAST”", "Literal MODE FAST"),
            ('Literal "MODE FAST"', "Literal MODE FAST"),
            ('Literal "ON/OFF"', "Literal ON/OFF"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))
        for old_value, new_value in (
            ('Value "one mode"', 'Value "1 mode"'),
            ('Literal "seven lanes"', 'Literal "7 lanes"'),
            ("Code 'one state'", "Code '1 state'"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_lexical_number_positions(self) -> None:
        for old_value, new_value in (
            ("Model 01", "Model 1"),
            ("Version 1.0", "Version 1"),
            ("Codes 01 then 1", "Codes 1 then 01"),
            ("Use 01/1", "Use 1/01"),
            ("Models 001 and 1", "Models 1 and 001"),
            ("Version 1.00", "Version 1.0"),
            ("Revision 1.20", "Revision 1.2"),
            ("Model 1e3", "Model 1000"),
            ("Code +5", "Code 5"),
            ("Version: 1.00", "Version: 1.0"),
            ("Version=1.00", "Version=1.0"),
            ("Model: 1e3", "Model: 1000"),
            ("Code=+5", "Code=5"),
            ("Revision: 1.20", "Revision: 1.2"),
            ("Load one.bin", "Load 1.bin"),
            ("Load one.yaml", "Load 1.yaml"),
            ("Code one", "Code 1"),
            ("ID one", "ID 1"),
            ("Version one", "Version 1"),
            ("Version: one", "Version: 1"),
            ("Code=one", "Code=1"),
            ("ID: one", "ID: 1"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_table_report_preserves_math_script_and_style_glyphs(self) -> None:
        for old_value, new_value in (
            ("x²", "x2"),
            ("R₁", "R1"),
            ("x ∈ ℝ", "x ∈ R"),
            ("x ∈ ℂ", "x ∈ C"),
            ("Matrix 𝐀", "Matrix A"),
            ("Vector 𝐯", "Vector v"),
        ):
            with self.subTest(old_value=old_value):
                self.assertEqual(1, len(self._table_changes(old_value, new_value)))

    def test_same_caption_tables_in_different_sections_are_not_aggregated(self) -> None:
        body = "Each requirement shall retain its own local table context. " * 10

        def extraction(name: str, part_one_value: str, part_two_value: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(page_number=1, text=f"1 Part I\n{body}"),
                    PageText(page_number=2, text=f"2 Part II\n{body}"),
                ],
                total_pages=2,
                selected_start_page=1,
                selected_end_page=2,
                table_visuals=[
                    TableVisual(
                        page_number=1,
                        table_number=1,
                        title="Table 1 Shared limits",
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri="",
                        row_texts=[
                            f"表格行: T1 | Parameter=Threshold | Value={part_one_value}"
                        ],
                        grid_summary="structured rows",
                    ),
                    TableVisual(
                        page_number=2,
                        table_number=1,
                        title="Table 1 Shared limits",
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri="",
                        row_texts=[
                            f"表格行: T1 | Parameter=Threshold | Value={part_two_value}"
                        ],
                        grid_summary="structured rows",
                    ),
                ],
            )

        options = DiffOptions()
        result = compare_extractions(
            extraction("old.pdf", "10", "20"),
            extraction("new.pdf", "20", "10"),
            options,
        )
        self.assertEqual("reliable", result.assessment.state)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(payload["table_changes"]), 1)
        self.assertGreaterEqual(
            sum(change["row_change_count"] for change in payload["table_changes"]),
            2,
        )

    def test_unique_same_caption_with_disjoint_rows_does_not_cross_pair_sections(self) -> None:
        """A unique caption cannot bypass content evidence when section contexts disagree."""

        caption = "Table 32-4. Shared Operating Limits"  # 两侧故意保持完全相同且各只出现一次的编号表题。
        old_table = TableVisual(  # 旧表只包含发送端电压事实。
            page_number=1,
            table_number=1,
            title=caption,
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=TransmitterVoltage | Value=1.0 V"],
            grid_summary="structured rows",
        )
        new_table = TableVisual(  # 新表只包含接收端抖动事实，与旧表没有内容身份交集。
            page_number=2,
            table_number=1,
            title=caption,
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=ReceiverJitter | Value=0.05 UI"],
            grid_summary="structured rows",
        )
        old_section = Section(  # 旧表明确归属发送端章节。
            "S1", "1 Transmitter", "Transmitter", 1,
            ("1 Transmitter",), ("1",), 1, 1, "body",
        )
        new_section = Section(  # 新表明确归属不同的接收端章节。
            "S2", "2 Receiver", "Receiver", 1,
            ("2 Receiver",), ("2",), 2, 2, "body",
        )

        groups = reporting_module._paired_table_visuals(  # 走真实跨修订表格配对入口。
            [old_table],
            [new_table],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(2, len(groups))  # 内容无关时必须保留“旧表删除 + 新表新增”两个逻辑组。
        self.assertTrue(  # 每个组只能有一侧，防止相同 caption 把无关事实伪装成修改。
            all(bool(group.old_tables) != bool(group.new_tables) for group in groups)
        )

    def test_same_number_but_different_section_titles_still_require_table_content(self) -> None:
        """相同 number_path 不能抹掉章节语义变化并绕过唯一表题整组门槛。"""

        caption = "Table 1. Shared Operating Limits"  # 两侧表号/表题完全相同，专门隔离上下文缺陷。
        old_table = TableVisual(  # 旧表属于发送端章节且只有电压行。
            1, 1, caption, (0, 0, 100, 100), "",
            ["表格行: T1 | Parameter=TransmitterVoltage | Value=1.0 V"], "rows",
        )
        new_table = TableVisual(  # 新表属于接收端章节且只有不相交的抖动行。
            1, 1, caption, (0, 0, 100, 100), "",
            ["表格行: T1 | Parameter=ReceiverJitter | Value=0.05 UI"], "rows",
        )
        old_section = Section(
            "S1", "1 Transmitter", "Transmitter", 1, ("1 Transmitter",), ("1",), 1, 1, "body",
        )
        new_section = Section(
            "S2", "1 Receiver", "Receiver", 1, ("1 Receiver",), ("1",), 1, 1, "body",
        )

        groups = reporting_module._paired_table_visuals(
            [old_table],
            [new_table],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(2, len(groups))  # 内容相似度为零时必须保留旧表删除和新表新增。
        self.assertTrue(all(bool(group.old_tables) != bool(group.new_tables) for group in groups))

    def test_unnumbered_tables_do_not_fuzzy_pair_across_reordered_sections(self) -> None:
        body = "Each requirement shall retain its own local unnumbered table context. " * 9

        def extraction(
            name: str,
            section_order: tuple[tuple[str, str], tuple[str, str]],
        ) -> ExtractionResult:
            pages: list[PageText] = []
            tables: list[TableVisual] = []
            for page_number, ((number, title), value) in enumerate(
                zip(section_order, ("1", "2"), strict=True),
                start=1,
            ):
                pages.append(
                    PageText(
                        page_number=page_number,
                        text=f"{number} {title}\n{body}",
                    )
                )
                tables.append(
                    TableVisual(
                        page_number=page_number,
                        table_number=1,
                        title="Limits",
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri="",
                        row_texts=[
                            f"表格行: T1 | Parameter=Limit | Value={value}"
                        ],
                        grid_summary="structured rows",
                    )
                )
            return ExtractionResult(
                pdf_path=Path(name),
                pages=pages,
                total_pages=2,
                selected_start_page=1,
                selected_end_page=2,
                table_visuals=tables,
            )

        options = DiffOptions()
        result = compare_extractions(
            extraction("old.pdf", (("1", "Part I"), ("2", "Part II"))),
            extraction("new.pdf", (("2", "Part II"), ("1", "Part I"))),
            options,
        )
        self.assertEqual("reliable", result.assessment.state)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(payload["table_changes"]), 1)
        self.assertGreaterEqual(
            sum(change["row_change_count"] for change in payload["table_changes"]),
            2,
        )

    def test_same_page_unnumbered_tables_do_not_cross_pair_by_content(self) -> None:
        body = "Each local section retains the table found at its own ordinal position. " * 8

        def extraction(
            name: str,
            first_fact: tuple[str, str],
            second_fact: tuple[str, str],
        ) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 Alpha\n{body}\n2 Beta\n{body}",
                    )
                ],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[
                    TableVisual(
                        page_number=1,
                        table_number=1,
                        title="Operating limits",
                        bbox=(0.0, 100.0, 100.0, 180.0),
                        image_data_uri="",
                        row_texts=[
                            f"表格行: T1 | Parameter={first_fact[0]} | Value={first_fact[1]}"
                        ],
                        grid_summary="structured rows",
                    ),
                    TableVisual(
                        page_number=1,
                        table_number=2,
                        title="Operating limits",
                        bbox=(0.0, 500.0, 100.0, 580.0),
                        image_data_uri="",
                        row_texts=[
                            f"表格行: T2 | Parameter={second_fact[0]} | Value={second_fact[1]}"
                        ],
                        grid_summary="structured rows",
                    ),
                ],
            )

        options = DiffOptions()
        result = compare_extractions(
            extraction("old.pdf", ("Alpha", "1"), ("Beta", "2")),
            extraction("new.pdf", ("Beta", "2"), ("Alpha", "1")),
            options,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(payload["table_changes"]), 1)
        self.assertGreaterEqual(
            payload["assessment"]["old_document"]["ambiguous_table_context_page_count"],
            1,
        )

    def test_same_page_inserted_table_does_not_offset_existing_unique_tables(self) -> None:
        """A new leading table must not turn unchanged later tables into false changes."""

        body = "Each uniquely captioned table shall keep its logical identity after insertion. " * 8

        def table(number: int, title: str, parameter: str, value: str) -> TableVisual:
            return TableVisual(
                page_number=1,
                table_number=number,
                title=title,
                bbox=(0.0, float(number * 100), 100.0, float(number * 100 + 80)),
                image_data_uri="",
                row_texts=[
                    f"表格行: T1 | Parameter={parameter} | Value={value}"
                ],
                grid_summary="structured rows",
            )

        old_alpha = table(1, "Table 1. Alpha limits", "AlphaVoltage", "1 V")
        old_beta = table(2, "Table 2. Beta limits", "BetaCurrent", "2 A")
        inserted = table(1, "Table 0. Inserted limits", "InsertedPower", "3 W")
        new_alpha = table(2, "Table 1. Alpha limits", "AlphaVoltage", "1 V")
        new_beta = table(3, "Table 2. Beta limits", "BetaCurrent", "2 A")

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old.pdf"),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                table_visuals=[old_alpha, old_beta],
            ),
            ExtractionResult(
                pdf_path=Path("new.pdf"),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                table_visuals=[inserted, new_alpha, new_beta],
            ),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(1, len(payload["table_changes"]))
        self.assertEqual("added", payload["table_changes"][0]["change_type"])
        self.assertEqual(
            ["Table 0. Inserted limits"],
            payload["table_changes"][0]["new_titles"],
        )

    def test_same_page_local_reorder_does_not_corrupt_stable_prefix_matches(self) -> None:
        """A reordered suffix must not turn earlier monotonic table anchors into edits."""

        body = "Stable table anchors shall survive an insertion and a later local reorder. " * 8

        def table(position: int, title: str, parameter: str) -> TableVisual:
            return TableVisual(
                page_number=1,
                table_number=position,
                title=title,
                bbox=(0.0, float(position * 100), 100.0, float(position * 100 + 80)),
                image_data_uri="",
                row_texts=[f"表格行: T1 | Parameter={parameter} | Value=stable"],
                grid_summary="structured rows",
            )

        old_tables = [
            table(1, "Table 1. Alpha limits", "Alpha"),
            table(2, "Table 2. Beta limits", "Beta"),
            table(3, "Table 3. Gamma limits", "Gamma"),
            table(4, "Table 4. Delta limits", "Delta"),
        ]
        new_tables = [
            table(1, "Table 0. Inserted limits", "Inserted"),
            table(2, "Table 1. Alpha limits", "Alpha"),
            table(3, "Table 2. Beta limits", "Beta"),
            table(4, "Table 4. Delta limits", "Delta"),
            table(5, "Table 3. Gamma limits", "Gamma"),
        ]

        def extraction(name: str, tables: list[TableVisual]) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                table_visuals=tables,
            )

        result = compare_extractions(
            extraction("old.pdf", old_tables),
            extraction("new.pdf", new_tables),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        changed_titles = {
            title
            for change in payload["table_changes"]
            for title in (*change["old_titles"], *change["new_titles"])
        }
        self.assertNotIn("Table 1. Alpha limits", changed_titles)
        self.assertNotIn("Table 2. Beta limits", changed_titles)
        self.assertTrue(
            {
                "Table 3. Gamma limits",
                "Table 4. Delta limits",
            }
            & changed_titles
        )  # C/D 的交叉物理顺序必须至少留下一侧移动证据，不能被插入位移抵消。
        self.assertTrue(
            any(
                change["change_type"] == "added"
                and change["new_titles"] == ["Table 0. Inserted limits"]
                for change in payload["table_changes"]
            )
        )

    def test_same_page_numbered_tables_preserve_physical_order(self) -> None:
        body = "Numbered tables remain attached to their physical document position. " * 8

        def extraction(name: str, swapped: bool) -> ExtractionResult:
            alpha = TableVisual(
                page_number=1,
                table_number=1,
                title="Table 1 Alpha limits",
                bbox=(0.0, 500.0 if swapped else 100.0, 100.0, 580.0 if swapped else 180.0),
                image_data_uri="",
                row_texts=["表格行: T1 | Parameter=AlphaVoltage | Value=1 | Units=V"],
                grid_summary="structured rows",
            )
            beta = TableVisual(
                page_number=1,
                table_number=2,
                title="Table 2 Beta limits",
                bbox=(0.0, 100.0 if swapped else 500.0, 100.0, 180.0 if swapped else 580.0),
                image_data_uri="",
                row_texts=["表格行: T1 | Parameter=BetaCurrent | Value=2 | Units=A"],
                grid_summary="structured rows",
            )
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[alpha, beta],
            )

        options = DiffOptions()
        result = compare_extractions(
            extraction("old.pdf", False),
            extraction("new.pdf", True),
            options,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(payload["table_changes"]), 1)

    def test_invalid_table_bbox_does_not_hide_other_tables_physical_reorder(self) -> None:
        """One coordinate-free table cannot revoke valid bbox order for its siblings."""

        body = "Valid table coordinates remain authoritative beside one fallback summary. " * 8

        def extraction(name: str, swapped: bool) -> ExtractionResult:
            alpha = TableVisual(
                1,
                1,
                "Table 1 Alpha limits",
                (0.0, 500.0 if swapped else 100.0, 100.0, 580.0 if swapped else 180.0),
                "",
                ["表格行: T1 | Parameter=AlphaVoltage | Value=1 | Units=V"],
                "rows",
            )
            beta = TableVisual(
                1,
                2,
                "Table 2 Beta limits",
                (0.0, 100.0 if swapped else 500.0, 100.0, 180.0 if swapped else 580.0),
                "",
                ["表格行: T1 | Parameter=BetaCurrent | Value=2 | Units=A"],
                "rows",
            )
            coordinate_free = TableVisual(
                1,
                3,
                "Table 9 Coordinate-free summary",
                (0.0, 0.0, 0.0, 0.0),
                "",
                ["表格行: T1 | Parameter=Fallback | Value=stable"],
                "rows",
            )
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                table_visuals=[alpha, beta, coordinate_free],
            )

        result = compare_extractions(
            extraction("old.pdf", False),
            extraction("new.pdf", True),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(payload["table_changes"]), 1)


if __name__ == "__main__":
    unittest.main()
