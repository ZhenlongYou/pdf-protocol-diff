# PDF 边界优化验证 · 2026-10-07

本记录对应基线 `909714ccc885710d8855ded28439660a9ac451c7` 上的本次任务提交；代码、测试、README 与本记录同次提交。权威目录为 `/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`，解释器为其 `.venv/bin/python`，Python 3.12.1。采用 STANDARD 范围验证；未运行整本 PDF、全语料、Windows 打包或独立审查门禁。

## 修改与反例

| 场景 | 修改前观察 | 本轮观察 |
|---|---|---|
| 正文相同，小栅格中的小数点删除 | 96 dpi 卡片过滤后允许无差异结论；新增反例先失败 | 沿用已有 144 dpi 原页核对及已有排除区域，残留未能证明一致时列为 `residual` 复核；不宣称已解释图像含义 |
| `3.3V`、科学计数法、中文紧邻数值和符号 | 六组摘要跨度断言先失败 | 保留完整原始数值；源字符不丢失、不求值、不换算，`3.0` 与 `3.00` 仍不同 |
| 无标题正文 2→2 页重排，或其他段落有未变小数 | 新增配对反例先失败 | 唯一前后锚点和单次编辑条件成立时，只报告真实改字；保留双方全部页来源 |
| 两列叙述格 1→2 页，同文、10→20、删除 not | 三组公开入口反例先失败，产生六条确定行变化 | 满足源格/源词/列边界/页边/其余行守恒条件后，集中为一条复核；全部物理行、重复首行、真实改字和页码保留 |

实现见 [visual_watchdog.py](../../src/protocol_pdf_diff/visual_watchdog.py)、[literal_atoms.py](../../src/protocol_pdf_diff/literal_atoms.py)、[fallback_repagination.py](../../src/protocol_pdf_diff/fallback_repagination.py)、[table_repagination.py](../../src/protocol_pdf_diff/table_repagination.py)。源词导航契约没有改用新的摘要分词。

## 可执行检查

328 项相关测试通过，14.013 秒：

```sh
PYTHONPATH=src:tests PROTOCOL_PDF_DIFF_OCR_CACHE=off .venv/bin/python -B -m unittest tests.test_reader_accuracy_regressions tests.test_reader_focus tests.test_literal_atoms tests.test_fallback_repagination tests.test_visual_coverage_disclosure tests.test_candidate_visual_evidence tests.test_graphic_region_review tests.test_content_only tests.test_evidence_alignment tests.test_table_repagination tests.test_table_annotation_role tests.test_table_order_generality tests.test_bounded_table_candidate tests.test_physical_table_rows_candidate tests.test_complete_numeric_table_literal tests.test_reporting_review_accuracy -q
```

首次未设置 `PYTHONPATH=src:tests` 时，`test_complete_numeric_table_literal` 的既有同目录 fixture 导入报错，不能算通过；补齐测试启动路径后上述 328 项通过。随后补充反向 2→1 页公开事务测试，单独运行 `tests.test_table_repagination.TableRepaginationTests.test_two_pages_joined_back_into_one_stays_a_sourced_review` 通过（1.152 秒）。因此当前同一套命令会包含 329 项。

另外，`tests.test_protocol_diff` 中名称以 `test_visual_watchdog` 开头的 20 项既有测试全部通过（21.467 秒），覆盖已知噪声排除、截图、移动、空文字和失败披露。13 个变更 Python 文件 AST 解析、`git diff --check` 通过。测试生成器不充当自然 PDF 的通用真值；反例只证明所列行为。

## 公开事务入口和真实小样本

```sh
.venv/bin/python -B tools/verify_boundary_accuracy_slices.py --output work/boundary-accuracy-20261007 --real-np --real-manual
```

[检查脚本](../../tools/verify_boundary_accuracy_slices.py)通过 `run_diff_transaction` → `report_outcome`，8 组全部通过；OCR 缓存关闭，输入生成无随机性：

- 受控小栅格小数点删除：正文 0 变化、1 项残留像素复核，不允许无差异结论；独立生成的相同图像对照仍可取得一致证据。
- 原生文字 `3.3V→3.5V`：HTML 摘要完整显示 `3.3→3.5`，单位留在上下文。
- 2→2 页重排，`+3.0→+3.5` 且另有未变 `+5.0`：仅 1 处替换，无额外整段新增/删除。
- 两列叙述格拆页：同文和 `10→20` 两组均为 1 条有完整来源的复核。
- OIF 自然版本对旧 p13 / 新 p15：两处 `Np=53→60`，各有自己的字词坐标，未串到同页其他相似正文。
- DPOJET 真实 p88 段落：人为重排 2→3 页并删除 `not`，仍只报告已知编辑。此项是受控重排，不是自然发布的版本对。

真实 OIF 路径与 SHA-256 由[既有输入清单](pdf-adaptability-slices-20261007.json)提供，并由脚本实际校验。DPOJET 输入、SHA-256 与段落边界复用[源页验证记录](pdf-source-repagination-20261007.md)及其检查脚本；本轮同样核对源文件 SHA-256，不重新读取全本正文。

并行只读子任务从同一清单仅选 `cem-tables`，使用 `tools/verify_adaptability_slices.py` 的 `run_manifest` 检查旧 p42–43 / 新 p51–52：7.55 秒通过。旧/新 SHA-256 校验通过；Table 5、6、4-1、4-2 与 `12V-2x6 Connector` 保留；5 条正文变化、1 张表格复核卡、8 项覆盖复核。此为子任务回报的标题/连接器存在性检查，没有逐格金标准，也未做 CEM 浏览器视觉验收。

## 实际 HTML 检查与边界

主任务用本地 HTTP 服务打开本轮生成的 `tiny-decimal`、`attached-unit`、`table-edited` 报告，实际点击并截图核对：

- 小数点报告顶部为“需人工复核”；展开清单有旧/新 p1 和具体原因，明确不能据此确认一致。
- 数值摘要和完整上下文可读；点击“定位对应原文”后，旧/新 p1 原图均可见。
- 表格只有 1 条复核，双方完整原文含 `10 ms` / `20 ms`；旧 p1 与新 p1/p2 原页表格都显示，无确定增删标色。

这证明所检查报告的可见行为，没有认证任意 PDF 准确率。表格候选仅覆盖相邻页、矩形两列、英文长叙述、一处以内编辑，遇到合并格、列变动、源词缺失、额外同名行或更多物理表时保留原路径。多处独立正文改动、移动/重复内容、复杂表格与图形语义仍有边界。残留像素检查只在可安全配对并走到相应核对路径的页对上生效；未覆盖页仍单列复核。

## 临时输出

主任务 `work/boundary-accuracy-20261007`：87 个文件，逻辑文件大小合计 3,210,786 字节，视觉检查结束后已删除并确认目录不存在。子任务临时 CEM 输出 10 个文件、2,724,736 字节已删除。原始 PDF、运行环境、既有其他任务输出未动。上述数值是文件大小统计，不是磁盘空闲空间变化。仅保留本记录、复跑工具和维护的测试源码；浏览器页与临时 HTTP 服务已关闭。
