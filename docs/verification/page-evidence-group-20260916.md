# 同页表格与正文证据归并

## 用户缺陷

同一旧版/新版物理页同时包含表格和正文变化时，报告把它们拆成独立卡片；页面截图去重只提供跳转链接，读者仍需要在多个位置来回查找，表格复核卡也不在对应页对比下方。

## 修复口径

报告先按旧版/新版物理页建立连通的 `page-evidence-group`，再把该页涉及的表格、正文和单侧证据卡放入同一组。页面组按页序输出，表格优先、正文随后；同组中的每侧原页截图通过 `data-source-alias` 只保留一份，其他 occurrence 保留可回源链接。表格不再因为配对相似度 `1.000` 被移入折叠附录；正文的 `1.000` 折叠规则保持不变。读者投影还会过滤运行页脚的版权/草稿声明、选定页窗的内部范围占位和 OCR 重复科学计数法符号；原始 JSON/CSV 继续保留审计事实。

## 验证

- 定向套件：坐标页家具、来源泛化、表格相似度、范围占位、页级证据归并及截图定位回归共 91 项通过。
- 完整套件：`.venv/bin/python -m unittest discover -s tests -q`，1754 项通过，1 项条件跳过。
- 编译与格式：`compileall` 和 `git diff --check` 均通过。
- 真实入口页窗：旧版 `oif2024.058.11.pdf` 第 19 页与新版 `oif2024.058.13.pdf` 第 18 页，输出 [protocol_diff_report.html](/Users/mac/Documents/ProtocolPdfDiffReports/page_group_fix_20260916_release_candidate2/protocol_diff_a8zzcomh/protocol_diff_report.html)。HTML 只有一个 `page-evidence-19-18` 页面组，包含两张表和正文证据；页面图像源地址 2 个且无重复 ID，所有别名焦点目标均可解析到带 `data-source-view` 的原图节点；表格明细默认展开。
- 同一 HTML 静态核验确认：读者区不再出现 `Copyright © 2026`、`This is a draft`、`范围起始页前序内容` 或 `3.2×x10`；`3.2 × 10` 保留为可读数字；表格相似度附录没有表格条目。

本页窗验证只覆盖上述真实输入和页范围，不外推整本 PDF 或任意 PDF 的语义对应准确率；报告仍按既有规则标记“需人工复核”的不确定证据。
