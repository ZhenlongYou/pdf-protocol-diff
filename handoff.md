# PDF Protocol Diff Handoff

## 2026-09-07 普适性、准确性和效率设计约束

- task_id: `pdf-diff-general-design-20260907`
- owner: `01a07b86-21ae-7fa1-af49-6ecf75c4a49b`
- status: design_pending
- recorded_commit: `5029f32ae1f76c3fc8eb0871f29509fd2c262c46`
- 用户要求：工具必须具有普适性，不能做OIF特制版，同时确保识别准确性和效率。延续“先分析方案”的阶段，本轮仅固化需求和验收方法，未恢复实现或宣称通过准确性验收。
- 通用规则：使用文档内的几何、排版、字符来源、重复分布、结构及内容证据；禁止以文档名、OIF页码、HCB字样或特定行号终值作为正确性的必要条件。原始字形保留；显示空白字形的处理必须有字体/位置证据，不能统一删除私用区字符或纯数字。配置可声明文档路线，不得偷偷依赖文件身份改结果。
- 结论合同：可靠匹配与变化事实分开。对侧解析不完整、结构冲突、匹配未解决必须保持明确的不确定状态。新增/删除需要对侧有效搜索范围及缺失证据；相同文本的重复出现不能靠全局集合去重吞掉真实删除；拆分/合并/移动须保持每个原文出现位置的身份。正文、图表、页边信息的区域归属须跨阶段传递，截图不得把未授权内容标成差异。
- 准确性验收：分别衡量正文、技术数值/单位、表格单元格、区域配对的误报和漏报，以及自动完成覆盖率/未解决率。不能靠把所有结果降级或隐藏来提高表面准确率。当前用户反例须逐一正确通过，同时真增删、真参数变更、合法小表和重复段落的对照必须保留；不承诺任意PDF的100%准确。
- 普适性验收：按来源/文档家族划分开发集与独立验收集，而非同文档拆页；覆盖不同厂商手册、规范及普通报告，中英混排、无编号/有编号、单栏/多栏、跨页图表、页眉行号、复杂字体、页数变化和内容移动。原生PDF、扫描件及混合页分别评估，未验证类别明确列出。OIF只能作为已知反例回归集，不能同时用作唯一泛化证据。真实文件人工核对与可控变更样本共同使用，变更生成器不充当唯一真值。
- 效率方案：原生文本优先；按证据对需要的区域启用OCR；用内容索引和可靠锚点缩小匹配候选，保持完整的候选遗漏/未解决记录；缓存绑定文件hash、页窗、配置和引擎版本；结果与截图分阶段生成、按需加载，避免报告一次嵌入所有图片成为新瓶颈。保留进度、取消、超时和完整结果原子发布，不通过截断正文或忽略难页提速。
- 效率验收：固定硬件和输入，在真实100/500/1000页规模及不同变更密度下记录提取/结构/匹配/OCR/报告阶段耗时、峰值内存、报告体积、取消延迟与失败恢复；分别报告首次和重复运行，使用实际长文档，复制短页只能做压力测试不能替代真实样本。时间指标必须和同批准确性、覆盖率并列；跨文档性能基线尚未测量，绝不先发布统一分钟数承诺。
- 实施前应先完成：当前数据流/证据丢失点梳理、通用中间表示和逐条结论条件设计、独立验收样本与真值方案；再按完整处理链实现，复用已经有效的PDF/OCR后端和性能/终止工作。各类别定量通过阈值与耗时预算待建立基线后提出，不能把尚未约定的百分比当成已批准要求。
- 本阶段产物：只有本handoff约束记录，生产代码仍为main `44b875b90e7168a5134813622c311355cfe9588a`；持久项目分支保存分析检查点。后续从本终态任务冻结的持久分支OID继续；本次不是功能交付或main集成。

## 2026-09-07 OIF 准确性方案分析（用户要求先分析，停止补丁交付）

- task_id: `pdf-diff-small-image-20260907`
- owner: `01a07b86-21ae-7fa1-af49-6ecf75c4a49b`
- status: design_pending
- recorded_commit: `44b875b90e7168a5134813622c311355cfe9588a`
- 最新用户明确要求：先从整体方案分析为何错配，不能继续缝补。本阶段仅交付诊断与重构方向；未实施准确性修复。生产代码已恢复上述基线，之前已完成的性能/终止功能保持原样。
- 曾临时修正 `.table-shot img` 的 width:100% 并运行 1261 项测试（1 skip）；用户转向方案分析后该未交付修改与3个验证文件已移到外部 `performance_fix_20260907/paused_layout_candidate/`，仓库不留代码补丁。`protocol_diff_report_readable.html` 只是样式候选，识别结果未修复，不应作为新准确性报告发布。原始报告保留。
- 反例一：旧5.2 p561/T2 为 Figure25-9 内的 HCB 小框（72x72像素），p345/T1 为 Figure16-10 内同类框（66x68）。新版p561仍有HCB，报告却标删除。图框被当表格与CSS强制铺满共同放大；仅改尺寸不解决错误删除。
- 反例二：双方p351/352同有16.4.1.1及16.A正文；旧、新章节祖先被错误变成 `Appendix 2 > 11 > 16.4.1 > 16.4.1.1` 与 `Appendix 2 > 14 > (1) > 16.4.1.1`，边界也分别吞到p352/p354。p127正文跨行 `Appendix 2.E.7, of ...` 被当标题，污染后续栈；16.A与第18章真实标题被上下文规则拒绝。共同短句 `Refer to Section 3.2.8.`（双方p381）所在17.3.2.7同样吞入18章并误报增删。
- 反例三：旧版656/656页保留ambiguous行号；新版671/685页去除行号、14页不确定。旧p381每个1..48行号后有独立U+F020字形，破坏空白基线证明；诊断内仅去掉这些字形可通过，p382/550本页本来可通过但被全局80%门槛撤销。两版都是1..49，不能改成48或简单下调阈值。原始材料应保留，但不能因此授权确定差异。
- 反例四：p550双方Table25-7均被完整检测。行号在表格左侧成为正文高亮锚点；每个锚点被表格阻断而独立分簇，再扩宽至页面64%，最终穿过表格生成16个细条。问题是区域归属与扩宽后的不相交约束缺失。
- 结论门禁缺口：compare.py `_match_sections` 将所有剩余项设为unmatched，compare_sections直接变added/deleted；quality仅全局degraded且编号路径非空就计稳定章节；截图层不继承ambiguous和匹配未解决状态，允许单数字红绿标色。测试绿和性能前后字节相同只能证明原行为保持，不能认证全OIF准确性。
- 建议整体改造顺序：统一不可变页/词/区域来源与归属及不确定状态；把原文编号、推断章节树和匹配身份分开，约束弱标题的远距离污染；分层匹配支持拆分/合并/移动并保留失败原因，未匹配默认待定位而非增删；逐条结论证据门禁和报告纯展示，按完整归属区域裁剪，不再从字符串重新猜所有权。复用已有PDF/OCR后端、坐标数据、取消/进度和性能优化，不先整体重写。
- 验收先冻结用户反例与人工真值，另外保留真实增删、参数变化、合法小表、跨页/跨章移动的独立样本；分别量化误报、漏报、未解决覆盖与资源耗时，不能通过隐藏不确定结果假装准确率提高。
- 证据：外部根 `/Users/mac/Documents/ProtocolPdfDiffReports/performance_fix_20260907`；匹配追踪 `accuracy_diagnosis_matching/matching_diagnosis_evidence.json`；原报告在 `full_final/protocol_diff_20260907_220704_fe2adf6f8e1a4851b7eba2579a8832bc/`；原始提取在相邻 `performance_audit_20260907/old.pickle,new.pickle`。独立只读诊断由 `diagnose_oif_matching`、`diagnose_oif_prose_crops`、`inspect_oif_hcb` 完成。浏览器file协议禁止访问报告，未绕过；本轮未完成全报告浏览器重验，任务浏览器已关闭。
- 恢复：本阶段只提交分析handoff/开放缺陷账本到 `project/pdf-protocol-diff` 并取消实施claim，main保持44b875。后续在此持久分支上依据终态claim和确切远端OID恢复同范围任务，再按方案定义验收与实施；不得将本次分析称为修复完成。

## 2026-09-07 长 PDF 性能与终止

- task_id: `pdf-diff-performance-cancel-20260907`
- owner: `01a07b86-21ae-7fa1-af49-6ecf75c4a49b`
- recorded_commit: `bff2fdd53474c2baab9037f44642d711fe274a74`
- status: ready
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`；持久分支 `project/pdf-protocol-diff`；起点 `db45c03f8d4bc0c15485349403449920f52bc736`。继承 cwd 已是此仓库的符号链接。
- 用户授权：修复几百页 PDF 处理过久及不能终止的问题。原始约 907 页、89 分钟无结果的文件不可用；使用本地 OIF CEI 5.2（656 页）和 5.3（685 页）。不承诺该原始故障已经逐文件复现。
- 已实现：独立工作进程、OCR 子进程树终止、关闭窗口先清理、取消后恢复和重启、只有完整报告才原子发布；清理失败保留所有权并重试。界面显示终止按钮、运行时间及扫描/章节匹配/正文差异阶段。
- 性能处理：数字词规范化避免重复全流扫描；视觉下标只枚举几何可能邻居且保留浮点边界；单任务有界纯函数缓存；阈值上界排除不可能候选；长重复文本通过 suffix automaton 保留 difflib 的最长连续匹配、方向及最早位置规则；报告清理复用不可变结果并跳过表格路径未消费的图注判断。表格与整页 OCR 单次均有 60 秒上限；整份累计 OCR 预算仍未实现。
- 提取实测：基线 448.593 + 337.225 秒，优化后 160.626 + 154.835 秒；两份完整提取 checkpoint 的字节 SHA-256 均与基线相同。原始基线没有完成整个比较，不能给出整份对比的基线加速比。
- 长字符串独立证据：65025 二字母有序对、另一路 132496 三字母有序对完整匹配块一致；非零子区间、多语和真实长度 4095/4096/4097/5003 验证通过。实际 OIF 约 9.6 万和 16.2 万字符 key 的新算法分别约 0.09/0.18 秒；9 组真实切片与标准库完整匹配块一致。没有运行旧巨型 key，不能虚构其加速比。
- 本地最后行为变更后全套 1260 项，168.382 秒通过（1 条条件 skip）；阶段/清理定向 21 项通过。v2 `receipt-cleanup.json`：16 个实际执行 run，`EXECUTED_EVIDENCE_PASS`，SHA-256 `347f23feb47bc17567f2039083503bdd62298ad04095c75724ad42e5802aab26`。
- 原生窗口验证：读取旧版 371/656 页时终止，423 ms 后观察到取消及表单恢复；章节匹配中也可取消；生成视觉证据约 17 分钟时终止，513 ms 后观察到取消。随后用户亲自运行 235.13/235.14 并确认完成，界面显示正文 7、Table 1、视觉 1。该用户报告保留在外部证据目录的 `gui_full/protocol_diff_20260907_214359_26bdd5933b944330bf4eaa1a0b4fffdf`，不得作为清理用临时文件删除。
- 最新跨平台 CI：`https://github.com/ZhenlongYou/pdf-protocol-diff/actions/runs/34129381087` 对同一代码 bff2fdd，macOS/Windows 各 1261 项通过（29 条环境条件 skip），打包及原生启动检查通过；两端只有 Upload artifact 因 GitHub artifact storage quota 失败。没有新的可下载安装包；未执行 frozen Windows 包中的真实 OCR 树取消。
- 完整 OIF 最终冷读通过同一桌面异步门面在后台完成，避免干扰用户窗口：1703.406 秒（约 28 分 23 秒），六个输出完整发布，工作进程已退出、无本次临时目录。终态记录 `full-final-result.json`；最终报告 `full_final/protocol_diff_20260907_220704_fe2adf6f8e1a4851b7eba2579a8832bc/protocol_diff_report.html`。这是本机本次运行，不能外推为原始 907 页文件的耗时保证。
- 中间版本完整比较/报告重放 1779.505 秒，最后清理优化后同阶段重放 1373.737 秒（均不含提取）；不能当作原始 db45 基线。两次重放与最终冷读的 JSON、两份 CSV 均字节相同；HTML/Markdown/TXT 各仅一处生成时间不同，3870 张图像和全部报告内容均保持一致。记录 `full-report-cleanup-equivalence.json` 与 `cold-full-verification.json`。
- 报告检查范围：参考报告页窗 656/685，3870 张内嵌图片全部可解码、无坏锚点；独立抽看 Table/Figure 源图可读。完整浏览器排版及全内容准确性未认证。报告保留 degraded：视觉哨兵 0/2 对完成、2 对失败、174 页未安全配对。额外观察：旧版 p358 正文样图把页边 40–48 行号及页脚标红，未证明本轮引入，仍需后续独立处理，不能宣传标色完全准确。
- 本轮唯一外部证据目录：`/Users/mac/Documents/ProtocolPdfDiffReports/performance_fix_20260907`；基线目录 `performance_audit_20260907`。重复提取 pickle 已确认与基线字节相同后移除 107260284 字节；原始 PDF、基线 checkpoint、用户报告均保留。私有材料和报告不进入 Git。
- 交付约定：最终 handoff 记录上一个已知代码 OID；两个独立 reviewer 对包含本段的最终 OID 写 attestation，main 与项目分支必须快进到同一最终 OID。实际集成与推送结果以外部 `delivery-receipt.json`、`delivery-gate.log` 及共享 claim 的 integrated 终态为准。后续准确性工作可从上述 p358 标色观察继续，本轮不宣称解决该问题。

## 2026-09-02 差异掩膜改为读者层默认折叠

### 当前任务

- task_id: `pdf-diff-reader-collapse-visual-mask-20260902`
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 工作分支：`project/pdf-protocol-diff`
- recorded_commit: `98798af38c7b3635d3b50a3b0fab0f2b5e41b949`
- status: ready
- 目标：保留像素掩膜的审计价值，但不再让它作为普通读者的主要内容直接展开。

### 已经完成

- 旧/新协议原图继续直接展示；差异掩膜改为原生 `<details>` 折叠控件，默认关闭，标题为“像素变化定位（技术复核）”。
- 展开后明确提示：“红色仅表示像素发生变化，不等同于协议参数或文字内容发生变化。”避免把像素变化误读为协议参数变化。
- 读者披露层级已加入用户逃逸缺陷账本、变异 RED、修复 GREEN、独立 oracle、六类固定输入和真实报告重开验证。回执 `/Users/mac/Documents/ProtocolPdfDiffReports/visual_mask_reader_collapse_evidence/test-effectiveness-layout-v2.json`，SHA-256 `cc1e054d24b399349602cd946c12596b7f3c7c6a5db4c4c4260cd2f49b1af63f`，结论 `EXECUTED_EVIDENCE_PASS`。
- 完整项目测试 `1238/1238` 通过，另有 1 项按环境条件跳过（465.101 秒）；两个 GUI smoke、编译和差异检查通过。
- 桌面真实 PDF 最终报告：
  - 235 组：`/Users/mac/Documents/ProtocolPdfDiffReports/visual_mask_reader_collapse/oif235/protocol_diff_20260902_013034/protocol_diff_report.html`
  - 058 组：`/Users/mac/Documents/ProtocolPdfDiffReports/visual_mask_reader_collapse/oif058/protocol_diff_20260902_013033/protocol_diff_report.html`
- 真实 Chromium 初始状态：1 个技术折叠项、0 个展开项、2 张旧/新原图可见、0 张掩膜可见；点击后 1 张掩膜可见且限制说明完整。折叠截图为 `/Users/mac/Documents/ProtocolPdfDiffReports/visual_mask_reader_collapse/oif235-mask-collapsed.png`。

### 当前状态或阻塞

- 无功能阻塞。报告仍为 `degraded/manual review`，折叠只改变阅读层级，不改变识别覆盖率或审计事实。
- 独立 reviewer agent 未运行：项目 AGENTS 只允许在用户明确请求时启动 reviewer agent。本轮已用完整测试、变异检测、独立 oracle 和真实浏览器路径完成主代理验收。

### 不要再踩的坑

- 像素掩膜是诊断证据，不应默认占据普通读者的主阅读流。
- 折叠不能等于删除；技术审核人员必须仍可展开并看到原始掩膜与含义边界。
- 不得把“红色像素”描述为“协议参数已变化”；语义结论必须来自正文、表格或公式证据。

## 2026-09-02 视觉证据异常放大与横向裁切修复

### 当前任务

- task_id: `pdf-diff-visual-evidence-native-scale-20260902`
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 工作分支：`project/pdf-protocol-diff`
- recorded_commit: `469a4dbd7d54e2106fcd215dd613e0c5ba9bd807`
- status: ready
- 目标：修复视觉复核图片区异常放大，以及右侧值变化导致表格左半边上下文被裁掉的问题，并用桌面四份 OIF PDF 完成真实报告闭环。

### 已经完成

- 根因一：共享 `.table-shot img { width: 100% }` 把本来较小的视觉证据强制撑满容器。视觉复核卡现在使用独立规则 `width: auto; max-width: 100%`，窄容器可缩小，但绝不超过图片原始像素放大。
- 根因二：视觉哨兵按变化像素外接框同时裁剪横纵轴；当变化在表格右侧数值列时，左侧 `Characteristic / Symbol / Condition` 等解释列被删掉。现在横向始终保留整页宽度，只在纵向围绕变化行裁剪。
- 两个用户发现的逃逸缺陷已写入权威账本，并增加变异 RED、修复 GREEN、独立几何 oracle、六类固定输入和真实 HTML 重开路径。测试有效性回执 `/Users/mac/Documents/ProtocolPdfDiffReports/visual_layout_fix_final/test-effectiveness-layout-v2.json`，SHA-256 `154a91021a29a7ef2099c9c5e7582ac758b3d81b26c4698a8b9b9315ee96e18a`，结论 `EXECUTED_EVIDENCE_PASS`。
- 完整项目测试 `1238/1238` 通过，另有 1 项按环境条件跳过（464.327 秒）；编译、差异检查、两个 GUI smoke、定向回归、独立 oracle 和真实报告路径均通过。
- 桌面真实 PDF 最终报告：
  - `oif2023.235.13.pdf -> oif2023.235.14.pdf`：`/Users/mac/Documents/ProtocolPdfDiffReports/visual_layout_fix_final/oif235/protocol_diff_20260902_011249/protocol_diff_report.html`
  - `oif2024.058.13.pdf -> oif2024.058.14.pdf`：`/Users/mac/Documents/ProtocolPdfDiffReports/visual_layout_fix_final/oif058/protocol_diff_20260902_011249/protocol_diff_report.html`
- 真实 Chromium 测量：两份报告的旧/新视觉证据均为自然宽度 816 px、实际显示 430 px；差异掩膜自然宽度和实际显示均为 816 px，没有放大。235 组截图 `/Users/mac/Documents/ProtocolPdfDiffReports/visual_layout_fix_final/oif235-visual-review-fixed.png` 已确认第 12 页表格左侧解释列完整可见；058 组同类截图为 `oif058-visual-review-fixed.png`。

### 当前状态或阻塞

- 无代码阻塞。两份真实报告仍保持 `degraded/manual review`，因为视觉哨兵存在未安全配对页或失败页对；本次只修复证据展示完整性，不能把报告改写为“自动确认全部差异”。
- 根据项目 AGENTS 约束，本轮未启动独立 reviewer agent；用户未明确请求代理复审。已执行主代理多轴代码检查、完整套件、变异检测、独立 oracle 和真实浏览器视觉验收。

### 下一步计划

1. 推送 `project/pdf-protocol-diff`，快进同步 `main`，并复核远端 exact OID。
2. 后续若希望差异掩膜更紧凑，可按连通区域拆成多张“保留整行横向上下文”的卡片；不得再次裁掉左侧解释列或把图片放大超过自然尺寸。

### 不要再踩的坑

- `.table-shot` 同时服务普通表格和视觉复核证据，不能用一个 `width: 100%` 规则覆盖两种尺寸语义。
- 右侧数值列的差异没有左侧行名就不可解释；视觉复核截图必须保留整页横向上下文。
- 浏览器中“看起来合适”不足以验收，要同时核对 `naturalWidth` 与 `clientWidth`，并实际查看完整表格左右边界。
- 修复报告展示不等于提升识别覆盖率；`degraded/manual review` 边界必须保留。

## 2026-09-02 视觉差异掩膜误导修复与桌面 PDF 闭环

### 当前任务

- task_id: `pdf-diff-visual-mask-closed-loop-20260901`
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 工作分支：`project/pdf-protocol-diff`
- recorded_commit: `bc1ee2ad3b66501fb447890a8d30ee9606b949df`
- status: ready
- 目标：修复视觉差异掩膜把分散变化用一个大红框连起来的问题，并用桌面四份 OIF PDF 通过当前 WebView GUI 完成两组真实比较。

### 已经完成

- 根因已定位：`diff_bbox` 本应只用于裁剪上下文，但旧预览同时把它画成外接红框，导致分散像素变化之间的未变化表格内容也被圈入。真实 PDF 中 `1000/1.0/30/80/1.25/11.8/0.4/0.5` 的品红色变黑色是实际样式变化，不是页配对错误。
- 预览现在仅将材料变化掩膜像素标为红色；外接框仍只用于裁剪。新增分散变化回归、独立像素 oracle、真实报告路径探针、六类固定输入、缺陷账本和可执行测试有效性证据。
- 完整项目测试 `1237/1237` 通过，另有 1 项按环境条件跳过（467.102 秒）；定向回归、独立 oracle、真实报告路径、编译、`main.py --gui-smoke-test`、`gui_app.py --smoke-test` 和 `git diff --check` 均通过。
- 可执行测试有效性回执为 `/Users/mac/Documents/ProtocolPdfDiffReports/visual_mask_fix_evidence/test-effectiveness-receipt-20260902-v2.json`，SHA-256 `5a5e148af80f1e7449c397a7562a993344dd3a3583637c451bd512d41940fbfc`，结论 `EXECUTED_EVIDENCE_PASS`。
- 当前 WebView GUI 已从权威入口重新打开并核验，包含最近确认的深靛紫双 PDF 界面提交；本轮没有修改 GUI 文件。通过同一 GUI 生成：
  - `oif2023.235.13.pdf -> oif2023.235.14.pdf`：`/Users/mac/Documents/ProtocolPdfDiffReports/protocol_diff_20260902_002551/protocol_diff_report.html`
  - `oif2024.058.13.pdf -> oif2024.058.14.pdf`：`/Users/mac/Documents/ProtocolPdfDiffReports/protocol_diff_20260902_002739/protocol_diff_report.html`
- 两组 GUI 产物都保持 `degraded/manual review`，输入 SHA-256 与视觉哨兵源哈希一致；235 组为正文 7、Table 1、视觉 1，058 组为正文 6、Table 1、视觉 2。

### 当前状态或阻塞

- 代码和真实入口闭环无阻塞。两份报告不得改写为“自动确认全部差异”：235 组视觉哨兵核对 7/8、失败 1、未安全配对页 10；058 组核对 9/10、失败 1、未安全配对页 6，因此仍需回到源 PDF 人工复核。
- 独立 reviewer agent 未运行：当前任务未获得用户对 reviewer agent 的显式授权；已执行主代理多轴代码审查、独立 oracle、变异检测和完整测试。

### 下一步计划

1. 交付当前修复提交、两份 GUI 报告和测试有效性回执。
2. 若继续提升视觉审阅体验，可按连通区域生成多个局部卡片，但不能把多个区域再画成一个联合边框。

### 不要再踩的坑

- `diff_bbox` 可以决定裁剪范围，不能作为差异覆盖层；覆盖层只能来自材料变化掩膜。
- 文本相同不等于视觉相同。颜色、字重或图形变化应保留在视觉复核区，但不能扩大到未变化像素。
- GUI 与 HTML 报告是两套界面；核对“是否旧版”时必须检查权威入口、包含的 UI 提交和本轮实际改动文件，不能仅凭报告页外观判断。
- 真实 OIF 全文报告中的 `degraded`、失败页对和未安全配对页必须保留，不得用绿色测试替代人工复核边界。

## 2026-08-31 识别效率优化复盘文档

### 当前任务

- 用户要求一篇独立可读的 Markdown，说明文件对比工具从低效识别到可审计差异识别的解决过程。

### 已经完成

- 新增 `docs/文件差异识别效率优化复盘.md`，基于当前源码、历史实现提交和 OIF 532、112G/224G 实测记录还原问题、方法、验收和边界。
- 文档明确区分有效识别/审阅效率与端到端运行速度；缺少同条件前后耗时数据，因此未声称倍数提速。
- 关键技术主张已绑定精确 Git 快照、文件和行号；验收数据保留 `precision=null`、`degraded` 和人工复核边界。

### 当前状态或阻塞

- 无。严格中文文档审读为 0 项发现；20 个带行号的历史/源码定位与 4 个提交链接已使用本地 Git 对象校验，`git diff --check` 通过。

### 下一步计划

1. 本次无必需的后续实现。
2. 若后续需要单独回答“运行速度提高了多少”，建立同机、同 PDF 快照、同页窗和同 OCR/版面设置的前后性能基准。

### 不要再踩的坑

- 不要把用例数或某一组 Gold recall 改写成全局准确率。
- 不要在没有同机、同输入、同页窗和同 OCR/版面设置的前后测量时声称性能提速倍数。
- 还原识别改进时继续使用 `source-cited-documents`、`natural-chinese-docs` 和 `delivery-acceptance-gate`。

## 2026-08-29 WebView 统一桌面界面

- task_id: `pdf-protocol-diff-glass-studio-ui-20260829`
- base: `e5a97167cbff047bd286e01383e7f64b7935153f`
- branch: `project/pdf-protocol-diff`
- recorded_commit: `396add22c6bb1e6665a6d47aea76a99ee92c80ec`
- status: ready
- delivery_note: Windows exact startup visual PASS; broader native state-chain gate remains manual review
- 生产入口 `gui_app.py` 已从 Tk 改为一份共享 HTML/CSS 的 pywebview 壳：macOS 使用 WKWebView，Windows 强制 `edgechromium`（Edge WebView2），禁止 MSHTML 回退。`desktop_gui.py` 仅保留迁移兼容，不被生产入口引用。
- 界面实现位于 `src/protocol_pdf_diff/webui/index.html`，采用用户确认的深靛紫玻璃工作台、紫/粉双 PDF 卡、精简文案、无交换按钮、页面范围切换、折叠高级设置和真实运行阶段状态条；760×520 改为卡片区纵向滚动，固定操作区不横向溢出。
- `ProtocolDiffJsApi` 仅暴露六个必要方法。后台比较线程非 daemon；写报告期间阻止关闭。文件/目录选择、任务启动、默认设置和打开结果的 Promise/系统失败都显示明确错误并恢复界面。高级设置使用 `aria-modal`、背景 `inert`、Tab 焦点循环、Escape 和焦点恢复；reduced-motion 停止状态动画。
- 成功状态只读取人类报告的 Markdown 汇总；汇总缺失、损坏或含负数时 fail closed，不回退到原始引擎计数。PDF 抽取、匹配、报告和标色算法未修改。PCIe 3.0 物理页 16–18 对 PCIe 4.0 物理页 33–35 已生成支持性报告；该报告为 degraded 且生成时 dirty state 未归档，只能作为非 exact 回归证据，不能据此给出无差异结论。
- macOS 最终冻结包 `dist/ProtocolPdfDiff.app` 已真实启动并通过 renderer probe；冷启动 smoke 为 0.69 秒。源码与包内 `index.html` SHA-256 均为 `1ecf600ca3602578fab798a2c0dc2ff86dd0d1ab80c029bfb216da4161e809f8`。
- 最终稳定快照完整套件 `1237/1237` 通过（502.289 秒）；WebView/跨平台定向 `37/37`、编译、两种 GUI smoke 和 `git diff --check` 通过。三路独立复审的代码质量、安全/视觉、Windows 工作流代码门禁均 PASS，P0/P1/P2=0。
- Windows Actions 使用 onedir 产物做原生门禁，避免 onefile 启动器父子 PID 歧义；同一 GUI 进程在 DOM loaded 后验证 WebView2、双列、backdrop、动画和 overflow，再用同目录临时文件加 `os.replace` 原子发布 probe，截图脚本等到 probe 后才拍并要求进程干净退出。本地 `build_windows.bat` 仍可生成同一 HTML/WebView2 的 onefile 分发包。
- 有效本地视觉证据：`/Users/mac/Desktop/test/pdf_protocol_diff_webview_ui_20260829/macos-frozen-window.png`、`chromium-edge-wide.png`、`chromium-edge-advanced.png`、`chromium-edge-narrow-idle.png`、`chromium-edge-narrow-scrolled.png`。旧的无真实桥接 running/narrow 截图和私密全屏图已可恢复地移入废纸篓，不得作为证据引用。
- 最终 exact 产品/证据提交为 `396add22c6bb1e6665a6d47aea76a99ee92c80ec`；GitHub Actions run `33203158047` 的 macOS/Windows 1237 项测试、打包、Windows WebView2 启动、原生截图和 artifact 上传全部成功。
- Windows 正式原图为 `/Users/mac/Desktop/test/pdf_protocol_diff_webview_ui_20260829/github-run-33203158047/artifacts/windows-webview2.png`，1044×720，SHA-256 `ba90e213b246ff6681b605ce7950fbff6d004470002393af0969149ac4af79af`；probe SHA-256 `042c995f5ce66190595af41ed24e635d9383072e8c736a9f0857cb7edb0cb7b2`。主审与两名独立 reviewer 均确认标题栏、双卡、底栏和主按钮完整，无裁切、任务栏、桌面或黑屏；Windows 启动视觉正式 PASS。
- 最新测试有效性报告位于 `/Users/mac/Desktop/test/pdf_protocol_diff_webview_ui_20260829/test-effectiveness.json`，独立审核记录位于同目录 `review-attestations.md`。总 verdict 诚实保持 `manual_review`：exact Windows idle startup 已 PASS；native running/success/error、冻结 GUI 驱动的完整 PCIe 流程、未来空白截图自动语义门禁和所有 WebView2 后代退出观测未形成完整证据。
- 首次 exact-commit CI 在 macOS 的既有 Annex 表抽取用例暴露干净环境缺少 `pymupdf`；本地 `.venv` 已有该包所以未暴露。现将 `PyMuPDF>=1.24.0` 同步加入 requirements 与 pyproject，避免干净 Windows/macOS runner 因未声明测试/运行依赖失败。
- 第二次 macOS clean CI 的 1237 项测试全部通过；冻结 smoke 在 runner 开启 `prefers-reduced-motion` 时按 CSS 正确停用动画，但旧探针误要求 `run-slide`。探针现同时记录 reduced-motion：正常设置要求 `run-slide`，减少动态效果设置要求 `none`，避免把无障碍合规行为误判为渲染失败。
- 早期 Windows 证据先后暴露任务栏混入和工作区裁剪右/下边缘的问题。最终脚本不再复制桌面像素，也不再使用 DWM/work-area 裁剪；它用 `GetWindowRect` 建立完整画布并以 `PrintWindow(PW_RENDERFULLCONTENT)` 离屏渲染同一窗口。
- 截图前和 `PrintWindow` 紧邻前均用 `IsWindow + GetWindowThreadProcessId` 绑定启动进程；异常清理使用已持有的 `Process.Kill(true)` 而非 PID 字符串或宽泛进程匹配，避免 PID 复用误杀。正常关闭失败会令证据步骤失败。

## 当前任务

- task_id: `pdf-protocol-diff-premium-ui-20260828`
- 权威仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`
- 工作分支：`project/pdf-protocol-diff`
- 持久项目分支：`project/pdf-protocol-diff`
- recorded_commit: `e073e3be5f83e81932455c352c2549b05c0cc092`
- status: ready
- 目标：在完全冻结报告、PDF 识别、配对和差异标记语义的前提下，将桌面启动界面改为用户选定的 B 色调双文档工作台，并用自动化门禁禁止冗余持久文案回归。

## 已经完成

- 用户选定的 B 方案已落地：深靛紫画布、紫/粉双文档识别、冷青单一主操作；高级感由对称、留白、字号和几何层级承担，未使用渐变、阴影或解释性填充。
- 启动页仅保留一个标题、旧/新 PDF 双卡、交换、三个真实设置摘要、高级设置和固定主操作。已删除英文品牌行、能力副标题、永久徽章、重复总结和右侧说明面板。
- 本轮问题根因已定位：最初仅调用了原型 skill，没有路由到本机 `ui-ux-pro-max` 的 UI Copy Minimalism Gate。现已把关键禁止文案和主视觉几何约束写入 `tests/test_cross_platform_gui.py`，后续不再依赖 skill 是否被当前对话正确暴露。
- 宽屏保持双卡并排，窄于 900px 时真实纵向重排并滚动，固定底栏始终可见。正式视觉证据为 `/Users/mac/Desktop/test/pdf_protocol_diff_premium_ui_20260828/startup-b-tone-minimal-copy.jpeg` 和 `startup-760x520.jpeg`。
- 完整项目虚拟环境套件 1219/1219 通过；GUI 定向 19/19、编译检查、差异检查、`main.py --gui-smoke-test` 和 `gui_app.py --smoke-test` 全部通过。
- 真实 GUI 公开路径已完成 PCIe 3.0 物理页 16–18 对 PCIe 4.0 物理页 33–35，报告位于 `/Users/mac/Desktop/test/pdf_protocol_diff_premium_ui_20260828/pcie_real_gui/protocol_diff_20260828_223313/`；报告引擎源码未改动。
- 第一路独立视觉审核 PASS，P0/P1/P2 均为 0，确认冗余说明清零、宽屏无截断、窄屏可滚动且主操作始终可见。第二路代码审核曾复现长输出目录使窄屏按钮越界的 P2；现已中间省略目录名、让按钮独立换行并增加真实右边界断言，复审 PASS。重复的 `workspace_layout_mode` 状态也已删除。

- 每张文档卡提供“全部页面/指定范围”。范围输入只在指定模式显示；切回全部页面保留填写值但运行时明确忽略。默认仍为全部页面、阈值 0.72、每章 20 个片段。
- 输出目录、阈值、片段数、未变化章节及自动打开报告收进默认折叠的高级设置；自动打开默认关闭。
- 运行状态锁定全部输入，并按“读取旧版→读取新版→匹配差异→生成视觉证据→生成报告”显示真实阶段和耗时；PDF 仅在完成全页坐标预扫描后显示实际页数进度，后续阶段不伪造百分比。
- `run_diff` 和 PDF 抽取入口增加可选只读进度观察器。回调缺省保持兼容，回调异常 fail-open；真实 demo 在冻结生成时间后证明六类报告与无观察器路径逐字节一致。
- 成功底栏从已生成 Markdown 汇总读取读者层计数，确保与 HTML 的过滤/聚合口径一致；OIF 风险状态会明确显示“视觉校对需人工复核”，不再用 raw audit 数字或“0 项”暗示无风险。
- 运行中关闭窗口会被阻止，避免 daemon 线程在逐文件写报告时被终止；macOS 的窗口关闭、应用菜单、Dock 和 Command-Q 均绑定同一保护回调。浏览器和文件管理器打开失败均保留成功状态并给出可操作提示；长异常只在对话框完整显示，固定底栏显示有界首行。
- 已增加页码模式、高级设置、自动打开、阶段顺序、预扫描时序、实际页数、运行控件锁定、timer 取消、成功/失败恢复、读者计数一致性和打开失败保护的回归测试。

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

- 本轮源码与回归选择器提交为 `e073e3be5f83e81932455c352c2549b05c0cc092`；最终 handoff 提交将在其后单独生成。
- 项目 `.venv` 完整套件 1219/1219 通过（551.074 秒）；新增精确 premium UI 选择器后 GUI 定向 20/20 通过，两种 GUI smoke、编译检查和差异检查通过。
- 仓外验收根目录唯一为 `/Users/mac/Desktop/test/pdf_protocol_diff_premium_ui_20260828/`；包含宽/窄屏真实 Tk 截图、测试有效性矩阵以及 PCIe 指定页窗的真实 GUI 报告。
- `295a5f6f3919da2c89019ba6b8029cc087d9d3cb..e073e3be5f83e81932455c352c2549b05c0cc092` 仅修改桌面 GUI 和对应测试；`compare.py`、`pdf_extract.py`、`reporting.py` 和报告格式未改动。
- 两路独立审核的视觉/代码结论已通过；此前报告绑定和旧交接段落冲突已精确修正，待最终 exact-commit 复审。
- 当前无代码阻塞。真实 OIF 比对仍必须保持“需人工复核”；这是冻结报告语义，不属于本次 UI 改动。

## 下一步计划

- 本次交付只剩最终 exact-commit 两路复审、GitHub OID 复核与原子 delivery gate。
- 若继续提高复杂版面识别率，优先引入带类型的页面区域图，并用 split/merge-aware 匹配处理一个旧块对应多个新块；不要继续向字符串启发式叠加协议专用词表。
- 视觉 late-interaction 检索只能作为低置信度候选召回，不能直接成为规范差异结论。
- 每次改动后必须用真实 OIF 输入生成完整 HTML，检查全部 Table、Figure、prose 联系表，再运行完整测试；单元测试通过不能代替报告视觉验收。

## 不要再踩的坑

- 不要把 Figure OCR 标签墙当作正文差异，也不要在 Figure 原图上绘制大面积黄色覆盖。
- 不要让 Table 同时出现在 Table 卡、正文截图和 Figure 截图中；区域所有权必须互斥。
- 不要用单个右侧数字推断整列页边行号；只有密集且跨越足够页高的坐标簇才能收窄正文边界。
- 不要只检查截图是否生成；必须确认左右未裁切、上下没有半行、公式没有混入 Figure、表题没有以残片出现。
- 不要把静态计数写成视觉结论。最终报告需要实际查看联系表，并由独立 reviewer 对同一提交和同一输出目录给出结论。
