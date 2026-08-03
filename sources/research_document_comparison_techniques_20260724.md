# 文档比较工具技术对标与本地落地边界（2026-07-24）

## 目标与证据边界

本轮对标 Adobe Acrobat、Draftable、Apryse、GroupDocs、Bluebeam 以及开源 PDF/文本/表格方案，目标是降低协议 PDF 比较中的误报与漏报。商业产品没有公开内部匹配算法和阈值，因此下文只把厂商明确披露的能力当作外部事实；具体算法、门槛和优先级依据本项目真实 OIF PDF 反例或明确标注的合成反例作出，不能表述为复刻某个商业产品。

本项目继续遵守以下边界：读者报告以文字和结构化表格差异为主，不恢复图片/像素差异卡片；低置信度证据保留在 JSON 与质量状态中，不用冗长的抽取警告污染读者报告；Docling 等重模型只有在本地 corpus 证明净收益后才能进入默认路径。

## 成熟工具公开能力的共同模式

| 能力层 | 官方公开证据 | 对本项目的含义 |
|---|---|---|
| 页面/区域先对齐 | [Adobe Compare](https://helpx.adobe.com/acrobat/using/compare-documents.html)会按内容类型选择比较方式，并在演示文稿模式中匹配相似页面；[Bluebeam Compare/Overlay](https://support.bluebeam.com/revu/features/compare-documents-vs-overlay-pages.html)提供自动配准、偏移与局部区域比较 | 页码和页内 ordinal 只能是弱证据；章节号、表题、稳定内容身份和顺序约束优先 |
| 内容分层比较 | [Adobe Compare](https://helpx.adobe.com/acrobat/using/compare-documents.html)区分文字与图形；[GroupDocs Comparison](https://docs.groupdocs.com/comparison/net/comparison/)公开段落/词/字符细度、灵敏度、坐标与表格设置 | 数字、单位、符号应严格；正文允许换行重排；表格不能退化为一条长字符串 |
| 差异成对并可回溯 | [Apryse TextDiff](https://docs.apryse.com/web/guides/textdiff/textdiff)按自然阅读顺序形成连续差异块，并用配对标识关联两侧标注 | 每项事实最终应关联旧/新逻辑对象和来源坐标，避免半行与另一行错配 |
| 移动和增删分开 | [Draftable redline settings](https://help.draftable.com/hc/en-us/articles/16549612535705-Redline-comparison-settings)公开 moved text、字符/词级分辨率及表格选项 | 高相似内容换位置不能一律显示成大段删除加新增；但移动识别必须受单调顺序和身份门禁约束 |
| OCR 按文档事实路由 | [Draftable FAQ](https://help.draftable.com/hc/en-us/articles/30114595020185-Draftable-Legal-Frequently-Asked-Questions)说明扫描文档通过内置 OCR 处理；[OCRmyPDF advanced](https://ocrmypdf.readthedocs.io/en/latest/advanced.html)区分 skip/force/redo 等模式 | 不能只凭固定字符数认定隐藏文字层完整；原生层、栅格覆盖和 OCR 证据应分别保留 |
| 表格拓扑独立处理 | [pdfplumber table extraction](https://github.com/jsvine/pdfplumber/blob/stable/README.md#extracting-tables)公开 lines/text/explicit 策略、cell 与 intersection；[Docling table options](https://docling-project.github.io/docling/usage/advanced_options/#control-pdf-table-extraction-options)公开 accurate 模式和 PDF cell matching | 物理行、列、cell、跨行/跨列和 NOTES 必须成为明确对象；换行不得无证据地产生新数据行 |

## 可借鉴但不直接照搬的开源技术

- [diff-pdf](https://github.com/vslavik/diff-pdf)证明渲染视觉差异适合发现文字抽取之外的变化，但像素差不等于语义差异。本项目不把它放回读者报告；未来如启用，只能作为内部漏抽取哨兵并保持独立质量状态。
- [Google diff-match-patch API](https://github.com/google/diff-match-patch/wiki/API)的 semantic cleanup 可减少碎片化显示，但原仓库已归档；本项目只借鉴分层 diff 思路，不新增这一长期依赖。
- [Git diff algorithm options](https://git-scm.com/docs/diff-algorithm-option.html)公开 Myers、minimal、patience 与 histogram 的边界。协议文档的候选匹配可借鉴低频锚点；“候选相似”与“内容相等”必须保持两套判据。
- [PubTables-1M](https://arxiv.org/abs/2110.00061)把表格检测、结构识别和功能分析分开，并强调 canonicalization；[GriTS](https://arxiv.org/abs/2203.12555)分别评价拓扑、位置和内容。这比单一文本相似度更适合作为未来二维表格回归指标。
- [pytesseract](https://github.com/madmaze/pytesseract)的 `image_to_data` 可返回词级 bbox、行页信息和原始 confidence。该 confidence 不是正确概率；在没有本地标注校准前，只能作为复核证据，不能据此自动判等。
- [Docling confidence scores](https://docling-project.github.io/docling/concepts/confidence_scores/)与 [DoclingDocument](https://docling-project.github.io/docling/concepts/docling_document/)提供组件级分数、bbox、body/furniture 结构和阅读顺序。它适合作为困难页复核器，不适合在当前未安装、未校准的环境中替换默认解析器。

## 当前代码差距审计

当前 HEAD 已具备坐标文字块、阅读顺序风险、按页 OCR、结构化表格行、章节/表格匹配、质量分级和真实语料验证，不能笼统归类为“只有字符串 diff”。本轮确认的高风险边界如下：

1. **同页插表导致后续表格错配**：旧版 `Alpha/Beta`，新版前插 `Inserted` 时，原逻辑把 ordinal 改变当作身份冲突，制造多个假修改；若只看列表索引而不看 bbox，还会吞掉真实物理换序。
2. **隐藏页边文字使扫描页跳过 OCR**：大面积栅格页只要原生层达到 80 个字符便跳过 OCR；合成反例证明，稀疏页眉/页脚足以掩盖图片中的 `0.121 UI → 0.118 UI`。
3. **无框表尚未进入可靠二维表格模型**：纯坐标对齐表格可能只作为正文处理，缺少 row/column/cell/span 级事实。
4. **Docling 候选必须与原生全文完全相同**：该门禁安全但不能真正修复双栏阅读顺序；放宽前必须证明 token、数字、单位、运算符和否定词完全保真。
5. **真实语料 oracle 仍偏关键词级**：`must_find` 不能充分证明 old→new、位置、类型与精确出现次数，后续应升级为结构化 oracle。

## 本轮采用的两项改进

### 1. 同页唯一表题采用单调相对顺序

页内 ordinal 继续保护同名/无编号表，防止相似正文跨表硬配；有效 bbox 的 `(top, x0)` 决定物理顺序，坐标无效时才退回抽取列表顺序。当旧、新两侧存在唯一且完全相同的编号表题时，对共享表题做 LCS 单调锚点对齐：前插表只会平移稳定锚点，局部交叉只影响换序区域，不会污染页面上其余稳定表格。已有唯一同题候选的表禁止在后备通道按 ordinal 串配给另一编号表。

验收反例：旧侧 `Table 1 Alpha, Table 2 Beta`，新侧 `Table 0 Inserted, Table 1 Alpha, Table 2 Beta`。报告必须只有 `Inserted` 一项新增；保持列表顺序但交换 Alpha/Beta 的 bbox 时仍必须产生变化。复合反例 `A,B,C,D → X,A,B,D,C` 中，A/B 不得进入变化，X 必须保持新增身份，C/D 的局部换序必须留痕。

### 2. 扫描页同时检查原生文字的空间覆盖

大面积栅格页不再只用原生字符数决定是否 OCR。页面高度划分为 8 个分段，每个非空 glyph 按中心点只归属一段；若原生字符占据至少 4 段且字符数达到既有门槛，则保留原生层并跳过重复 OCR。稀疏页眉加页脚只占少量分段，不能用首尾跨度或跨分段 glyph 伪造整页覆盖。大面积栅格页缺少字符坐标时继续 OCR，因为无空间证据不能证明隐藏文字层完整。

8 段/50% 是本项目在合成反例上的暂定保守路由阈值，不是 Tesseract 或商业工具公开的通用准确率结论。它与字符数、栅格覆盖、DPI、超时一并写入报告 provenance。所有实际执行 OCR 的页面继续降级为需复核，不能据此自动判定两份 PDF 一致。

验收反例：全页栅格 + 顶部页眉/底部页码，旧/新 OCR 事实分别为 `0.121 UI` 与 `0.118 UI`，必须检出修改并保持 `degraded`；缺少字符坐标时同样不能直接信任长隐藏文字；同源可选文字字符和坐标跨越多数分段时必须跳过重复 OCR。

本地顶层 058、235、532 六份 OIF PDF 在当前 50% 栅格覆盖门槛下没有页面进入 image-dominant 路径，因此上述 OCR 路由目前是合成反例闭环，而不是这些 OIF 文件上的真实扫描件准确率结论。若要提升为实测门禁，仍需加入带人工 old→new oracle 的真实扫描 PDF。

## 后续优先级

1. 把表格升级为 `rows/columns/cells/span/bbox/source/confidence` 二维模型，并用真实 PDF 验证物理行守恒与 cell 级 old→new。
2. 把页面/章节/表格候选匹配统一为“强锚点 + 单调序列对齐 + 歧义拒判”，避免全局贪心在重复模板中强配。
3. 将真实 corpus oracle 升级为 `location/kind/old/new/exact_count`，并记录指定 PDF 是否真正执行；关键词存在只能作召回提示。
4. 在不进入读者报告的前提下，评估内部视觉哨兵是否能发现文字轨遗漏；只有能控制字体渲染、抗锯齿和轻微位移误报时才进入审计路径。
5. 评估词级 OCR bbox/confidence 与本地标注校准；未校准分数只能触发复核，不能生成“高置信正确率”。
