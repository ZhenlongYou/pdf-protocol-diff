"""把已有覆盖事实转成可行动清单，不把未检查误写成没有变化。"""

import re


def coverage_review_items(result):
    items = []

    def add(old, new, scope, reason, action, bbox=None):
        row = dict(old_page=old, new_page=new, scope=scope, reason=reason, action=action)
        if bbox is not None:
            row["bbox"] = list(bbox)
        if row not in items:
            items.append(row)

    audit = result.provenance.visual_watchdog_audit if result.provenance else None
    if audit is not None:
        if not audit.enabled:
            add(None, None, "所选范围的视觉内容", "此次未开启像素核对。", "核对源页图示，或开启视觉核对后重试。")
        for issue in audit.coverage_issues:
            add(issue.old_page_number, issue.new_page_number, "页面图形", issue.reason, "按源页检查图示及其对应关系。")
        for old, new in audit.semantic_change_pages:
            add(old, new, "正文变化页中的图形", "正文已参与比较，整页像素未核对。", "检查源页图片；局部检查不能代表整页覆盖。")
    if result.assessment:
        for side, metrics in (("old", result.assessment.old_document), ("new", result.assessment.new_document)):
            for warning in metrics.extraction_warnings:
                if "OCR" not in warning:
                    continue
                page_match = re.search(r"第\s*(\d+)\s*页", warning)
                if page_match is None and "页码:" in warning:
                    continue  # 已有逐页失败项，不把汇总提示再算成无页码的 OCR 成功项。
                page = int(page_match[1]) if page_match else None
                failed = any(word in warning for word in ("失败", "未执行", "未识别", "未安装", "已用完"))
                add(page if side == "old" else None, page if side == "new" else None,
                    "未完成的文字识别" if failed else "识别文字需复核", warning,
                    "核对源页；按原因缩小页窗、调整语言或识别时限后重试。" if failed else "对照源页检查文字、数值和重复条目。")
    for side in ("old", "new"):
        for table in getattr(result, side + "_table_visuals", ()):
            if table.row_alignment_reliable and table.content_fully_represented:
                continue
            add(table.page_number if side == "old" else None, table.page_number if side == "new" else None,
                "表格行列", f"{table.title or '表格'}的行列边界或文字覆盖尚未验证。",
                "核对报告中的表格截图，确认每个值及其行列归属。", table.bbox)
        for formula in getattr(result, side + "_formula_visuals", ()):
            add(formula.page_number if side == "old" else None, formula.page_number if side == "new" else None,
                "公式", "公式自动比较未开启。", "核对源页公式及上下标。", formula.bbox)
    return items
