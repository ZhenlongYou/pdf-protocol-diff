"""Lossless visible unknown-source handoff, independently of formula equivalence."""

import csv
import html
import json
from pathlib import Path


def render_formula_source_reviews(records, directory):
    if not records:
        return "", "", ""
    title = f"公式来源待核实（{len(records)} 项）"
    explanation = "以下原公式文字的读取不可靠。完整来源保留供人工核对；不表示新增、删除或公式相同。"
    h = '<section class="formula-source-reviews"><h2>' + title + "</h2><p>" + explanation + "</p>"
    m = "\n\n## " + title + "\n\n" + explanation + "\n"
    t = "\n\n" + title + "\n" + explanation + "\n"
    rows = []
    for i, r in enumerate(records, 1):
        h += '<div class="figure-source-visual-grid">'
        for side in ("old", "new"):
            q = r[side]
            label = f"{i} {side} PDF {q['page']}"
            visual_label = f"公式 {i} · {'旧版' if side == 'old' else '新版'} · PDF 第 {q['page']} 页"
            raw = q["original_formula_text"]
            img = q["image_path"]
            proof = json.dumps(q, ensure_ascii=False)
            wrapped_pre = '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;overflow-x:auto">'
            h += (
                '<section class="prose-source-side"><figure class="prose-source-page formula-source-page">'
                '<figcaption>' + html.escape(visual_label)
                + ' · 完整原页，未标色 · 点击放大</figcaption>'
                + wrapped_pre + html.escape(raw) + '</pre>'
                + '<img src="' + html.escape(Path(img).name)
                + '" alt="' + html.escape(visual_label) + ' 完整原页">'
                + '</figure><details class="formula-source-proof">'
                + '<summary>完整来源位置记录</summary>'
                + wrapped_pre + html.escape(proof) + '</pre></details></section>'
            )
            fence = "`" * (
                max(
                    [len(z) for z in __import__("re").findall(r"`+", proof + raw)] + [2]
                )
                + 1
            )
            m += (
                "\n### "
                + label
                + "\n\n"
                + fence
                + "\n"
                + raw
                + "\n"
                + fence
                + "\n\n![公式所在完整原页]("
                + Path(img).name
                + ")\n\n"
                + fence
                + "json\n"
                + proof
                + "\n"
                + fence
                + "\n"
            )
            t += (
                "\n"
                + label
                + "\n"
                + raw
                + "\n原图: "
                + img
                + "\n完整来源: "
                + proof
                + "\n"
            )
            rows.append(
                {
                    "review": i,
                    "status": r["status"],
                    "side": side,
                    "page": q["page"],
                    "formula_text": raw,
                    "source_image": img,
                    "source_image_base": "report_directory",
                    "complete_source_record": proof,
                    "raw_transaction": json.dumps(
                        {k: r[k] for k in ["raw_added", "raw_removed", "raw_replaced"]},
                        ensure_ascii=False,
                    ),
                }
            )
        h += "</div>"
    with (Path(directory) / "formula_source_reviews.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return h + "</section>", m, t
