# Corpus v0 回归门

Corpus v0 用一组逐文档断言保护 PDF 抽取、章节匹配和报告输出。它不是准确率排行榜：每个 case 都必须单独通过自己的状态、变化数量和文字锚点门禁，不能用一批简单文档的平均分掩盖某一种出版物或复杂版式文档的退化。

私有 Corpus 是额外的真实文档回归，不是“普适性”的唯一证据。仓库内的测试会在运行时生成多组 old/new PDF，分别使用数字章节、命名章节、Part 和 Annex 结构，并注入公式、否定词、技术标识符及限值变化；这些测试不可 skip，保证共享核心不依赖某个出版方或本机私有文件。self-diff 只证明同一输入的确定性，不计作变化识别覆盖。

## Extraction-only 解析基准（manifest v1）

`manifest.example.json` 是 old/new 差异回归；
[`parsing_benchmark.example.json`](parsing_benchmark.example.json) 则是独立的单文档解析
基准。后者不写报告、不比较两份 PDF，只用 literal（大小写不敏感）文字 anchor、严格递增的
阅读顺序 anchor、OCR 是否使用和质量状态检查抽取结果。因此它不能以 self-diff 的零变化
替代复杂版面或扫描件的抽取验证。

受控版本在临时目录构造五类固定小型样本（线性原生文字、定位双栏、公式、无框表格、英文
栅格 OCR），无需提交二进制 fixture：

```bash
python3 tools/benchmark_pdf_parsing.py --controlled \
  --output-json /tmp/pdf_parsing_benchmark.json
```

真实文档版本只允许相对 `--corpus-root` 的 `.pdf` 路径，禁止绝对路径、`..`、未知字段和
模糊的 expectation 拼写：

```bash
python3 tools/benchmark_pdf_parsing.py parsing_benchmark.example.json \
  --corpus-root /path/to/private/pdf-corpus \
  --output-json /tmp/pdf_parsing_real_benchmark.json
```

每个 case 需显式 `id`、`required`、`document` 和 `expect`；`description` 与
`ocr_language` 都可选。`expect` 可用互斥的 `state` 或 `states`，并可声明
`must_extract`、`must_not_extract`、`ordered_extract`、`ocr_used`。缺失 optional PDF 是
`skip`；缺失 required PDF、已存在 PDF 的解析异常或任一门失败是 `fail`。输出只含不透明的
`case_index`（即本机 manifest 数组中的 1-based 位置）、状态、有限失败说明、质量状态、页数、
OCR 页、告警数、字符数和耗时；不含 case id、description、anchor、文件名/路径、全文、root
绝对路径、hash 或临时报告路径。`pass` 表示抽取门通过，不提升扫描/复杂布局页面的自动判等资格。

## Gold Accuracy 可量化识别率

`gold_accuracy.example.json` 使用人工核对的变化事件，而不是少量“出现/不出现”锚点。
每个事件声明类型（正文、表格、公式或视觉）、旧/新 literal、可选位置、预期出现
次数、是否属于关键事实，以及它应不应该出现在 HTML/Markdown/TXT 三个读者层。应显示的
事件必须三面都出现，应隐藏的事件必须三面都不出现，任一格式单独泄漏或漏显都会失败。
HTML 以流式可见文字解析，先丢弃图片 data URI、样式、脚本和折叠审计正文，不把 base64
复制进内存。评估器按 JSON 中的内部 `reader_card_id` 把事实绑定到具体 C/T/F/V 卡片，并在
每张卡内核对 occurrence 数量；另一条款出现的相同数值、同一张卡只渲染一次的重复事实，
都不能替缺失事件作证。视觉哨兵的覆盖审计缺失或未完成时，Gold case 默认直接失败，不能用已
命中的文字事件掩盖未执行的像素核对。仅评估正文/表格/公式识别且不声明视觉召回率时，case
可显式设置 `visual_coverage_required: false`；summary 仍同时输出
`visual_coverage_required=false` 和 `visual_coverage_complete=false`，不得把该结果表述为
视觉认证。包含任何 `kind: visual` 事件的 case 禁止使用此豁免。正文的
`location` 是报告条款位置；表格可用 `old/new titles:` 或 `old/new pages:` 片段；公式可用
`old/new page N formula (M)` 片段，以便相同数值或表达式按来源消歧。运行：

```bash
python3 tools/evaluate_diff_accuracy.py corpus/gold_accuracy.example.json \
  --corpus-root /path/to/private/pdf-corpus \
  --output-json /tmp/pdf_diff_gold_accuracy.json
```

输出包含总体 recall、关键事实 recall、视觉 recall、false-negative 数量和逐 case 状态。
只有 `oracle_complete: true` 明确表示该版本对的实际变化已经穷举标注时，才计算
precision；不完整 oracle 的 precision 必须为 `null`，防止把未知变化当作真阴性。
literal 按大小写敏感的技术 token 边界匹配，`10 mV` 不会命中 `110 mV`，`UI` 也不会
命中 `ui`。重复 literal 必须增加上述 `location` 和准确的 `occurrences`，并由同一具体
报告卡逐格式提供足量 occurrence，不能任选别处凑成命中。summary 只保留 case/event 序号与计数，不复制
PDF 路径或技术正文。

Gold manifest 还可在顶层声明 `minimum_distinct_families`，并为每个 case 写入非空
`family`。只有实际执行并产出指标的文档族才计入覆盖数；缺文件的 skip、self-diff 名称数量
或同一家族的多个版本对不能凑足门槛。默认门槛为 `1`，用于普通准确率回归；凡是要宣称
“跨标准/跨文档族通用性”，必须显式设为至少 `2`，并至少包含一组非 OIF 的真实 old/new
人工标注版本对。summary 仅输出 required/executed/complete 数量，不复制 family 名称。
当前示例 manifest 明确是单一 `oif-serdes` 家族，因此只能证明该家族的 Gold 结果，不能单独
认证通用性。

受控模式自建临时 corpus，不能同时传入 `--corpus-root`；传入会在参数校验阶段以退出码 2
拒绝，避免误以为私有文件参与了受控验证。

## 支持范围与状态

当前可靠支持范围是：具有原生可选文字、以线性阅读顺序为主、章节编号稳定的协议或规范 PDF。

- `reliable`：两侧都满足当前支持范围，文字量、章节结构和抽取信号通过门禁；只有这个状态允许把“没有发现差异”作为自动结论。
- `degraded`：仍会输出可定位的正文和表格证据，但存在文字量不足、按页回退、多栏/非线性布局、异常密集的单字/短碎片抽取、抽取警告等风险，必须人工复核原 PDF。
- `indeterminate`：缺少可比较文字或章节；不能从零变化推导出文档相同。

扫描件、图片主导 PDF、复杂多栏论文、表单、幻灯片、CAD 导出、字体映射损坏文件，以及依赖图形/盖章/矢量图语义的审阅都属于降级或不支持范围。这里“unsupported”是能力边界，不是第四个状态：整页 OCR 若成功可提供 `degraded` 的文字差异定位证据，没有可比较文字或章节时是 `indeterminate`；纯视觉、非表格图片变化只由文字一致页的像素哨兵追加人工复核证据，不自动判断图形语义。self-diff 可以守住“同一输入不得产生实质变化”，但不能把本来不可抽取的文档提升成 `reliable`。OCR 和非表格视觉语义比较也不属于 Corpus v0 的可靠判等承诺。

## 私有文件放在哪里

manifest 中只写相对于 corpus root 的 PDF 路径，禁止绝对路径和 `..`。root 按以下优先级解析：

1. 命令行 `--corpus-root`；
2. 环境变量 `PDF_DIFF_CORPUS_ROOT`；
3. manifest 所在目录。

建议把私有 PDF 放在仓库外；也可以临时放在已忽略的 `corpus/local/`。不要提交源 PDF、私有报告或私有哈希。`corpus/manifest.local.json` 也已忽略，可用于本机调整文件名和页窗。

## Manifest v1

最小 pair case：

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "protocol-version-pair",
      "kind": "pair",
      "required": false,
      "certification_required": true,
      "old": {"path": "protocol-old.pdf", "start_page": 20, "end_page": 35},
      "new": {"path": "protocol-new.pdf", "start_page": 22, "end_page": 38},
      "expect": {
        "state": "reliable",
        "max_technical_section_changes": 12,
        "max_technical_table_changes": 4,
        "must_extract_old": ["Receiver bandwidth"],
        "must_extract_new": ["Receiver bandwidth", "46.25 Ω"],
        "must_find": ["Receiver bandwidth", "46.25"],
        "must_ignore": ["D R A F T"],
        "must_ignore_locations": ["31.1. Wrapped prose sentence"]
      }
    }
  ]
}
```

最小 self-diff case：

```json
{
  "id": "native-self-diff",
  "kind": "self_diff",
  "required": false,
  "document": {"path": "native.pdf", "start_page": 1, "end_page": 10},
  "expect": {
    "max_technical_section_changes": 0,
    "max_technical_table_changes": 0
  }
}
```

字段语义：

- `id` 在 manifest 中必须唯一；`kind` 只能是 `pair` 或 `self_diff`。
- `required` 默认为 `false`。缺少任一 PDF 时，optional case 记为 `skip`；required case 记为 `fail`。文件存在但损坏时始终失败。
- `certification_required` 控制 `--require-executed` 的覆盖门禁：pair 默认是 `true`，self-diff 默认是 `false`。认证必跑 case 若因缺文件而 skip，会转为失败；不属于当前本地 corpus 的示例 pair 必须显式设为 `false`。严格认证清单至少要有一个 `certification_required` old/new pair；self-diff 只能守住确定性，不能替代真实版本差异 oracle。
- 启用 `--require-executed` 时，每个 `certification_required` case 的 `expect` 必须同时声明一个 `state`/`states` 质量状态门，以及至少一个独立可观测门：非空 `must_find`/`must_ignore`/`must_extract_old`/`must_extract_new`/`must_ignore_locations` 锚点，或 `max_technical_section_changes`/`max_technical_table_changes` 数量上限。其中认证 old/new pair 还必须有至少一条非空 `must_find` 正向差异 oracle；只有状态和 `max_*` 上限只能证明没有 false-positive 洪泛，不能证明工具真的找到了已知差异。`expect: {}`、只写状态、或 pair 没有正向 oracle 都会在读取 PDF 前作为 manifest 失败。
- `old`、`new` 和 `document` 的 `start_page` / `end_page` 都是可选、从 1 开始且包含端点的页码。pair 的两侧页窗互相独立。
- `state` 对 `reliable`、`degraded`、`indeterminate` 做精确匹配。可选 OCR 引擎会改变扫描件是 `degraded` 还是 `indeterminate` 时，可改用互斥字段 `states: ["degraded", "indeterminate"]`；它仍能明确禁止误升为 `reliable`。
- `max_technical_section_changes` 和 `max_technical_table_changes` 只统计 `role=technical`；被标为 `document_metadata` 的封面、声明和修订历史不会占用技术变化预算。
- 两个数量上限都是可选的 false-positive 门禁，不是变化召回率 oracle。对已明确为 `degraded` 的复杂版本对，应用手工复核后的宽松有限上限阻断突发的 false-positive 洪泛，并用状态与高价值 `must_find` 守住至少一个已知差异；不要设置会迫使实现删除未知内容的低变化上限。self-diff 仍应把两项上限设为 0。
- `must_find` 不区分大小写，只检查 JSON 中已确认的实质正文片段与表格变化字段；复核项、未变化表题/定位、报告头、文件名、provenance，以及仅供审计的完整旧/新章节都不能让正向 oracle 假通过。`must_ignore` 用于防止读者报告出现已知噪声，会组合检查 JSON 的变化事实、HTML/Markdown 变化正文、正文 CSV 与表格 CSV，因此也覆盖复核项。两种搜索面都会先折叠换行、制表符与连续空格，避免 PDF 视觉换行造成假失败；标点和文字仍按 literal 匹配。每条失败都会在对应 case 的 `failures` 数组中单独列出。
- `must_extract_old` 与 `must_extract_new` 分别检查对应版本的完整章节标题和正文，不使用截断预览、变化报告或文件名替抽取成功作证；适合守住公式、数值、单位及被边栏误删的正文。
- `must_ignore_locations` 只检查旧/新两侧的章节位置，可阻止跨行引用、表格行、脚注或整数要求列表被误判成章节，同时不会因普通正文合法引用同一文本而误报。

schema 版本、类型、页码、状态、路径、未知字段或 expectation 配置错误属于 manifest 失败，而不是 skip；例如把 `expect` 拼成 `expects`、把 `start_page` 拼成 `start_pages` 不会被静默忽略。

## 运行

先安装项目依赖，然后运行：

```bash
python3 tools/validate_corpus.py corpus/manifest.example.json \
  --corpus-root /path/to/private/pdf-corpus \
  --require-executed \
  --output-json corpus/local/summary.json
```

也可以只设置环境变量：

```bash
PDF_DIFF_CORPUS_ROOT=/path/to/private/pdf-corpus \
  python3 tools/validate_corpus.py corpus/manifest.example.json
```

stdout 和 `--output-json` 都是同一份机器可读 summary，包含总体 `status`、`pass/fail/skip` 数量和每个 case 的状态。实际执行的 case 还包含指标与具体失败；skip case 包含相对文件名原因，manifest 失败包含 schema/认证 oracle 错误。只要有失败就退出 1。发布/准确度认证应使用 `--require-executed`：严格 manifest 必须有带有效 oracle 的认证 old/new pair，任何 `certification_required` case 未执行也会失败。不带该参数的探索运行仍允许无 oracle 的草稿 case 或全 skip 后退出 0。

每个实际 case 都在 `TemporaryDirectory` 中调用正常的 `run_diff` 和 `write_reports`。锚点检查组合使用 `protocol_diff_data.json` 的变化/表格变化、HTML/Markdown 的变化正文和两个 CSV，临时报告随后删除。summary 会保留 manifest 中的相对文件名用于解释 skip/fail，但不保存 corpus-root 绝对路径、输入 SHA 或临时报告路径。

## Anchor 维护规则

`manifest.example.json` 保留少量高价值的真实版本对锚点，并列出 JLT、form、scan、slides 等不同能力边界的 self-diff。受控公式、否定词、关键数值、标识符、章节层级和表格排列变化由单元测试动态生成，不提交二进制 fixture，也不允许因私有文件缺失而跳过。

真实 version pair 的目标是逐份文档累积 20–30 个可手工核对的 anchor，而不是全 corpus 合计 20–30 个。建议每份 pair 至少覆盖：

- 关键公式、数值、单位和比较符；
- 规范性措辞、否定词和步骤顺序；
- 表格参数、表题、续页和行级值；
- 章节新增、删除、重编号和跨页移动；
- 应被过滤的页眉页脚、水印、行号和版式噪声。

新增 anchor 前，先在旧/新源 PDF 的明确页码上人工确认，再确认它应该出现在差异报告还是必须被忽略。每个 case 单独设门，任何一份文档失败都应阻止抽取启发式变更进入交付；不要设置总体平均通过线。

## 可复现性与 provenance

底层 JSON 报告记录 package version、可选 build commit、支持范围、旧/新输入路径与 SHA-256、选择页窗及有效阈值。Corpus runner 只在临时目录使用这些报告，持久 summary 刻意不保存 corpus-root 绝对路径、输入 SHA 或报告路径；它仍会显示 manifest 自带的相对文件名。需要审计某次失败时，应在受控本机保留相同 manifest、代码 commit、源文档版本和页窗；不要把私有 provenance 复制到 Git。
