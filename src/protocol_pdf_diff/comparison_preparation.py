"""准备必须实际落盘并回读的来源证据，再交给内容层作最终判断。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedComparisonEvidence:
    physical_html: str
    physical_payload: object
    physical_receipts: frozenset
    formula_accepted: object


def prepare_comparison_evidence(result, tables, report_dir):
    table_groups = tables.table_groups
    from .physical_table_rows import render_physical_appendix, write_physical_csv
    # Physical-row records use the same table-level source images as the main
    # table/uncertainty evidence.  Suppress another embedded copy there; the
    # row text and its audit receipt remain separate.
    displayed_table_source_keys = {
        (side, table.page_number, table.table_number)
        for group in table_groups
        for side, tables in (("old", group.old_tables), ("new", group.new_tables))
        for table in tables
    }
    physical_html, physical_payload, physical_receipts = render_physical_appendix(
        table_groups,
        displayed_source_keys=displayed_table_source_keys,
    )
    physical_csv_path = report_dir / 'physical_table_records.csv'
    physical_receipts &= write_physical_csv(physical_payload, physical_csv_path)
    from .formula_source_review import prepare_formula_source_reviews
    formula_accepted = prepare_formula_source_reviews(result.formula_source_reviews, report_dir, result.formula_source_context)

    return PreparedComparisonEvidence(physical_html, physical_payload,
                                      frozenset(physical_receipts), formula_accepted)
