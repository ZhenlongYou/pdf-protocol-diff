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
    h = "<section><h2>" + title + "</h2><p>" + explanation + "</p>"
    m = "\n\n## " + title + "\n\n" + explanation + "\n"
    t = "\n\n" + title + "\n" + explanation + "\n"
    rows = []
    for i, r in enumerate(records, 1):
        for side in ("old", "new"):
            q = r[side]
            label = f"{i} {side} PDF {q['page']}"
            raw = q["original_formula_text"]
            img = q["image_path"]
            proof = json.dumps(q, ensure_ascii=False)
            h += (
                "<h3>"
                + label
                + "</h3><pre>"
                + html.escape(raw)
                + '</pre><img src="'
                + html.escape(Path(img).name)
                + '" alt="公式所在完整原页"><details><summary>完整来源位置记录</summary><pre>'
                + html.escape(proof)
                + "</pre></details>"
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
    with (Path(directory) / "formula_source_reviews.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return h + "</section>", m, t
