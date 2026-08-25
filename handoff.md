# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-reader-segmentation-20260826`
- status: implementation complete; final OIF visual acceptance is stored beside the delivered report
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 工作分支：`codex/pdf-diff-reader-segmentation-20260826`
- 持久项目分支：`project/pdf-protocol-diff`
- 目标：正文采用结构化词级对比；Table 不在正文证据中重复；Figure 只显示原图；章节截图不跨归属边界、不放大窄残片。

## 当前读者行为

- 正文卡先显示词级替换/新增/删除；旧版和新版 PDF 原文区域放在默认关闭的“查看原文出处（无颜色对比）”中，截图不再覆盖颜色。
- 已识别 Table 的页面由 Table 卡独占视觉证据，不再生成正文出处截图。
- 整页坐标块只要证明存在独立 Figure 图题，该页便退出正文截图通道；Figure 以旧/新原图直接显示，不比较图内 VMA、轴、图号或短标签。
- Figure 下边界由下一正文段落、章节、Figure、Table 或坐标确认的页脚决定。连续换行正文即使首行没有句号、第二行被打印行号打断，也不会被带进 Figure。
- 读者层会过滤 Figure 后连续视觉标签，但完整规范句会重新打开正文边界；JSON/CSV 原始审计事实不删除。
- 正文源截图匹配只允许当前章节的 `page_bodies`，避免父卡借用同页子章节；窄残片和无可读面积的扣除结果直接丢弃。

## 结构和冗余结论

- 没有新增第二套 OCR、章节器或渲染引擎；正文语义差异继续由原比较核心产生，截图模块只负责坐标归属和出处证据。
- Figure caption 判断已收敛为整页 `DocumentBlock` 坐标入口，旧的章节字符串判断已删除，避免两套规则漂移。
- Table、Figure、prose 三类视觉证据在 `ProseSourceVisualGroup` 中分槽保存；报告层只负责按槽展示，不重新推断归属。
- 下一阶段若要继续提高复杂版面识别率，应优先引入带类型的页面区域图和 split/merge-aware 匹配，不要继续向字符串启发式叠加协议专用词表。

## 验证

- 相关回归：149/149 PASS，覆盖词级正文优先、原图无颜色、Table/Figure 互斥、父子章节所有权、窄残片、换行正文边界和页脚裁剪。
- 完整回归：`.venv/bin/python -m unittest discover -s tests -v` → 1120/1120 PASS，503.876 s。
- 字节码编译与 `git diff --check` 通过。
- 真实输入：`/Users/mac/Desktop/oif2021.405.14.pdf` 与 `/Users/mac/Desktop/oif2024.522.06.pdf`。
- 最终报告输出根：`/Users/mac/Desktop/test/pdf_protocol_diff_oif_reader_cleanup/`；最终时间戳目录中的 `visual_acceptance/acceptance_report.md` 记录静态检查、视觉总览和独立 agent 审核。

## 已知边界

- OIF 实测仍保持“需人工复核”；视觉哨兵存在未覆盖/歧义页时，不能宣称两份文档可靠一致。
- Figure 原图保留源 PDF 自带的水印和未能坐标证明为页边栏的行号；它们不再被颜色标记，也不会进入图内文字差异。
- Table/Figure 页面跳过可选正文出处截图会牺牲一部分截图便利，但结构化文字差异仍保留；这是避免视觉重复的 fail-closed 选择。
