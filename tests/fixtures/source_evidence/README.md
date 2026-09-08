# 原创来源证据回归样本

这些小型 PDF 和字体均为本任务或独立审查时程序生成的原创输入，不含 OIF 或其他第三方标准原文。

- `empty.pdf`、`visible.pdf`、`owned-glyph-evidence.ttf`：同源字体的实际空 CID 字形与可见私用字形；生成入口为 `tools/build_source_evidence_fixture.py`。
- `bottom_requirement-*`、`long-numeric-revision-*`、`footer-distribution-*`：独立审查发现的三个页脚漏报反例，分别保留技术末尾数值、数字版本、三页版本分布。
- `two-column-owners.pdf`：独立创作的 HN-73 双栏文档。首次留出验证发现标题归属缺陷；现已用于回归，不能再称未参与选择的盲测材料。

人工预期位于 `tests/test_stable_source_evidence.py`。已知失败的历史观察保留在任务外部验证目录，不能用最终通过覆盖首次失败事实。
