# PDF Protocol Diff Handoff

## 当前任务

- task_id: `pdf-diff-reader-segmentation-integration-v2-20260828`
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 工作分支：`codex/pdf-diff-reader-segmentation-20260826`
- 持久项目分支：`project/pdf-protocol-diff`
- recorded_commit: `e8f7b198e2ab107e354a38efb21f746b0aa4ce75`
- status: ready
- 目标：以显式 legacy-audit 将已完成并验证的读者报告基线合法集成到 `main`，随后开始桌面 UI 重构；本次不改变报告、抽取、配对或差异标记逻辑。

## 已经完成

- 正文卡先显示旧/新 PDF 原文截图，只在真实变化词坐标上覆盖淡红/淡绿半透明底色；相同文字、纯 Table/Figure/Section 定位编号和页边行号不着色。结构化 OCR/文字差异默认折叠。
- 已识别 Table 的页面由 Table 卡独占视觉证据，正文截图不再重复；Table 截图只保留 3 pt 边框安全距离，表题由结构化卡片显示，避免半截表题进入图片。
- Figure 以旧/新原图直接显示，不比较图内 VMA、坐标轴、图号或短标签；没有坐标授权的 Figure 文字会 fail closed 保留在正文中。
- Figure 裁剪范围内的原始字词现在作为坐标归属证据传入读者报告。即使 PDF 阅读顺序丢失 `Figure xx` 前缀，整片框图标签墙也不会再伪装成正文新增/删除；若同一文字块后半段确有普通要求，只剥离已证明属于 Figure 的前缀。
- Figure 坐标证据会按物理页传播给该页的子章节差异卡，而不再只依赖抽取阶段的 owner section id；同一裁剪可清理连续多个 OCR 标签片段，但仍保持逐裁剪授权，避免跨图拼接删除预算。
- 对 Figure 双栏/框图 OCR 的清理现在同时支持顺序片段、列交错字符覆盖和反向坐标轴文本；只有单图坐标字符库存足以证明、且边界不像完整英文句子时才移除，普通要求句的 `All`、`The`、`Amplitude...` 等句首必须保留。
- 坐标归属按单个 Figure 裁剪逐段证明，禁止把同章节多张图的 token 合并成一个删除预算；双栏图造成的交错字母只在单图字符覆盖和顺序相似度同时达标、且片段没有正文谓词时才去重。
- TableVisual 同时保留 bbox 内坐标词作为内部去重证据。即使结构化行不完整，原图坐标仍可从正文文字面移除它明确拥有的短表头；没有 bbox 坐标证明的长混合片段继续保留，不能被不完整表格隐藏。
- Table 与 Figure 连写图题会分别按已知物理页对象清理；Table 的无序字符覆盖只允许删除完整表格片段，不允许像 Figure 那样裁掉混合正文前缀，防止真实句子被表格字符库存误吞。
- Table 字符库存还可删除完整正文句号之后、由同页 bbox 逐字证明的表头尾巴；前面的真实段落保持原样，避免 `... Table 30-13. gDC2 gDC Location ...` 再进入 Markdown/TXT。
- Figure 改为全文级图题和文档顺序单调配对，不再依赖章节差异卡；旧版多图对新版一图时保持明确单侧原图，不伪造对应关系。
- Formula 自动对比已经关闭：显示公式不产生新增、删除、修改、相似度或颜色差分卡，只作为版面阻断区；报告边界会丢弃遗留调用方注入的公式变化，公式周围可可靠抽取的普通说明文字继续比较。
- Formula 禁比同时作用于正文审阅单元：带独立式号且具有多重关系/算术运算或私用数学字形的显示公式墙不会再改名成正文替换进入 JSON/CSV；普通 `Equation (30-1)` 引用句、图表坐标轴后的可读要求仍保留。
- 双侧显式页窗现在可救回多组有真实段落骨架证据的单调章节配对；用户声明相关不再只授权一对。
- 当旧版子章节正文在新版合并进已匹配父章节时，只有至少两个实质正文单元且总量达到门槛才折叠进父章节，避免同一内容同时显示为父章新增和子章删除。
- Figure 下边界会在下一正文、章节、Figure、Table、公式或坐标证明的页脚之前停止；公式式标签和图内刻度不再误切 Figure。
- 正文截图只使用当前章节的 `page_bodies`，并把紧密相连的完整段落行纳入同一出处；页边打印行号即使与短末行合并，也不会导致末行被拒绝或只露出上沿。
- 正文截图现在先重建完整物理行，再应用 Table/Figure/Formula 阻断；任何与阻断区相交的整行都会被丢弃，纵向裁剪不再额外留白，因此不会露出公式左右残片或相邻行半个字形。
- `fb` 下标被 PDF 拆成独立坐标块时，正文成员判断允许一个窄的有序 token 缺口；`Equation (30-2)` 的换行和后续普通说明能够完整显示，真实的 `56→112 GHz` 仍按逐词坐标浅色标注。
- 正文视觉下标族增加坐标证明的 `gDC/gDC2` 与 CTLE 频点 `fp1/fp2/fz/fz1/fLF`；只在字号、基线、水平邻接、一对一关系和整页字符守恒同时成立时重排，不再发布游离 `DC DC2` 或 `LF`，也不做纯字符串猜测。
- 公式邻域判定不再仅凭“英文短句”把上一行普通说明吸入公式区域；真实 OIF 页面中的 `...or a valid CEI signal.` 已完整保留，公式本体继续只作为无标色上下文。
- 跨页句子的页面分配允许相邻页提供独立且足量的词覆盖，避免正文在页中点换页时只显示前半句。
- `Section 29.4.1.2.1 using ...` 这类跨行引用续句不再误识别为新章节，避免 VMA 段落被截断。
- Table、Figure、Formula、prose 采用互斥的视觉归属。报告层只按已授权的槽展示，不重新凭字符串猜测区域类型。
- 当前实现没有新增第二套 OCR、章节器或渲染引擎。Figure caption 的视觉授权使用统一的坐标块入口；原有字符串过滤仍只用于缺少坐标时的保守匹配回退，不能作为隐藏正文的授权。
- 高度相关文档的章节匹配新增文档级单调锚点救援：`Data Patterns` 这类同标题、短引用正文可合并为修改，`AC Common Mode Noise` 这类带共同外部 Clause 定位的改名章节也可正确配对；双侧显式页窗仍优先服从用户锚点，不被文档级规则覆盖。
- 读者层会丢弃清洗后旧文等于新文的伪替换；软换行造成的 `low- frequency`、句末标点、表格下标被拆成 `z (mm) p` 等版面噪声不再发布为技术变化。共享的通用表头只在全部单元格等价且存在坐标抽取折行证据时去重，原始截图仍完整保留。
- 面向读者的 HTML、Markdown、TXT 将可证明的私用字体字形规范化成 Unicode 下标和符号；JSON/CSV 继续保留原始抽取值供审计，避免以显示修复覆盖证据。
- JSON 同时提供完整 raw/audit 字段与和 HTML、Markdown、TXT 一致的 `display_*` 投影；显示摘要不再计入已过滤的软断词、标点、Table/Figure OCR 残片。历史 `omitted_snippet_count` 保持原始审计语义，新增 `display_omitted_snippet_count` 明示读者层省略数。

## 当前状态或阻塞

- 原 claim 因缺少后来新增的受保护持久分支快照，且临时 `codex/*` 分支不能执行 v1 policy adoption，已由原 owner 携带恢复后的 lease 以 blocked 结束；基线提交和远端持久项目分支均完整保留。
- 当前从独立 linked worktree 执行显式 legacy-audit；远端临时分支已在确认与 `project/pdf-protocol-diff` 同为 `d1a06906…` 后删除，持久项目分支保持可恢复。
- 第一处 linked worktree 曾误放在父 RinysProject 根仓内，影响另一个 CDR 任务的 clean 状态；该 audit claim 已取消，测试只中断本任务自己的进程，随后通过 `git worktree move` 将完整候选迁到父根仓之外的 `/Users/mac/PycharmProjects/pdf-protocol-diff-worktrees/pdf-diff-reader-segmentation-integration`，再以本任务重新认领。
- 没有实现阻塞。相关回归已覆盖截图优先、逐词浅色坐标、Table/Figure/Formula 互斥、全文 Figure 配对、父子章节合并、显示公式关闭、页边行号、换行标题和完整段落截图。
- 当前候选使用项目 `.venv` 与外部 worktree `PYTHONPATH=src` 完整重跑为 1200 项通过，耗时 500.561 秒；其中新增 1 项只锁定 legacy-audit handoff 契约，读者报告基线的持久验收日志仍为 1199 项通过、1909 个子用例通过。`git diff --check`、Python 编译检查和 GUI 真实入口冒烟均通过。慢项来自真实 OIF/协议 PDF 的重复抽取、跨页表格与截图回归；后续若优化测试时间，应缓存同一 PDF 的抽取快照，不能缩减真实语料门禁。
- JSON 显示投影追加后再次运行完整套件：1198 项和 1909 个子用例通过，唯一失败暴露了 `omitted_snippet_count` 的历史 raw 契约；改为保留原字段并新增显示计数后，相关 4 项回归、编译检查和 GUI 冒烟全部通过。
- 初次独立审核发现的 Figure/Table 标签残片、`Data Patterns` 拆成新增/删除、Table 30-11 `Zp` 表头假差异、软断词及句点误报均已增加回归并修复。提交后必须重新生成真实 OIF 成品，使 `provenance.build_commit` 绑定确切提交，再让三个独立 agent 复审同一份最终报告和全部联系表。
- 真实 OIF 比对必须继续保持“需人工复核”；视觉哨兵存在未覆盖或歧义页时，不能宣称两份文档可靠一致。
- 最终验收证据不在仓库中伪造固定数字；以 `/Users/mac/Desktop/test/pdf_protocol_diff_oif_final_verified/` 下最终报告、静态摘要、联系表和独立 agent 审核为准。

## 下一步计划

- 若继续提高复杂版面识别率，优先引入带类型的页面区域图，并用 split/merge-aware 匹配处理一个旧块对应多个新块；不要继续向字符串启发式叠加协议专用词表。
- 视觉 late-interaction 检索只能作为低置信度候选召回，不能直接成为规范差异结论。
- 每次改动后必须用真实 OIF 输入生成完整 HTML，检查全部 Table、Figure、prose 联系表，再运行完整测试；单元测试通过不能代替报告视觉验收。

## 不要再踩的坑

- 不要把 Figure OCR 标签墙当作正文差异，也不要在 Figure 原图上绘制大面积黄色覆盖。
- 不要让 Table 同时出现在 Table 卡、正文截图和 Figure 截图中；区域所有权必须互斥。
- 不要用单个右侧数字推断整列页边行号；只有密集且跨越足够页高的坐标簇才能收窄正文边界。
- 不要只检查截图是否生成；必须确认左右未裁切、上下没有半行、公式没有混入 Figure、表题没有以残片出现。
- 不要把静态计数写成视觉结论。最终报告需要实际查看联系表，并由独立 reviewer 对同一提交和同一输出目录给出结论。
