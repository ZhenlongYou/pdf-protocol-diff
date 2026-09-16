# 正文数值变化与相似度折叠

## 用户缺陷

旧报告把长正文中的 `26450 → 26560` 标成 `相似度 1.000`。实际章节配对分数约为 `0.999664`，只是三位小数显示发生了舍入；如果按这个显示值折叠，协议中的关键参数会从主差异清单消失。

## 根因与修复口径

章节配对分数用于判断两侧是否是同一逻辑章节，不能代表完整内容相等。报告现在同时计算完整 `content_similarity`，并保留 `pair_similarity`（旧字段 `similarity` 继续作为兼容别名）。正文进入折叠附录必须满足：

1. 两侧章节存在且 `pair_similarity` 精确为 `1.0`；
2. 完整正文经过保守空白归一后逐字相同，`content_similarity` 精确为 `1.0`；
3. 没有数字、技术标识、语义运算符或未展开的未知差异。

因此 `26450 → 26560` 会留在主差异区。HTML 显示两种分数，JSON/CSV 还提供 `critical_content_equal`，后续消费方不需要从四舍五入后的展示值推断是否相同。表格仍按原页证据留在主区。

## 验证证据

- 回归测试：`tests.test_screenshot_first` 14 项通过；正文/表格/坐标/页级证据及协议回归定向套件 755 项通过。
- 完整套件：`.venv/bin/python -m unittest discover -s tests -q`，1763 项通过，1 项条件跳过（274.944 秒）。
- 编译与格式：`.venv/bin/python -m compileall -q src tests docs/verification/numeric_similarity_probe.py docs/verification/numeric_similarity_oracle.py`、`git diff --check` 通过。
- 可执行 v2 门禁：`docs/verification/numeric-similarity-fold-evidence.json` 的五次 fresh 运行取得 `EXECUTED_EVIDENCE_PASS`，回执为 `docs/verification/numeric-similarity-fold-receipt.json`；该回执只支持本次数值/标识符/关系运算符折叠场景的 `verified_scope_only` 结论。
- 真实入口页窗：旧版 `/Users/mac/Desktop/oif2024.058.13.pdf` 与新版 `/Users/mac/Desktop/oif2024.058.14.pdf` 第 1–23 页。报告为 [protocol_diff_report.html](/Users/mac/Documents/ProtocolPdfDiffReports/numeric_similarity_fix_20260917_release2/protocol_diff_9vrljqpw/protocol_diff_report.html)。第 17 页卡的 `pair_similarity=0.999608`、`content_similarity=0.999160`、`critical_content_equal=false`，`26450` 与 `26560` 均在主差异证据中，`appendix_card_id` 为空；整份页窗共有 6 条主区正文变化、2 条主区表格变化、0 条相似度附录。

本页窗验证证明了这条用户反例已修复，不外推整本 PDF 的语义对应准确率；报告仍会对抽取或跨页对应不确定的内容标记“需人工复核”。
