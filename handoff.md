# PDF Protocol Diff Handoff

## 当前任务：长文档候选优化（2026-09-18）

- 本轮所有代码只在候选分支 `codex/pdf-long-doc-optimization-20260918`、worktree `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff_longdoc` 上修改；canonical `project/pdf-protocol-diff`（`6abf4f7fd80af1415404579517b264bd448e3a05`）未写入、未覆盖。因旧任务的持久 WIP/legacy 协调保护仍不能建立正常 claim，本轮按用户授权保留为候选分支，不宣称已合入生产。
- 报告目录继续按 `旧版文件名_vs_新版文件名_时间` 命名；本轮补齐目录内的主要 HTML、Markdown 和 TXT 文件名，分别为 `旧版文件名_vs_新版文件名.html/.md/.txt`，机器审计 JSON/CSV 文件名保持兼容。独立冻结应用已从该 worktree 构建并启动：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff_longdoc/dist/ProtocolPdfDiff.app`，窗口标题为 `Protocol Comparison Tool`。
- 比对层加入完全相同正文/章节的快速证明、全章节已一一对应时跳过无效 rescue 扫描；长句候选只在 quick-ratio 上界已证明达不到原有 `0.45` 配对门槛时提前丢弃，其余候选仍使用原精确分数和排序；纯函数缓存仅覆盖同一比较会话。章节阈值和用户可见高级设置均未开放或改写。
- 抽取层每页完成后释放 pdfplumber 页面缓存；来源字体证据在首次比较后传给表格候选投影，避免长文档重复扫描；表格投影正文未变化时复用已完成的章节结果。所有正文、表格、坐标和审计字段在释放前已复制。
- 内置小 PDF 真实桌面 API A/B：候选与基线均为正文 7、修改 4、增加 2、删除 1、表格 0、视觉 0、可靠；`changes.csv`、`table_changes.csv`、`physical_table_records.csv`、两个相似度 CSV 均字节一致，JSON 仅临时输入路径不同。候选报告：`/private/tmp/pdf-longopt-small-current-final/out/old_protocol_demo_vs_new_protocol_demo_20260918_021408_d9t88okl/protocol_diff_report.html`；基线报告在相邻 `pdf-longopt-small-baseline-final` 目录。
- 真实 OIF 1–80 页同页窗 A/B：候选 `extract 87.16 s / compare 129.74 s / RSS 968 MB`，基线 `extract 76.96 s / compare 137.95 s / RSS 1.15 GB`；双方章节 `45/45`、变更 `9`、表格视觉 `80`、告警 `162`、状态 `degraded`。抽取时间受冷缓存和机器状态影响，不能把总耗时差异视为稳定提升；比对阶段约减少 6%，峰值内存约减少 16%。完整 656/685 页历史结果仍是 `degraded / 需人工复核`，本轮不外推整本语义对应准确率。
- 验证：完整 `unittest discover -s tests -q` 为 `1769` 项通过、`1` 项条件跳过；`LONG_PDF_CHECK_OK performance cases=14`、`cancel cases=7`、`oracle cases=17`、`LONG_PDF_REAL_PATH_OK`；本次命名修复后的用户设置/报告/任务事务定向测试 `38` 项通过，`compileall` 和 `git diff --check` 通过。候选提交 `ab5b17d96d009d547941baf24c8e37690af4d7b7` 已推送到 `origin/codex/pdf-long-doc-optimization-20260918`，canonical 分支仍保持 `6abf4f7`。

## 当前任务：用户可见设置收敛、协议文件名报告目录与长 PDF 基线（2026-09-18）

- 用户要求隐藏普通用户无法可靠解释的章节阈值、每章片段数和未变化章节开关，并要求生成报告不能比原版差。候选分支 `codex/pdf-user-facing-settings-20260917` 基于 canonical `6abf4f7fd80af1415404579517b264bd448e3a05`；canonical 分支仍有其他任务的 stale active claim，本候选未写入或覆盖 canonical。
- WebView 与 Tk 界面均改为“报告设置”，只保留输出目录和完成后自动打开；WebView 比较请求固定使用 `min_similarity=0.72`、`max_snippets=20`、`include_unchanged=false`，旧 Tk 程序化字段仅为兼容保留。更换 PDF、模式、页码或输出目录后会使上一次结果失效，避免误打开与新输入不对应的报告。
- 报告目录按导入文件名组合命名并做跨平台清理，例如 `old_protocol_demo_vs_new_protocol_demo_YYYYMMDD_HHMMSS`；目录内既有 `protocol_diff_report.html`、CSV、JSON 等固定文件名保持不变，兼容原有打开入口和脚本。
- 小 PDF 同输入 A/B 已完成：候选与原版使用同一对内置 4/5 页协议和同一默认选项；章节数 `(6, 7)`、变化项 `7`、表格 `0`、视觉 `0`、可信度 `reliable` 一致；`changes.csv` 和 `table_changes.csv` 字节级一致，HTML/Markdown/TXT 与 JSON 去除时间戳和构建标识后完全一致。
- 长 PDF 实测使用 `/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/test-effectiveness/evidence/oif-source/old.pdf`（656 页）和 `new.pdf`（685 页）。仅文本抽取约 `622.41 s`、峰值常驻内存约 `3.22 GB`；完整候选比较与报告生成约 `1590.79 s`、峰值常驻内存约 `3.42 GB`，最终报告约 `244 MB`。结果为 `degraded / 需人工复核`：正文 180、表格 95、视觉 135，视觉哨兵只完成 `135/390` 对，另有 1722 条警告。该结果证明长文档存在明显吞吐、内存和证据覆盖压力；不能据此断言算法已经错误，也不能把整本长文档称为可靠自动结论。
- 长 PDF 性能/取消/独立 oracle 检查和真实路径检查均通过：`LONG_PDF_CHECK_OK performance cases=14`、`cancel cases=7`、`oracle cases=17`、`LONG_PDF_REAL_PATH_OK`。这些门禁证明终止和局部性能保护仍在工作，不外推整本内容对应准确率。
- 本轮用户可见设置测试 20 项、Tk 兼容定向测试 2 项、表格事务 26 项均通过（表格事务使用 `/tmp/pdf-userfix-deps` 提供 PyMuPDF）；候选分支完整回归 `1767` 项通过、`1` 项条件跳过（320.102 秒），最后一轮界面/桥接/事务定向 40 项也通过。A/B 和长文档实测记录在本轮交付说明中。提交前仍需执行最终 diff/编译检查、提交并推送候选分支，随后报告 canonical stale claim 阻塞集成。

## 当前任务：页面组左右截图对称与全部差异聚合（2026-09-17）

- 用户最新反馈同一页组中只看到旧版单页截图，无法完成新旧内容对比。根因是上一轮把去重放在差异卡内部：首个卡片保留图片，后续卡片只保留别名；页面组兜底只补真正缺失的页，不能把每个页组重新组织成对称的左右原页证据。
- canonical `reporting.py` 现由页面组统一持有可见截图：主报告关闭正文/表格卡内的重复截图，按页组把所有实际覆盖的旧版和新版物理页各渲染一次；卡片仍保留完整正文、表格行和隐藏来源锚点。表格单侧缺页继续以状态文字说明，真实无对应页才显示单侧页面证据。
- 真实 1–20 页入口重跑产物：`/Users/mac/Documents/ProtocolPdfDiffReports/final_reader_page_group_symmetric_20260917/protocol_diff_0m5hvzeb/protocol_diff_report.html`。9 个页面组中 8 个双侧匹配组均有旧/新版截图；主区 17 个正文卡和 6 个表格卡均无重复内嵌图；44 张内嵌图片字节级无重复、DOM id 无重复，跳转/省略/截图不可用占位均为 0，表格明细 6 项默认展开。
- 回归：完整 `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q` 为 1764 项通过、1 项条件跳过；截图/页面组/正文/坐标定向套件 68 项通过；`py_compile`、`git diff --check` 和嵌入图片解码核验通过。新增 `READER_PAGE_GROUP_SHARED_SOURCE_TEST` 覆盖多张差异卡共享一对旧/新版截图。
- 缺陷账本新增并验证 `DEF-READER-PAGE-GROUP-SOURCE-SYMMETRY-20260917`。报告整体识别状态仍为 `degraded / 需人工复核`；本次 1–20 页页窗验证不外推整本 PDF 的内容对应准确率。源码已提交并推送：`7077ec4`（远端 `project/pdf-protocol-diff` 与本地一致）。

## 当前任务：正文数值相似度误折叠（2026-09-17）

- 用户指出真实报告把第 17 页正文 `26450 → 26560` 显示为“相似度 1.000”，后续按该值折叠会隐藏协议关键参数。根因是 `SectionChange.similarity` 只用于章节身份配对，长正文单个数值变化得到约 `0.9996`，旧展示层按三位小数判断 `1.000`。
- canonical `reporting.py` 已将完整正文分数与章节配对分数分开：`content_similarity` 使用完整 `Section.comparable_text` 的保守归一和精确序列比值，`pair_similarity`（兼容字段 `similarity`）只说明章节身份。HTML/Markdown/TXT/CSV/JSON 均保留六位小数和明确字段。
- 正文折叠现在必须同时满足双侧章节存在、配对分数精确为 `1.0`、完整正文内容精确相同、无数字/技术标识/语义运算符变化且无未展开差异；表格继续留在原页证据主区。`<=`/`>=` 等关系运算符也纳入关键内容保护。
- 真实页窗入口重跑：旧版 `/Users/mac/Desktop/oif2024.058.13.pdf` 与新版 `/Users/mac/Desktop/oif2024.058.14.pdf` 第 1–23 页，输出 `/Users/mac/Documents/ProtocolPdfDiffReports/numeric_similarity_fix_20260917_release2/protocol_diff_9vrljqpw/protocol_diff_report.html`。共 7 条原始章节变化、6 条主区正文变化、2 条主区表格变化、0 条相似度附录；第 17 页卡为 `pair_similarity=0.999608`、`content_similarity=0.999160`、`critical_content_equal=false`，`26450` 与 `26560` 均保留在主证据中。
- 回归：完整 `.venv/bin/python -m unittest discover -s tests -q` 在最终源码上为 1763 项通过、1 项条件跳过（274.944 秒）；`tests.test_screenshot_first` 定向测试 14 项通过；`.venv/bin/python -m compileall -q src tests docs/verification/numeric_similarity_probe.py docs/verification/numeric_similarity_oracle.py` 与 `git diff --check` 已通过。聚焦的可执行 v2 门禁 `docs/verification/numeric-similarity-fold-evidence.json` 取得 `EXECUTED_EVIDENCE_PASS`，回执为 `docs/verification/numeric-similarity-fold-receipt.json`，仅覆盖本次数值/标识符/关系运算符折叠场景。报告状态仍为 `degraded / 需人工复核`；本次真实页窗验证不外推整本 PDF 的语义对应准确率。
- 缺陷账本新增 `DEF-PROSE-NUMERIC-NEAR-ONE-FOLD-20260917`，关联回归 `PROSE_NUMERIC_NEAR_ONE_FOLD_TEST` 和公开路径重跑 `RUN-PROSE-NUMERIC-NEAR-ONE-FOLD-20260917`。修复提交 `4049d4a25d626bc5bb0eba10c9bb911d9fc65022` 已推送到 `origin/project/pdf-protocol-diff`；本地与远端 OID 一致。

## 当前任务：跨页证据归并、单页截图去重与新版补图（2026-09-17）

- 用户反馈旧版第 7 页在多个证据区重复出现，旧版第 8 页/新版第 10 页页组还出现新版截图缺失。根因是页面组只按章节起始页分组，跨页正文/表格实际覆盖的物理页没有进入归并和全局去重账本。
- canonical `reporting.py` 现在把每张卡的全部旧/新版物理页作为来源元数据；有交集的页组先合并，再按整个报告范围维护可见页集合和兜底页集合。同一侧同一物理页只保留一次可见原图，后续卡只保留隐藏的 `data-source-alias` 锚点，所有表格/正文差异卡和明细仍在同一页组内完整列出。
- 兜底逻辑只为当前组件确实没有可见图片的物理页补图，并跳过报告中已经展示过的页；有真实来源时不再产生“跳转到已展示截图”“本页截图已在其他变化项展示”或“截图暂不可用”占位。删除/单侧证据仍保留其已有一侧原图和文字事实。
- 真实 1–20 页入口重跑产物：`/Users/mac/Documents/ProtocolPdfDiffReports/final_reader_page_dedup_20260917/protocol_diff_2z74_s76/protocol_diff_report.html`；页面组 9，主证据区 32 个可见物理页/侧组合且无重复，44 张内嵌数据图全部唯一且可解码，22 个来源别名目标全部存在，跳转/省略/截图不可用占位均为 0，表格文字明细默认展开 6 项。页组 `旧版第 4、5、6、7、8 页 / 新版第 6、7、8、9、10 页` 同时保留表格与正文 4 张证据卡，旧版第 7 页只出现一次，新版第 10 页可见。
- 定向套件 68 项通过；完整 `.venv/bin/python -m unittest discover -s tests -q` 为 1760 项通过、1 项条件跳过（309.842 秒）；`py_compile`、`git diff --check` 和浏览器实际加载/截图核对通过。结构化核验明细写入同目录 `verification.json`。报告整体识别状态仍为 `degraded / 需人工复核`，本页窗验证不外推整本 PDF 的内容对应准确率。
- 缺陷账本新增并验证 `DEF-READER-PAGE-SCREENSHOT-DEDUP-20260917`；本轮提交已推送到 `origin/project/pdf-protocol-diff`，最终 OID 为 `ebdb326`，本地与远端一致。

## 历史记录：页眉降噪、左右截图和表格内容相似度分离（2026-09-16）

- 用户要求从读者报告移除当前 OIF 版本页眉这类非重点重复内容；含 `ALPHA/BETA` 等不透明标识的动态页眉仍保留，避免把潜在技术标识误删。原始 JSON/CSV 继续保存页眉审计记录。
- `reporting.py` 已在读者投影过滤明确出版物型运行页眉；每个表格/正文证据项都直接嵌入对应的原始裁剪截图，来源别名只保留给焦点定位使用，不再渲染跳转或“已在其他变化项展示”占位。页面组仍按旧版/新版物理页顺序合并，同一组只出现一次，表格和正文明细继续在截图下列出。
- `TableChange.similarity` 现在是完整行内容相似度，新增 `pairing_similarity` 保留表题/行身份配对分数；JSON/CSV 同时输出 `content_similarity` 与 `pair_similarity`，HTML/Markdown 明确标注二者用途。截图中的 `T_J4.3u03 → T_JH4.3u`、`T_JRMS03 → T_JHRMS` 当前显示内容相似度约 0.996、配对相似度 1.000，表格不会进入相似度折叠附录。
- 回归：完整 `unittest discover -s tests -q` 为 1758 项通过、1 项条件跳过；编译和 `git diff --check` 通过。真实 1–20 页入口重跑产物为 `/Users/mac/Documents/ProtocolPdfDiffReports/final_reader_direct_screenshots_20260916/protocol_diff_5gzmg6cy/protocol_diff_report.html`：读者页眉/页脚卡 0，原始页眉审计记录 1，页面组 13，省略/跳转占位 0，表格明细默认展开 6，嵌入图 68 张且全部可解码。
- 本轮源码、测试和缺陷账本已提交并推送到 `project/pdf-protocol-diff`，行为提交 OID `81de3386acdeeaced107a1a923954c84739fec22`，本地与远端一致。真实报告仍为 `degraded / 需人工复核`，1–20 页验证不外推整本 PDF 的内容对应准确率。

## 历史记录：同页证据归并与表格主区展示（2026-09-16）

- 用户要求同一旧/新版物理页只出现一个对比区；若该页同时有表格和正文变化，应在同一页证据区的截图下集中列出，而不是拆成多个页面卡片。
- canonical `reporting.py` 已将表格卡和正文卡按旧版/新版物理页组成连通的 `page-evidence-group`。同一页对只生成一个页面组，页面组内按表格优先、正文随后排列；同页的单侧证据也会并入该组。页级来源别名继续保证每侧原页截图最多嵌入一次，后续卡只显示可回到截图的链接。
- 所有 `TableChange`（包括配对相似度为 `1.000` 且行列边界需要人工复核的表格）均留在页面主证据区；只有正文 `SectionChange` 仍使用相似度 `1.000` 折叠附录。表格文字明细保持默认展开，正文文字明细保持默认折叠。
- 真实页窗验证：旧版 `oif2024.058.11.pdf` 第 19 页、 新版 `oif2024.058.13.pdf` 第 18 页，报告 `/Users/mac/Documents/ProtocolPdfDiffReports/page_group_fix_20260916_release_candidate2/protocol_diff_a8zzcomh/protocol_diff_report.html`。页面组 `page-evidence-19-18` 将两张表和正文证据归于同一组；2 个图片源无重复，所有别名目标均解析到带 `data-source-view` 的原图节点。页脚版权/草稿声明、范围占位和 OCR 重复科学计数法已从读者 HTML 过滤，原始 JSON/CSV 保留审计值。
- 新增回归覆盖同页两张表、页面组唯一性、单侧证据合并、来源别名焦点、页脚元信息、范围占位和重复数字符号；定向套件 91 项通过，完整 `unittest discover -s tests -q` 为 1754 项通过、1 项条件跳过。`compileall` 与 `git diff --check` 也已通过。
- 当前工作树尚未提交；下一步是提交并推送 `project/pdf-protocol-diff`，记录最终 OID。上述真实页窗验证不外推整本 PDF 的语义对应准确率。
- 本轮实现已提交并推送：`project/pdf-protocol-diff`，OID `125210253bd75d3c269624ddfe6827e4207d0b14`；远端同一分支已核对为该 OID。

## 当前任务：表格 1.000 相似度折叠修复（2026-09-15）

- 用户指出表格符号/数值发生变化时仍显示“相似度 1.000”并被折叠。根因是配对相似度只证明同一逻辑表（表题与行身份），主动忽略单元格值；展示层却没有检查行级内容变化就统一移入折叠附录。
- canonical `reporting.py` 已调整：配对分数仍保留为审计字段，所有 `TableChange` 均留在页面主证据区，不再进入 `similarity-review-appendix`；正文 `SectionChange` 的既有 1.000 折叠行为保持不变。
- 回归测试 `TABLE_SIMILARITY_FOLD_TEST` 覆盖截图中的 `T_JH4.3u03 → T_JH4.3u` 与 `T_JRMS03 → T_JHRMS`，确认 `pair_similarity=1.000`、两条“实质/符号变化”位于主 `table_changes`，`similarity_review_table_changes` 为空。单元测试与正式 `write_reports` 生成流程均通过。
- 公开验证产物：`/Users/mac/Documents/ProtocolPdfDiffReports/table_similarity_fold_fix_20260915/protocol_diff_20260915_224706/protocol_diff_report.html`；验证摘要 `verification.json`。该产物是两行表格规则的定向回放，不代表整本 PDF 重跑或全量对应准确率。
- `DEF-TABLE-SIMILARITY-FOLD-20260915` 已在 `docs/verification/escaped-defects.yaml` 记录并标为 verified；更大范围的 `DEF-CONTENT-CORRESPONDENCE-20260913` 仍保持 open。
- 修复提交 `b04ad6a321869d655160fb645a8c77343b030741` 已推送到 `origin/project/pdf-protocol-diff`，本地与远端 OID 一致，工作区干净。

## 当前任务：同页表格/正文截图合并与页序排列（2026-09-15）

- 用户继续反馈同一 PDF 页在表格卡和正文卡中重复出现，并要求同页只展示一次、差异明细仍完整列举且按页数顺序阅读。本轮在已接受的截图去重基础上继续修复，没有覆盖原始报告。
- canonical `reporting.py` 现在把表格和正文证据卡按旧版/新版起始页排序；同页时表格证据先于正文证据。每个旧/新版物理页在表格与正文截图之间共用一个来源节点，后续 occurrence 显示为“本页截图已在其他差异证据展示”的跳转链接；表格行明细继续默认展开，正文文字明细继续可展开。
- 真实页窗验证使用旧版 `oif2024.058.11.pdf` 第 1–20 页、新版 `oif2024.058.13.pdf` 第 1–19 页，最新报告为 `/Users/mac/Documents/ProtocolPdfDiffReports/page_order_fix_final2/protocol_diff_j2294a6p/protocol_diff_report.html`。主差异区按页序为 22 个证据卡；42 张嵌入证据图均为唯一数据源，主表格/正文区每一侧每个物理页最多一张；跨卡来源别名 21 个且目标均存在。全量截图占位的 5 个正文卡改为紧凑跳转提示，避免视觉上形成重复大框。
- 本轮新增同页表格/正文共享截图、同页表格重复截图、页序 tie-break 和全量占位紧凑提示回归；定向套件 249 项通过，编译、`git diff --check` 和真实报告结构/图片解码检查通过。完整 `unittest discover -s tests -q` 为 1746 项，11 个失败均为既有基线（7 个旧标点断言、058 表格编号上下文 1 个、532 样例 3 个），另 1 项条件跳过；没有新增失败。
- 最新实现基线已提交并推送到持久分支 `project/pdf-protocol-diff`，上一远端 OID 为 `9d2184aae8dbfe16644aef75a49bad54eb7fa4ea`；本轮紧凑占位修改待提交。真实报告只写入 Documents 输出目录，旧报告保持不变。

## 当前任务：重复原页截图修复（2026-09-15）

- 用户反馈报告 `/Users/mac/Documents/ProtocolPdfDiffReports/protocol_diff_jqhgimg4_1ccd91325f4645898124463cc1666097/protocol_diff_report.html` 中第 4、5、7、8 页等原页截图重复出现。根因已定位为三条独立渲染路径：同一表格同时进入主表格卡和“表格对应待核实”附录；物理表格行逐行重复嵌入同一张表格截图；跨页正文变化卡各自嵌入相同的中性页截图。
- canonical 源码已修复：表格待核实附录引用已有 `T*`/`A-T*` 证据，不再复制图片；物理行记录保留单元格和审计回执但复用上方表格截图；正文同侧同页的中性截图改为跳转到带标注的唯一截图，保留独立标注截图和文字定位。来源定位脚本会沿 `data-source-alias` 解析，点击文字明细仍可到原页。
- 定向回归：`tests.test_reader_focus`、`tests.test_physical_table_rows_candidate`、`tests.test_complete_report_followup` 共 53 项通过；正文/截图相关套件另有 117 项与 22 项通过；`.venv/bin/python -m compileall -q src tests` 与 `git diff --check` 通过。
- 真实输入按旧版 1–20 页、新版 1–19 页重新生成：`/Users/mac/Documents/ProtocolPdfDiffReports/duplicate_page_fix_final/protocol_diff_dqg5d5kr/protocol_diff_report.html`。HTML 图片由原报告 95 张降至 56 张，重复图片组从 11 组降为 0；正文截图重复的中性页改为 8 个跳转占位，表格物理行不再重复嵌入。第 12/17 页仍各有两张不同变化区域的独立标注截图，这是不同变更证据，不是同一图片复制。
- 本轮验证覆盖上述真实页窗和渲染/导航回归，未宣称任意 PDF 或整本协议的内容对应准确率；旧报告文件未覆盖。代码已提交为 `070a681`，持久分支仍为 `project/pdf-protocol-diff`；推送状态以本轮交付回执为准。

## 当前任务：左右原页截图对称候选回退（2026-09-15）

- 针对旧版第 19 页与新版第 18 页在章节分页变化后整页截图内容不对称的问题，生成了按正文变化段落裁剪的候选预览：`/Users/mac/Documents/ProtocolPdfDiffReports/symmetry_preview_candidate/protocol_diff_5upn940j/protocol_diff_report.html`。
- 用户查看候选 HTML 后认为视觉效果不符合预期，明确要求保持原行为；候选代码与测试改动已全部撤回，canonical 源码恢复到 `feda2f8a83956df4944b17956e6ada4ada19de70` 的已接受行为。`tests.test_screenshot_first` 定向回归 11 项通过。
- 本候选未提交、未合入、未覆盖原报告；后续若再次处理，应先取得新的视觉方案确认。

## 当前任务：原页文字标注与句子标点过滤（2026-09-14）

- 普通英文/中文句子中的逗号、分号、句号、问号、冒号及顿号不再生成正文差异或行内高亮；数值、标识符和紧凑技术表达式中的结构符号仍保留语义保护。
- 正文变化继续沿用原页截图优先展示，旧版淡红、新版淡绿；“展开文字识别明细”保持折叠。新增单页真实入口报告：`/Users/mac/Documents/ProtocolPdfDiffReports/punctuation_repair_page13/protocol_diff_a8of0dt0/protocol_diff_report.html`。
- 定向标点/截图测试 12 项通过；报告、截图和原页视觉套件 169 项通过。正文套件 502 项中仅保留基线已有的 4 个失败（058 编号上下文 1 个、532 表格展示 3 个），未新增失败；完整 PDF 未重跑。
- 代码已提交为 `feda2f8`；交付时保留上述人工复核边界，不把单页验证扩展为整本准确性结论。

## 当前任务：第十五整本复核状态（2026-09-14）

- 第十五轮已用冻结源码 `6b95fb6e61895b6332332d1787204ac4443d6086` 对两份完整 PDF 重跑：旧 636 页、新 685 页，耗时 1986.310 秒。报告已生成，源码与两份输入 SHA 已绑定；HTML SHA `c64658cad6344c7e4f2228ffe0123e830463a46323926d3ec6009aa824f9bb0c`，JSON SHA `f336f35b825cccd447db1f548b6d6b2b41ecb5fa3e46d15fab9824f571d7f151`。
- 当前报告统计仍为正文 162（修改 96、新增 66、删除 0）、表格 23、视觉 104；状态是 `degraded / 需人工复核`。关键第十五定向检查通过：三公式来源保持 UNKNOWN、六条 package 三格记录与两侧原始编码保留、FOM 空格误配不再出现；这只是范围检查，不是整本准确性验收。
- 整本剩余风险仍包括：697/749 条抽取警告、旧/新多页非线性阅读顺序风险、重复章节编号路径 8/9 个、各有 1 个无编号技术段、新版第 681 页整页 OCR 与大面积栅格图；视觉哨兵只核对 104/369 对，265 对未核对，263 页对无法安全配对。因此报告不能自动下“无差异”结论。
- 第十四轮全 501 原 ID 审计的剩余边界仍适用于第十五：正文噪声 52（主文 46、附录 6），另有 17 条混合真实内容带噪声；19 条图形/区域来源未决，26 条数学或结构关系未决。表格仍有 68 条结构/字形未决，包含附录短横编码、私用字形、Cd/PUA、分式和混合单元格；真实参数与条件必须继续保留。
- 尚未进入 canonical 的候选：C80 `log10 (2 f / fb)` 与 `log10(2f/fb)` 的 typed 商空格投影；C127 的唯一 lowered `R_LM` 来源承接；QPRBS 8 张表 16 对 Index 字段的有界 source-codec 呈现。它们已有外部正/负证据或原型，但还没有完成生产 hook、独立复核和整本重跑，不能写成已修复。
- 当前整本报告路径：`/Users/mac/Documents/ProtocolPdfDiffReports/repair_20260913/full-native-fifteenth/reports/protocol_diff_whm_ej91_d8334d4ca6264bebbbefdbdb1b9f1277/protocol_diff_report.html`。完整 prose/table/visual 501 项第十五逐项审计和最终原生页面检查仍待继续；DEF-CONTENT-CORRESPONDENCE-20260913 仍 OPEN，STRICT 门禁不可宣称 PASS。
- 本轮针对用户指出的图内重复文字层及页边家具误差已完成修复（代码提交 `747d68c`、`426e3cc`、`c5d156a`）；按旧209页↔新213页一页窗口重新生成报告，页眉版本号、右侧行号和页脚均不再进入比较文本，真实 PDF 单页回归通过，结果为 0 条内容差异。整本报告未重跑。
- 报告展示层继续收敛：表格截图若只提供网格横/竖线诊断则不再显示该块；表格文字明细默认展开。相关报告渲染/表格回归通过，未改变内部网格证据或表格识别算法。

## 当前任务：整份报告内容对应修复（2026-09-13）

- task_id: pdf-content-correspondence-20260913；owner: 01a09983-9268-7c20-94c1-83e2272ddff7；持久分支project/pdf-protocol-diff；status: active；root为唯一canonical写入者。
- 第十四完整原生GUI已完成：冻结43c8cb0d53843586f9abd3735c0d1f10298d1039，636/685页，2349.933秒；full-native-fourteenth/native-result.json绑定源码/输入。报告JSON SHA ad55361b7152fe7b1e78b254db00e01dfeb1448df588c5749f887c5a0f7df55f，HTML SHA 09049c48ec7335fbc2d5fd24b030a36068dbb5e217b740a371d88de001d33af6。冻结前完整1717测试266.313秒通过（1条件skip），20合同/10定向反例通过。
- 全501原ID、189当前正文卡（169主卡=162候选+7复核，20附录）及44表卡已完整审计，complete-fourteenth-audit.json。剩52原正文噪声（46主卡+6附录），另17混合真实内容带噪声、19几何/26数学未决；未作整本准确性验收。1963完整section仅旧S0872去除两条已承接gamma/Zc副本，其他body逐字相同；C102附录乘号空格假替换消失。
- 三个公式来源UNKNOWN（旧241/257/314↔新245/261/318）各保留双侧原页、全文与相邻标量；六PNG实际重渲染一致，120源glyph逐字/位置唯一匹配。不能宣称公式相等或整个C37/C42/C54修完；原PageText未导出，报告独立offset重证仍有边界。完整表字段/632来源矩阵/配对/质量/三CSV/104视觉payload与已审13相同，限定复用源判断。四条三格记录及154个源字符另行原PDF核对，tau/Cd仍未决。
- 17+28+11+9内容保全检查及82原视觉清单检查通过。原生HTML旧类1198图无损坏、默认折叠/统一放大通过，但520px窄屏因新增公式裸img/pre溢出失败；两次原native失败记录保留。外部formulaUI候选dce4dd8以现有grid/sourcepage修复、全14HTML重放1520img/六formula图/展开proof无溢出，独立390px全部展开复验通过，root已目视桌面/窄窗/proof；已合入，待最终整本。
- 当前未提交：独立通过的numeric074149b已加入，仅完整Setting数值且明确物理Units才允许U2010短横编码等价，原值不改；79b43e因版本/编码ID反例被拒。summary-cache1538f7f已加入，仅完整实参的会话内纯摘要缓存2048，返回列表隔离；128容量140键二遍0命中的证据保留，未宣称整本性能收益。18项新增局部测试通过；UI已合入，最终完整1735测试260.043秒通过（1条件skip），20合同/10定向及三组真实3页公开窗口通过。尚待下一冻结整本，不能据缓存微基准宣称整本提速。
- H50“2 6 Term”图内端口被误标题的628985候选已拒：框内小字会吞合法2.6/2.7章节关联。真实两侧各510glyph的位置、字体和矢量表示不能严格相等；不按字袋/任意距离阈值判等。该问题及其他旋转/二维关系仍open，见review/figure-region-source-candidate/rejected-boundary.md及exact-scene-boundary.md。
- DEF-CONTENT-CORRESPONDENCE-20260913仍open。正式STRICT实际运行因该open缺陷BLOCKED（strict-fourteenth.log），不是PASS。未push/main/最终attest；不能把当前候选当最终交付。恢复状态repair_20260913/continuation-state.json。


## 当前任务：坐标轴伪表格（2026-09-10）

- task_id: `pdf-axis-fragments-20260910`；owner: `01a086af-c710-7b50-99f2-8fe0f3110b34`；持久分支 `project/pdf-protocol-diff`；基线 `b7b75780c1932daf2b8ad04c3147d920a1035bc4`。
- status: ready；recorded_commit: `3874077964540f82ea94582d3cb6c790f3711971`。最终行为后完整1336项测试、206.937秒、1条件skip，无失败（`full-final.log`）。修复大型曲线图横轴字体碎片被当作续表；允许对应未闭合括号的闭括号碎片跨格，保留独立字段、符号及异常坐标候选。独立审查发现的跨栏图题、图后已有轴名/短正文、非有限bbox、符号字段误删均已闭环。
- 证据根 `/Users/mac/Documents/ProtocolPdfDiffReports/axis_fragments_20260910`；`strict-release.json`执行11次、4组RED/GREEN负控，八行通过，仅`verified_scope_only`；`release-original/original-public.json`绑定两本真实PDF及最终报告SHA。真实291–293↔295–297、348–350↔352–354页窗均无表格变化；291/292/349的坐标轴伪表消失，Table13-8的10行和Figure图题/曲线/双轴保留。
- `native-release/layout.json`：4图无损坏，默认折叠关闭，实际展开Figure区，点击原图放大同源，520px窄窗无溢出；root与独立审查目检完整曲线图。`native-core.log`通过真实GUI核心流程；入口smoke通过。整本636/685页时间/内存未复测；页窗其他未决/页眉项目不属于本轮轴碎片准确性结论。旧静态HTML不会自动更新，需重启工具后重比。
- 恢复/验收说明：`docs/verification/plot-axis-20260910.md`。最终full-final.log、官方exact-OID review及main/远端交付状态以最终记录和delivery receipt为准。

## 当前任务：原页截图优先（2026-09-10）

- task_id: `pdf-screenshot-first-20260910`；owner: `01a086af-c710-7b50-99f2-8fe0f3110b34`；持久分支 `project/pdf-protocol-diff`；基线 `219a794f3c725e80c2d3f2c4d7126e735a670d7f`。
- status: ready；recorded_commit: `023dd0446cf989a995f18569f842f5bc38141bb3`。原页优先、折叠文字、显示1.000末尾附录、完整表格上下文已完成范围验证。最后行为变更后完整1327项、207.741秒、1条件skip，无失败；`full-complete.log`。STRICT `strict-complete.json` 15次运行、6组负控、八行通过，仅 `verified_scope_only`。两位独立审查发现的近似句/孤立词误着色和附录PUA退化均按真实PDF或三格式公开报告路径闭环；最终exact-OID attestation及main/远端状态以正式delivery receipt为准。
- 原生GUI核心流程与入口smoke通过；原报告三个反馈位置的最终预览为 `/Users/mac/Documents/ProtocolPdfDiffReports/screenshot_first_20260910/original-examples/protocol_diff_20260910_012233/protocol_diff_report.html`，SHA `0227889efa6043e2d3d5cffe9db35de1b97708d08dcd0762ff8352403c996111`。`native-complete/layout.json`验证7源图、默认关闭4折叠区、实际点击放大及520px窄窗；最终PNG目视完整647页上下文与494页图。预览仅重放原始三个位置事实，不是整本重比；整本636/685页耗时/峰值资源未复测。重启源工具后新生成报告应用本轮展示，旧报告不覆盖。
- 验收和恢复入口：`docs/verification/screenshot-first-20260910.md`；证据根 `/Users/mac/Documents/ProtocolPdfDiffReports/screenshot_first_20260910`。原始用户报告不覆盖。

## 当前任务：只报告实质内容变化（2026-09-09）

- task_id: `pdf-content-only-20260909`；owner: `01a086af-c710-7b50-99f2-8fe0f3110b34`；持久分支 `project/pdf-protocol-diff`；基线 `8becb689f32edaa30d9a7c6f37afdf6bbca32f3d`。
- 已完成：取得正式写入权；保存旧取消任务的已提交历史；修复作者分类、编号目录边界、普通大小写/单元格折行、纯归属提示；全文邮箱不参与差异，混合句保留实际参数变化；CSV 与读者内容清单统一，JSON 新增内容清单并保留原始取证。
- status: ready；recorded_commit: `fa2eba88011dda57d27d430f8681a16edb11ce97`。最终行为源码完整 1317 项（1 skip）和 27 场景通过；STRICT `strict-executed-final.json` 为 EXECUTED_EVIDENCE_PASS / verified_scope_only。缺陷账本已由同一公开报告路径复验为 verified。
- 验证：原生 GUI 桥接生成内容报告、真实 OIF 前置页作者/邮箱/目录/版权清单归零；Table 1-9 纯折行不报表格差异。两位 reviewer 已关闭重要发现，最终文档提交后的 exact-OID attest、main/GitHub 引用与官方交付门禁以证据根 `delivery-receipt.json` 为准。窄页窗仍可能保留行号/来源不足复核；未认证整本 OIF、907 页输入或任意 PDF 语义一致。详情 `docs/verification/content-only-20260909.md`。
- 不要再踩：不能按相似度 1/字符集合判等；不能删除全文含邮箱的整句；不能把数字/单位/条件归属移动当排版；不能恢复未验收的旧报告视图；旧分支已归档，精确恢复路径和证据见 `docs/verification/content-only-20260909.md`。

## 2026-09-08 报告读者负担优化

- task_id: `pdf-diff-reader-focus-20260908`；owner: `01a07b86-21ae-7fa1-af49-6ecf75c4a49b`；status: ready。
- recorded_commit: `949ce785ec3ababa2f03612ea151a8a8deb1de2a`（最后行为变更提交，本handoff终态提交之前）；canonical与持久分支仍为本项目 / `project/pdf-protocol-diff`，目标main。最终双独立attestation及main/GitHub精确OID以交付根 `delivery-receipt.json` 和协调门禁为准。
- 用户目标：保留旧新源图，先给出具体文字/表格行变化及完整条件，按可靠源词定位，超出首屏的全部已有正文/表格条目仍可展开。报告包含检测变化、待核实项及补充原图；不能把出现在报告里等同于确定技术变更。
- 实现：事实先于截图；数值按带符号小数/指数的原文整体呈现；混合私用字体字符单独中性标注，真实数值变化仍保留；来源按唯一物理词跨度定位，排除词和块间留断点；不按固定词数或句号剪去条件。恢复全部已有audit occurrence；表格其余行及所有像素区域可展开；像素框仅作定位；Figure补充证据移至末尾折叠。没有OIF特例，不改变比较结论和差异掩膜。
- 交付根 `/Users/mac/Documents/ProtocolPdfDiffReports/reader_focus_20260908`。用户最终HTML为 `report/protocol_diff_report.html`，SHA `4a5c398895b09e179bc5764a29e51acb274e6536ffca0905461a08aa7e661995`；JSON SHA `3ae62ebc9a622970d74d4c3c54af45b784da934531ef25e6f52a43e0df311215`。原生源GUI整本OIF656/685页完成1065.038秒（17分45秒），始末源SHA一致；同期有测试与打包，不能将其与之前1008.138秒当严格性能基准。
- 最后发现窄窗长目录引导点标题撑宽至671px：独立验证为旧版同样存在的问题。仅加标题自动换行后严格520/520，无截字和隐藏overflow。整本比较不重复提取：原始full-gui-final报告原样保留，最终report仅替换与当前生成器完全相同的一条CSS；`report-style-refresh.json`证明其余全部源码和五个审计文件不变，不冒充第二次完整GUI比较。当前源码另走原生真实PDF路径与全部测试。
- 实际最终HTML原生验证 `oif-native-final/layout.json` PASS：1297图全部加载，无坏图/放大/宽窄溢出；正文与像素两类定位实点正确，完整其余条目可展开，HCB原图与覆盖清单可见。独立旧新审计11组守恒；9017按钮/17958侧目标无错页/侧/跨卡/缺图，7个边缘仅≤0.48源像素的round裁剪偏差，保留初轮检查器FAIL及0.51px负控。上述静态结果针对原HTML；最终仅CSS变化，定位数据逐字节不变。
- 视觉仍有431条未核对记录（225配对页保守跳过，206单侧页），不认证全部页面一致。MQ82原“短段也必须双侧截图”强要求首次FAIL保留：两段330/341字符未达原有500字符截图门槛。本轮条件合同仅认证完整事实、缺图明确未知且不猜位置；不声称MQ82通过正向定位。完整条件/符号/单位与出现次数均保留。精确取整、缺图降级均不是准确率保证。
- 最后行为变更后完整1307项、153.750秒、1条件skip；日志 `work/reader-focus/full-suite-final.log`。STRICT v2正式执行25run、8对RED/GREEN、八行PASS：`test-effectiveness/final-executed-receipt.json`，SHA `d258420a6acf48dde1c96c88fbc092a04a81bf8164a7b8690571cab163978111`，仅 `verified_scope_only`。authority ledger `DEF-READER-FOCUS` 已通过同一公开报告路径验证；scoped ledger由完整账本精确投影且实际比对。
- 新本机app `desktop/ProtocolPdfDiff.app`，当前CSS源码权威打包与冻结WK启动PASS，始末SHA一致，已确认app自包含并清理本轮重复onedir/build-work。整本OIF由源GUI执行，非冻结binary；Windows冻结版及907页原文件未测。此任务不新增算法性能或任意PDF准确率保证。
- 详细验收见 `docs/verification/reader-focus-20260908.md`。恢复先核对最终delivery receipt、claim和refs，保留失败历史与原始用户报告；不把旧prep、首次强要求FAIL或源GUI产物说成其他阶段的成功。

## 2026-09-08 正式路径修复与稳定性验收

- task_id: `pdf-diff-stable-closeout-20260908`；owner: `01a07b86-21ae-7fa1-af49-6ecf75c4a49b`；status: ready。
- recorded_commit: `7f6f48f1c84472899b599101d2492ce9f2cf1e20`（代码提交在本handoff最终提交之前）。canonical根为本项目，持久分支为 `project/pdf-protocol-diff`，目标main。最终精确提交、双独立attestation和main/GitHub一致性由 `/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/delivery-receipt.json` 及协调门禁记录给出，不用先前候选OID代替。
- 已在默认生产路径实现：CID/GID实际空轮廓证据及词界、字体/位置绑定章节、图表/公式/正文共用区域归属、每次出现的共文身份、证据不足的中性待核实结果、完整目录配对、来源页码词与页脚版本次数保留、原生尺寸截图、逐页视觉未核对清单。没有OIF文件名、页码、机构名或原句特例。
- 最终实际OIF完整GUI证据 `/Users/mac/Documents/ProtocolPdfDiffReports/stable_accepted_20260908/evidence.json`：旧656页/新685页，1008.138秒（16分48秒），运行始末源码SHA一致，完整六份报告。此前同机1703.406秒（28分23秒），耗时减少40.8%；是同输入端到端对照，包含准确性修复导致的工作量变化，不是相同输出微基准。907页原始文件未取得。
- 最终HTML为 `stable_accepted_20260908/reports/protocol_diff_20260908_112205_924cdc23699f4f92bbd77da2e8a9a033/protocol_diff_report.html`（相对 `/Users/mac/Documents/ProtocolPdfDiffReports`），SHA `259e70afca5ff086dc5b72d0803ff288570aabb4f02bb35eeea9b8024c21e9c3`；JSON SHA `77fe792da2502d4f7d615d6b5136a262439e4bbdbee9afbecaaaaf9a16c83d7b`。三个原共文anchor无误增删，目录唯一双侧modified，p550无条带且表格完整，p561无误表/HCB原图完整。独立实际产物审查存 `stable_delivery_20260908/reviews/accepted-artifact-review.md`。
- 真实WK报告检查1297张图片全部加载、无损坏/实际CSS放大/横向溢出；HCB和覆盖清单另有原生截图。首次严格native探针发现一张mask的DOMRect多1/64 CSS像素、computedStyle等于自然尺寸，按WebKit原始精度并以独立CSS边界约束复核；自检拒绝2/64及CSS真实放大。第一次BLOCKED回执和精确原材料保存在v2目录的 `attempt-1-native-quantization`，未覆盖失败历史。
- 视觉覆盖实际为128/353对，185对文本块数不同、40对块内容不同而保守跳过，另206个单侧页未安全配对，运行异常0。431条完整页码/原因均在JSON和HTML清单中。p382双方均明确unpaired/NOT_RUN，无正文误标；不能称其像素核验通过。整份OIF仍是需人工复核、不能自动判一致。
- 最后行为变更后的完整回归1297项157.824秒（1条件skip），默认桌面smoke通过。STRICT v2正式执行41run、11对RED/GREEN、八行均PASS，`EXECUTED_EVIDENCE_PASS / verified_scope_only`。清单与回执在 `/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/test-effectiveness/`；`final-executed-receipt.json` SHA `78958ed764b544e6e372779c3a0528cb70210fe784509506ba1e0f385d7f24ea`。四个authoritative OIF缺陷已按真实公开路径验证更新为verified；这不认证任意PDF的整体准确率。
- 独立家族：HN73首次FAIL保留，修复后原gold回归PASS；CP46首次独立留出PASS并在当前源重跑，保留重复2→3和+1.50→-1.50mV及行归属。DPOJET p88限定条件通过；AMSER复杂数学/阅读顺序未认证。11机制还覆盖页脚末数字、版本次数、双栏来源、完整目录及058 p18同一词跨度多证明路径，防只针对OIF截图修补。
- 终止/重启正式WK证据 `/tmp/pdf-diff-wk-cancel-lDdSpn`：读取/视觉阶段后台取消约0.03–0.11秒，界面恢复约0.17–0.30秒，同窗口重启成功，历史输出不变；相关生产取消源码未变。两处测试回调兼容cleanup_error重试，未放松产品清理策略。Windows冻结应用整本OCR取消未实测。
- 新版本机app：`/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/desktop/ProtocolPdfDiff.app`，独立输出未覆盖原安装，权威打包器及真实冻结WK启动检查PASS，binary SHA `e62991a375b309df784d1650ac9a5bd56cd259147205a4a6a77769fb2f547f3d`；source before/after一致，详见同目录build-evidence.json。完整OIF使用源GUI执行，非冻结binary；额外CUA文件选择操作因Mac锁屏NOT_RUN，未尝试解锁或替代控制。已清理本次重复onedir和build-work，保留自包含app、spec和日志。
- 远端7f6f48检查 https://github.com/ZhenlongYou/pdf-protocol-diff/actions/runs/34182496589 ：macOS/Windows测试、构建及适用原生renderer检查成功，总状态失败仅因GitHub Artifact storage quota导致安装包未上传。未删除他人制品；不得称CI全绿或Windows安装包已发布。
- 详细验收及来源链接见 `docs/verification/stable-closeout-20260908.md`；恢复工作先核对最终交付回执、协调claim、canonical main与持久分支，保留现有用户报告及独立失败历史。复杂扫描/数学、多栏等未认证类别继续如实降级，不能靠强配、隐藏或全局删符号获得表面通过。


## 2026-09-08 通用引擎第一阶段候选保存

- task_id: `pdf-diff-general-engine-20260907`
- owner: `01a07b86-21ae-7fa1-af49-6ecf75c4a49b`
- status: candidate_saved_not_production_accepted
- 起点：`8fc4323eec5e7db8b7f258e74d5ab115dc8ecfd5`，持久分支 `project/pdf-protocol-diff`；生产 main 仍为 `44b875b90e7168a5134813622c311355cfe9588a`。最终保存 OID 以 Git/本轮回复为准，不能把这里的起点当最终源码。
- 用户已授权按普适性分析实施。本轮完成可核验的引擎对照、来源匹配候选及局部真实反例验证；整体任务仍未完成，未替换桌面默认路径、未认证全量 OIF 报告，开放用户缺陷账本保持原状态。
- 新工具：`tools/evaluate_parser_candidates.py` 独立进程评测原生/PyMuPDF/PyMuPDF4LLM/Docling/OpenDataLoader；冻结 manifest、runner、PDF 快照与源码哈希，新 worker 绑定完整 input SHA-256。`tools/build_parser_evaluation_fixtures.py` 生成五个可控输入，包括外观相同但内部写入顺序不同的双栏变体。
- 新接口：`src/protocol_pdf_diff/evidence_alignment.py` 保留每次出现的身份、页、坐标、类型和风险，精确匹配支持分段变化、全局唯一与双锚点局部唯一；所有最终配对统一核验顺序交叉，不能把条件互换判作全体不变。对侧仍有未解释内容时不授权缺失；保留原文符号及词界，坐标与整页文字不一致时保存双视图且不重复计数。
- `tools/compare_document_evidence.py` 是真实 PDF 的原生候选 CLI，输出来源 JSON 和文字 HTML；`tools/compare_parser_evidence.py` 直接接入冻结解析区域，输出来源与配对 JSON。两者都不是正式桌面报告。历史 Docling 仅支持核验其原生 SHA-256 低64位来源，明确披露截断绑定且complete=False；缺少可验证来源的其他旧结果拒绝读取。不得补写新字段后声称旧解析时已经记录完整输入输出关系。
- 评测依赖隔离于 `work/parser-evaluation-env`（约1.6GiB），生产 `.venv` 未安装重型候选。实际版本：Docling 2.126.0、docling-core 2.95.0、PyMuPDF4LLM 1.28.2、OpenDataLoader 2.5.7。Java 使用已有 PyCharm jbr，通过 JAVA_HOME 指定；未新装系统Java。Docling模型缓存是本轮下载，保留以供后续复测。
- 八案例32次同机运行：原生4/8、PyMuPDF4LLM6/8、Docling7/8、OpenDataLoader4/8通过所选检查；这不是准确率，包含开发样本和已知反例，尚无独立文档家族验收。Docling表格行列警告仍存在。手册人工参考为DPOJET物理88页Table29，论文为amser物理1、5页；OIF不能作为唯一泛化证据。
- 两份整本候选管线耗时：PyMuPDF4LLM旧656页95.468秒、新685页102.245秒；Docling旧451.002秒、新437.862秒。是新进程导入/初始化/转换/保存/规范化/检查时间，模型已下载，不是全比较时间或首次安装时间。长文计量绑定旧冻结runner；最终Docling映射仅重放原始输出，未重跑模型，严格分开记录。worker RSS不含子进程，不冒充总峰值。
- 原始Docling adapter 经独立审查修正跨页多prov整段复制、picture children及页边层遗漏、单prov列表charspan口径不同导致文字清空、表格Markdown格式偏置等。最终投影两份空list_item均0，p1跨页对象只保留其真实字符范围。后续必须继续保留raw，不能用模型表格字符串覆盖原始文字。
- 旧原生checkpoint桥接结果1341/1341整页未决，已拒绝该接入路线。直接从Docling区域接入后，用户截图中物理p351、p352、p381的共同正文均找到双方来源；没有文档名/页码/原句特制规则。最终24,923旧＋13,638新非空区域，19,454来源单元仍未决，含页边行号；匹配0.242秒仅该阶段，不包括解析/JSON/图表报告。不能将这三个原句回归视作整份OIF修复。
- 补充双栏检查：两份PDF在2倍渲染下像素SHA完全相同；整列写入时Docling/ODL通过，交错写入时失败。原生两份通过、PyMuPDF4LLM两份失败。新生成器保留两变体；原始32次记录不回填、不改称新结果。
- 最后匹配/接入行为变更后的完整套件：1278项、159.859秒、1 skip；18项候选定向检查通过。公开PDF CLI检查保留+3.0/-3.0变化及来源ID；主桌面入口 `main.py --gui-smoke-test` 通过。六固定回归合同的v2 `occurrence-receipt-accepted-candidate.json` 为 EXECUTED_EVIDENCE_PASS / verified_scope_only，包含三个实际故障注入、独立手写预期、API与CLI入口；不认证解析器或整体PDF准确性。
- 独立只读审查：`review_occurrence_candidate` 已逐项关闭已报候选反例（重复消费守恒、移动修改误删、条件互换、词界丢失、逆序锚点与来源绑定）；`review_parser_benchmark` 关闭跨页复制和列表丢失；`review_engine_architecture` 核对评测记录与结论边界。真实桌面OIF新报告和最终浏览器版面未验收；不得称生产修复完成。
- 证据全部在 `work/general-evaluation/`：`reviewed/`、`long/`、`source-bound-controlled/`、`stream-order-check/`、`docling-occurrences-final/`、最终完整测试日志及回执；私有PDF/raw不进Git。详细方法和入口见 `docs/verification/general-engine-evaluation-20260907.md`。旧用户报告及原生窗口未改动。
- 后续恢复：从本次终态claim记录的持久分支精确OID继续同项目任务。首先建立原生字词与候选区域的一一归属及覆盖核验，按证据选择原生/复杂版面路线；再补表格行列、重复条件、扫描/混合页及真实独立文档的误报/漏报/未决率。结果展示必须继承区域归属，保持真实尺寸和完整上下文，复用现有取消与原子发布。通过这些门槛前不接默认路径；不能靠全部降级未决或隐藏难内容达标。


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

- 第五轮源`04e312faf5803c95c99d8186eb1d120440993f36`全套1388项出现2个既有视觉归属契约失败；第五次GUI已主动中止并保留aborted.json。第六轮修正：已为单侧的增删可按物理证书清理；replacement仅在双方实际清理，或清理后同一非空正文时接受，否则两侧原样保留。独立C47与800/900mV反例通过。新增宽截图不等于真实图框的独立负控，19语义案例与10负控预检通过；完整套件运行中，最终GUI与501复核仍待完成。

- C131/C133共同根因已落地：完整物理标题已有strong证据，旧文本却拆5行。仅以唯一连续有序字符跨度绑定既有标题，普通字体/缺字/重复/插正文拒绝；真实新增子节仍added。离线原词框3测试通过；1391项全套重跑中。C47/C59空间标签身份可证，但缺snippet字符到word-id桥；H55物理四cell守恒可证但现模型不保留原cell矩阵，均不以字符串袋或放宽alignment消噪。外部诊断记录后续边界。

- 第六轮（含标题归属）完整套件1391项282.735秒通过、1条件skip；10负控全部检测。源码即将冻结后启动full-native-sixth，尚无该轮整本结果，不能代称通过。

- 2026-09-14：第六次全本 GUI 因独立发现相邻强标题 14.2/14.2.1 被串并而中止，证据在 full-native-sixth/aborted.json。当前尚未重启全本。新增强标题边界、真实叶子阈值、原生字符图归属桥、物理四格表行保全；1428项全套通过（1 skip），但之后的 TXT/MD 导出修正及待合入双边图组归属保护尚未完整重跑。独立复核发现单侧真实数值删除、跨同名 Figure 组借证，正在修复；缺陷仍 open，尚未交付或推送。原始501项必须基于下一份完整报告重新复核。

- 本轮行为冻结前完整套件1442项通过（1条件skip），日志seventh-release-final-tests.log；10负控全部检测，真实C47与H55局部路径复跑通过。独立word-group-final-review、pre-freeze-recheck在各自有界范围重要未决0。准备提交当前候选后启动full-native-seventh；整本与501项结果仍待完成。

- 第七次整本运行4eabe50因发现原方案展示口径未落实而主动中止：普通正文候选仍称核心技术变化。现仅修改HTML/MD/TXT摘要名称与解释，GUI兼容读取旧新标签，原始事实与计数算法不变；完整套件重跑中。下一次full-native-eighth将按同一原PDF公开入口验证并复核501项。

- 第八轮冻结前全套1442项245.925秒通过（1条件skip），10负控全部检测，summary-label-review独立确认展示口径与GUI兼容。即将提交后启动full-native-eighth；此前第七轮已中止，不计作完整验收。
