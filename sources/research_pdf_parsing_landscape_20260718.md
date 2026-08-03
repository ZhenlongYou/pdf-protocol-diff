# PDF 解析与文档比较开源方案调研（2026-07-18）

## 调研目标

本轮只解决一个问题：怎样增强扫描件、复杂版面和表格的识别，同时不让一个不确定的 OCR 或版面模型把“没有发现差异”误报成“两份文档一致”。检索覆盖 GitHub 开源项目、官方文档和公开基准；实现选择以当前工具的可审计性、离线可用性和桌面打包成本为约束。

## 候选项目与可借鉴点

| 项目 | 可借鉴能力 | 对当前工具的判断 |
|---|---|---|
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | `auto`、`fast`、`hi_res`、`ocr_only` 的质量/成本路由；`auto/fast` 会利用可提取文字，`ocr_only` 可主动 OCR | 直接采用“按页路由”思想，不引入完整依赖栈 |
| [Docling](https://github.com/docling-project/docling) | 多 PDF 后端、可选/强制 OCR、多 OCR 引擎、表格与阅读顺序模型 | 作为下一阶段可选高精度后端候选 |
| [PaddleOCR / PP-StructureV3](https://github.com/PaddlePaddle/PaddleOCR) | 文档方向校正、版面分类、表格/公式和多栏阅读顺序 | 适合中文复杂版面；依赖较重，应在 corpus 上证明收益后再接入 |
| [MinerU](https://github.com/opendatalab/MinerU) | 扫描件、多栏、公式、跨页表格与双解析引擎 | 适合作为离线批处理后端候选，不宜成为默认桌面依赖 |
| [Marker](https://github.com/datalab-to/marker) | 按需 OCR、版面/阅读顺序/表格模型、结构化 JSON/Markdown | 可借鉴结构化中间表示；需单独评估许可证与模型部署成本 |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | skip/redo/force OCR、deskew、rotate、oversample、timeout | 借鉴 OCR 前处理和可控失败语义；不先生成一份不可追溯的新 PDF |
| [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html) | 多栏识别、只在真正需要时 OCR | 可作为轻量 PDF 后端 A/B 候选 |
| [diff-pdf](https://github.com/vslavik/diff-pdf) | 页面渲染后的视觉差异 | 仅适合补充诊断；不能替代当前章节/表格语义比较 |
| [pdfcompare](https://github.com/red6/pdfcompare) | Java 生态中的逐页视觉比较和忽略区域 | 可借鉴“可配置忽略区域”，不作为主解析器 |
| [pandiff](https://github.com/davidar/pandiff) | 基于结构化文档/AST 的语义 diff | 借鉴先转结构化中间表示、再比较的分层架构 |

官方文档证据：

- [Unstructured PDF partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning) 与 [strategy 说明](https://docs.unstructured.io/open-source/concepts/partitioning-strategies)：解析策略由文本可提取性、表格需求和模型可用性共同决定。
- [Docling CLI](https://docling-project.github.io/docling/reference/cli/) 与 [pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/)：OCR、表格、公式和阅读顺序是独立选项，启用高级模型会增加运行成本。
- [Tesseract 识别质量指南](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)：约 300 DPI、纠偏和合适的页面分割模式会显著影响 OCR。
- [pytesseract API](https://github.com/madmaze/pytesseract)：支持 `lang`、`config` 和 `timeout`，可把语言与超时纳入可复现配置。
- [OCRmyPDF 高级选项](https://ocrmypdf.readthedocs.io/en/stable/advanced.html) 与 [API](https://ocrmypdf.readthedocs.io/en/stable/apiref.html)：deskew、rotate、oversample 和 OCR 模式都有准确率/破坏原图之间的权衡。
- [PP-StructureV3](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)：复杂文档需要方向、版面、表格、公式和阅读顺序的联合处理。
- [OmniDocBench（CVPR 2025）](https://openaccess.thecvf.com/content/CVPR2025/html/Ouyang_OmniDocBench_Benchmarking_Diverse_PDF_Document_Parsing_with_Comprehensive_Annotations_CVPR_2025_paper.html)：文档解析应按版式属性和元素类型分别评估，不能只看一个总体平均分。

## 本轮已经落地

1. 只有“原生文字稀少 + 大面积栅格图”页面才触发整页 OCR，健康文字层不会重复 OCR。
2. 扫描页按 300 DPI 渲染，并在渲染前限制像素预算；Tesseract 使用自动页面分割 `--psm 3`，识别阶段单页超时 60 秒。
3. 支持 `eng`、`chi_sim`、`chi_sim+eng` 一类语言配置；语言表达式先校验，并写入报告 provenance。
4. 命令行、PyCharm 参数区、桌面 GUI 使用同一设置，旧/新文档对称执行。
5. OCR 页显式记录为 `ocr_pages`，比较状态至少降为 `degraded`；即使零差异也不允许自动判等。
6. 缺少 Tesseract/语言包、超时、空结果或异常时保留原生证据并发出警告，不伪造成功。
7. 新增 extraction-only benchmark：五个临时受控门分别覆盖线性原生文字、真正重叠**纯正文**双栏的 column-major 顺序、公式、无框表格的参数/数值/单位顺序与英文栅格 OCR 的 `3.3 V`、`25 PS` 顺序；summary 只用不透明 case ordinal，不保存 case id、路径、anchor、正文、私有根路径、hash 或临时路径。
8. 新增不可变 `DocumentBlock` 和五种页面路由（`native_text`、`native_layout_risk`、`ocr_fallback`、`image_text_layer`、`unreadable_image`）。它们只提供坐标/来源审计证据，既不改变现有正文比较，也不代表重型后端已经接入。
9. 轻量双栏重排的边界已明确：只要同一候选双栏有两条或更多跨行“数字 + 已知工程单位”值，保留 pdfplumber 的 y-first 顺序并维持 `layout_risk`。工程单位使用有限的物理/SerDes 符号及全拼词表（例如 `V`、`mV`、`ps`、`UI`、`dB`、`GHz`、`GT/s`），普通英文计数不触发该边界。长标签无框表与含测量值的双栏正文在这一层不可可靠消歧，不能用关键词、句式或标签长度猜测；`DocumentBlock` 几何继续保留，供未来后端 A/B 验证。

## 再次优化方案

### P0a：轻量默认路径集成（本轮已完成）

完成上述 OCR 触发、300 DPI、语言、识别超时、渲染像素预算、质量降级和回归测试。它打通“扫描页不再只能为空”的调用路径，同时保持当前安装和报告模型基本不变。大面积栅格页即使带有较多搜索文字，也会保持降级，避免残缺文字层造成假一致。

当前已通过的门：原生数字版页面不走 OCR；真实生成的栅格 PDF 能进入 OCR 调用边界；重叠图像不重复计数；超大渲染提前停止；OCR/图片主导页永不 `reliable`；异常不吞掉；以及受控五类 extraction-only benchmark。2026-07-18 运行 `tesseract --version` 为 **5.5.2**，`tesseract --list-langs` 可见 `eng` 与 `chi_sim`；`python3 tools/benchmark_pdf_parsing.py --controlled` 实际执行非 mock 英文栅格 OCR，结果为 5 pass / 0 fail / 0 skip，记录在 [`parsing_benchmark_baseline_20260718.json`](parsing_benchmark_baseline_20260718.json)。

仍未验收的门：上述非 mock 运行只证明受控英文 anchor 的端到端路径，并不证明中文、低清/倾斜扫描、公式图片、无框图片表格或任意复杂版面的 OCR 准确率。新增真实 version pair 前，应逐项核对数值、否定词、单位、章节标题的 anchor 召回与误报上限；每一类单独设门，不能用总体平均分替代。

### P0b：长扫描文档运行控制（下一步）

单页 Tesseract 超时不能约束整份超长扫描文档。下一步增加文档级 OCR 时间/页数预算、逐页进度、取消和“跳过剩余扫描页”策略；预算耗尽必须在报告中列出未识别页并保持 `indeterminate` 或 `degraded`，不能把部分结果当作完整比较。

验收门：100、500、1000 页合成扫描文档分别记录总耗时和峰值内存；GUI 可在当前页结束后取消；预算耗尽后未处理页可审计；用户显式页窗仍能覆盖默认预算策略。

### P1：版面后端适配层

已定义内部 `DocumentBlock` 中间表示，包含页码、边界框、类型、阅读顺序、来源引擎、可选置信度和原始文本；pdfplumber 原生行、表格以及 Tesseract 整页 OCR 均可留下来源明确的块。报告 JSON 的新增审计字段只序列化路由、风险事实和 block 数，不复制 block 原文或 bbox。现阶段仍保留 pdfplumber 为默认后端，尚未接入 Docling、PaddleOCR/PP-StructureV3、MinerU 或 Marker；这些块和路由是未来 A/B 对照的接口，不是上述项目的准确率声明。同一文档的两套引擎输出也不得静默合并。

在轻量 pdfplumber 路径中，跨行重复“数字 + 已知工程单位”的双栏页面一律不做 column-major 重排：它可能是参数表，也可能是技术叙述，单靠文字、关键词或短标签形态没有可审计的判别力。该词表只含明确物理/SerDes 单位，不把 `1 item` 等普通计数纳入。页面仍会保留 `DocumentBlock` 几何、默认 y-first 文本与 `layout_risk`，使比较结果降级而非伪造安全顺序。可选重型后端的验收必须以这类数值双栏为独立 corpus 类别，证明阅读顺序提升且不破坏表格行对应关系后才能启用。

进入条件：在扫描、多栏、无框表格、公式、旋转页五类 corpus 上，候选后端至少提升目标 anchor 召回，且不能增加技术内容误删；每一类单独设门，不使用总体平均分掩盖退化。

### P2：OCR 前处理按证据开启

增加方向检测、deskew 和局部表格/文本区域 OCR。所有前处理必须保存参数和页面级警告；不默认做强去噪、二值化或重采样覆盖，因为它们可能损坏细线、公式上标和标点。

进入条件：针对旋转、倾斜、低分辨率和复杂背景分别建立合成 fixture 与真实 corpus；比较字符/词 anchor、数值/单位保真和耗时上限。

### P3：视觉差异作为独立证据层

参考 diff-pdf 增加可选页面渲染差异图，只用于提示图片、印章、波形和矢量图变化。视觉变化不得混入文字“修改章节”数量，也不得把视觉零差异提升为语义一致。

进入条件：能配置页眉页脚/动态页码忽略区域；报告清晰区分“文字/结构变化”和“视觉变化”；对抗抗锯齿、字体渲染器差异和轻微页面位移。

## 暂不采用的方案

- 不把重型模型栈设为默认依赖：桌面包体、离线模型下载、CPU/GPU 兼容和启动时间尚未经过 corpus 验证。
- 不用 OCR 文本覆盖原生文字：原生坐标与字符映射仍是更可审计的证据。
- 不根据单一 OCR confidence 自动判等：confidence 不等于数值、否定词、单位和公式的语义正确性。
- 不把纯视觉 diff 作为章节比较主路径：它能发现像素变化，却不能回答“哪个规范条款改变了什么”。
