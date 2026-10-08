# PDF 对比核心重构与验证（2026-10-09）

本轮针对内容判断与报告相互依赖、来源身份随截图状态变化、章节候选抢占和视觉残差遗漏重构主链路。权威目录为 `codex_projects/pdf_protocol_diff`，任务分支为 `codex/pdf-core-architecture-20261009`，基线为 `069e3ec739af5106b225ae185389b67d7ee9b9c4`。代码、测试和本记录随同一任务提交；最终代码身份取包含本记录的 Git 提交。

## 主链路与责任边界

```mermaid
flowchart LR
    A[源文件快照与物理位置] --> B[结构抽取与章节候选竞争]
    B --> C[来源截图与覆盖核对]
    C --> D[表格事实和内容规则]
    D --> E[凭证写入并回读]
    E --> F[固定 ComparisonView]
    F --> G[各格式一次渲染]
    G --> H[独占目录原子发布]
```

- 生产入口统一为 [`run_comparison`](../../src/protocol_pdf_diff/table_view_transaction.py)，桌面、WebView、命令入口和验证脚本使用同一路径。取消子进程、累计 OCR 预算、源文件哈希和发布失败保护仍保留。旧的显式注入接口继续兼容。
- 从 `reporting.py` 提出正文/表格判定规则，集中在 [`comparison_content.py`](../../src/protocol_pdf_diff/comparison_content.py) 的 `build_table_content`、`build_comparison_view`。报告根据确定的视图输出 HTML、Markdown、TXT、CSV、JSON；候选表格的凭证失败时整组退回原结果，禁止文件生成后再追加改变结论。凭证准备见 [`comparison_preparation.py`](../../src/protocol_pdf_diff/comparison_preparation.py)。
- 已完成比较的 profile、匹配阈值和来源选项必须一致；摘要条数是允许独立调整的展示参数。视觉覆盖使用完整内容记录，不能由摘要截断决定某页是否存在内容变化。见 `validate_result_options`、`_complete_reader_occurrences`、[`_reader_visible_semantic_change_pages`](../../src/protocol_pdf_diff/visual_watchdog.py)。
- 章节先汇总同编号、唯一完整正文和结构候选，再做同一轮保序选择；完整正文证据与相似度分开计分，插入的新章节不能仅靠占用旧编号抢走原有章节。见 [`_match_sections` / `_select_monotonic_section_candidates`](../../src/protocol_pdf_diff/compare.py)。已有后续结构恢复规则保留，不宣称所有历史规则已消除。

## 来源与视觉规则

1. 表格保留稳定的文件哈希/页号/表号身份，物理框与截图裁剪框分开。截图失败保留结构内容和失败状态；没有可见来源图时，不能仅凭表格框隐藏正文。见 [`TableVisual` / `PageText`](../../src/protocol_pdf_diff/models.py)、[`_build_table_visual`](../../src/protocol_pdf_diff/pdf_extract.py)、[`visual_ownership.py`](../../src/protocol_pdf_diff/visual_ownership.py)。
2. 章节建立真实标题位置与表格来源关联；追加到比较文本的派生表格行用精确区间记录，章节切分前只移除这些已知区间。续页表格仍可归属前章，位于下一标题之前的表格不能倒归给后章。见 [`_bind_table_sources`](../../src/protocol_pdf_diff/sectioning.py)。CEM 拆表只用严格同题核心和唯一后续标题边界建立整组复核线索，后续边界不是表格章节所有权证明；见 `_tables_share_unique_following_boundary`。
3. 局部图片 OCR 单独记录区域数，不再把整页原生正文标成 OCR。含完整内部文字标签的固定图示允许像素核对，正文跨越图形边界仍拒绝自动处理。见 [`pdf_extract.py`](../../src/protocol_pdf_diff/pdf_extract.py)、[`image_regions.py`](../../src/protocol_pdf_diff/image_regions.py)。
4. 表格截图保持原像素，定位装饰留在显示层。页级及固定图示核对统一调用 [`review_source_pixels`](../../src/protocol_pdf_diff/visual_watchdog.py)：在 144 DPI 上区分逐像素一致、可展示变化和未解释残差。存在大块变化时，远处微小残差也进入定位区域；仅有低于材料性阈值的小残差时保留未核实状态，不能证明一致。provenance 记录实际使用的 144 DPI。
5. 正文和表格调用同一 [`canonical_content_number`](../../src/protocol_pdf_diff/text_utils.py)：普通小数末尾零仍参与比较，长整数不受 Decimal 全局精度舍入影响。覆盖的是明确字面数值规则，不等于完整的科学计量有效数字推理。
6. 独立预期直接检查实际 HTML/Markdown/TXT 块；JSON 将卡片标为隐藏不能证明文字在报告中没有泄漏。见 [`_expected_reader_failures`](../../src/protocol_pdf_diff/accuracy_evaluation.py)。

## 实际执行结果

环境：项目 `.venv/bin/python`，Python 3.12.1；PyMuPDF 1.28.0、Pillow 12.3.0、OpenCV 5.0.0。执行时 `PROTOCOL_PDF_DIFF_OCR_CACHE=off`，受控 PDF 由 PyMuPDF 生成，像素反例无随机性。

| 检查 | 观察结果 |
|---|---|
| 宽范围回归 | 1,893 项通过，274.232 秒，无失败 |
| 新增架构反例 | 9 项通过；展示条数不改变完整内容/覆盖，插入章节候选竞争，正文与表格数值规则一致，profile 不得重解释，截图失败结构保留，原始截图无装饰，大小像素共同定位，表格位于后续标题前的归属，实际导出文字泄漏反例 |
| 真实与受控适用性页窗 | 最终重跑 8 组通过：5 个受控反例、3 个真实版本页窗 |
| 来源与重分页 | 3 组通过：受控 2→3 页单处改字、移动图片各自原框、真实 OIF Np 定位；OIF 与上一行是同一对输入，不能重复算独立样本 |
| CEM 强断言 | 宽测中执行；最后仅调整复核文案后再次单独执行 1 项通过（9.697 秒），验证四表题为 1 个 review、双方页窗正确、无重复嵌套表片段 |
| 命令入口 | 使用 `main.py` 实际运行同一 CEM 页窗，退出码 0；检查最终 JSON 为 1 个 review，实际 HTML 已打开核对修订后的复核说明 |
| 静态检查 | `git diff --check`、Ruff F821（Python 3.12）通过 |

宽测范围为其它 87 个 `test_*.py` 模块全量，加 `test_protocol_diff.py` 中 497 个无装饰测试方法；共 584 个加载条目。该大模块排除了 2 个 Tk 桌面测试与 15 个依赖旧路径本地 PDF 的测试；其它模块里的真实页窗测试仍运行。这不是“所有测试全量”。初次宽测发现的 CEM 表格归属、来源图可见性及旧测试注入位置/选项不一致已逐项修正，再执行最终宽测。

真实输入的完整路径、SHA-256、页窗和预期事件复用[原页窗清单](pdf-adaptability-slices-20261007.json)，脚本在运行前核验全文件身份：

| 输入 | 页窗 | 本轮观察 |
|---|---|---|
| `oif2024.058.11.pdf` → `oif2024.058.13.pdf` | 旧 13 → 新 15 | 两处 Np=53→60，分别对应 32.3.1.6.1、32.3.1.6.2；源框纵坐标不同 |
| `OIF-CEI-5.1.pdf` → `OIF-CEI-05.3.pdf` | 旧 105 → 新 109 | 未产生实质修改结论，保留 1 项 review；不等于证明所有图形一致 |
| `pcie_cem_r4.pdf` → `pcie_cem_r51.pdf` | 旧 42–43 → 新 51–52 | Table 5/6 与 Table 4-1/4-2 合为 1 个 review，保留 `12V-2x6 Connector`；未自动承诺逐行对应 |

CEM 清单本身只检查锚点，曾让旧的四张增删卡通过；本轮结论还依赖 [`test_real_cem_power_table_split_is_one_review_group_without_nested_fragments`](../../tests/test_page_window_content_correspondence.py) 的独立强断言及最终报告核对，不能只引用清单 PASS。

实际 HTML 已在浏览器默认显示尺寸打开并点击：OIF 两项原文定位分别指向旧 13 / 新 15 页的对应段落；CEM 一张复核卡显示两侧四个表题与正确页窗；受控原生正文页中的远处小点和大方块有两个独立导航入口，第二处显示旧黑块/新空白。该受控页尺寸 612×792 pt，两侧原生正文相同，旧页另有 `(100,220,101,221)` 与 `(260,650,285,675)` 两个黑矩形，新页无矩形；144 DPI 差异总框 `(199,439,571,1351)`，独立定位框数为 2。

## 复跑

在仓库根目录使用项目解释器，输出放入新的任务临时目录。重点反例和真实输入：

```sh
PYTHONPATH=src:tests PROTOCOL_PDF_DIFF_OCR_CACHE=off .venv/bin/python -B -m unittest tests.test_core_architecture tests.test_table_view_transaction tests.test_page_window_content_correspondence tests.test_image_regions
PYTHONPATH=src PROTOCOL_PDF_DIFF_OCR_CACHE=off .venv/bin/python -B tools/verify_adaptability_slices.py --output /tmp/pdf-core-architecture-rerun --manifest docs/verification/pdf-adaptability-slices-20261007.json
PYTHONPATH=src PROTOCOL_PDF_DIFF_OCR_CACHE=off .venv/bin/python -B tools/verify_source_repagination_slices.py --output /tmp/pdf-core-repagination-rerun --real-np
```

宽测选择器复现如下：将代码保存为临时 `.py` 文件，从仓库根目录用项目解释器执行；不要直接通过 stdin 执行，因为子进程取消测试需要可重新导入的主模块。

```python
import ast
from pathlib import Path
import sys
import unittest

if __name__ == '__main__':
    root = Path.cwd()
    sys.path[:0] = [str(root), str(root / 'src'), str(root / 'tests')]
    names = []
    for path in sorted((root / 'tests').glob('test_*.py')):
        module = 'tests.' + path.stem
        if path.stem != 'test_protocol_diff':
            names.append(module)
            continue
        for cls in ast.parse(path.read_text()).body:
            if isinstance(cls, ast.ClassDef):
                names.extend(module + '.' + cls.name + '.' + m.name
                             for m in cls.body
                             if isinstance(m, ast.FunctionDef)
                             and m.name.startswith('test_') and not m.decorator_list)
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    raise SystemExit(not unittest.TextTestRunner().run(suite).wasSuccessful())
```

## 范围与清理

本轮建立主链路责任边界并验证列明的反例，未把历史判定规则全部重写；新的内容模块仍较大，后续可按正文投影、表格身份和表格内容继续拆分，但应保持本轮公共视图合同。没有运行整本协议/全部私有语料准确率评估、Windows 实机测试或桌面重新打包。缩放/移动图形配准、一般图形语义、复杂合并格及公式自动比较不在本轮保证范围；无法证明的位置继续给人工复核，公式自动比较仍关闭。

本任务临时目录 `/tmp/pdf-core-architecture-20261009` 的生成 PDF、报告、调试脚本和日志共 64,485,043 字节，已在记录以上观察后删除；这是文件大小合计，不是实测磁盘可用空间增量。临时浏览器标签与本地 HTTP 服务已关闭。仅删除本轮新增内容模块的编译缓存，其余缓存、原始真实 PDF、维护中的测试输入和 `.venv` 保留；本任务无待保留临时结果。
