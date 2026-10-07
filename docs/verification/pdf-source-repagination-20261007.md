# 原文导航与重分页改字：2026-10-07 验证

本轮接续[适用性优化](pdf-adaptability-20261007.md)，完成原文导航和无标题连续页“一处改字 + 重分页”的保守配对。仅验证下表场景，没有运行整本 PDF 或全语料准确率测试。

## 代码与环境

- 仓库：`/Users/mac/PycharmProjects/RinysProject/codex_projects/pdf_protocol_diff`；分支 `codex/pdf-source-repagination-20261007`；基线 `2288c99f053470801e91f9d138531621c5ac8dbd`。最终代码身份为包含此记录的任务提交。
- 解释器：该项目 `.venv/bin/python`，实测 Python 3.12.1；沿用上轮解析和 OCR 环境，未安装或升级依赖。本轮关闭 OCR 缓存。
- 生产入口：`run_diff_transaction` → `report_outcome`，复跑工具为 [`verify_source_repagination_slices.py`](../../tools/verify_source_repagination_slices.py)。主代理写入，子代理只做指定来源与边界分析；未启用独立交付审查或 STRICT 门禁。

## 观察结果

| 输入与先验 | 结果 |
|---|---|
| 无标题原生文字，2→3 页，只有 `+3.0→+3.5`；新页恰从单位 `V.` 开始 | PASS：一个修改、一次替换，无额外增删；保留旧 1–2、新 1–3 页来源。首轮发现 `V.` 被误当列表项，改为仅消除物理页边界后通过。 |
| 一页原生正文不变，局部图像 `3.3 V→5.5 V`；旧 bbox `[65,220,415,290]`，新 bbox `[100,260,450,330]` | PASS：一个 OCR 复核项；按钮分别使用各自 bbox 和未标色原图，不把区域导航声称为字级定位。浏览器点击后两侧原文均可见。 |
| OIF `.058.11` p13 → `.058.13` p15，两处 Np 53→60 | PASS：两个替换，除 Np 数值与空白外片段不变；两处导航分别位于 y190–230、y390–430，物理页正确。实际点击首处后，裁图显示对应第一段，未串到第二处。 |
| DPOJET p88 的 explicit-clock 正文，人为分成 2→3 页并删除 `not derived` 中的 `not` | PASS：只报告一次替换；报告显示 `not→∅`、完整变化句以及双方原页范围，浏览器点击可见原文。属于真实来源文字的受控重排，**不是自然发布版本对**。 |

OIF 文件、每侧 SHA256 和物理页见[切片清单](pdf-adaptability-slices-20261007.json)的 `oif-np`。源页预期为 32.3.1.6.1 的 y200.48–216.86 和 32.3.1.6.2 的 y397.43–413.81；第一处 Dp=3 保持不变。首处原先因行内变化词比例不足而没有高亮，连带丢失可用源词；本轮拆开这两个判断，没有放宽高亮阈值。最终代码再次运行这一页对，增加“只允许两处 Np 变化”的片段断言，仍通过。

DPOJET 来源为 `/Users/mac/Documents/嵌入去嵌/outputs/cdr-research-rebuild-20260906/DPOJET-077004818.pdf`，SHA256 `0f8954d26a5dcd749c6127333e8bd4c833fc46623384d40ad2fe8cd4f5965411`，物理 p88（印刷 p64）。段落从 `In Explicit Clock Recovery,` 到 `measurement back to a single-source measurement.`；不包含左侧标题或上方参数表。生成样本使用系统 Arial 字体保留弯引号；保持来源抽取文字，不把本次结果解释为全部空格或字体问题已解决。

## 自动检查与复跑

最终 **309 项相关测试通过，9.983 秒**；10 个变动 Python 文件 AST 解析通过，`git diff --check` 通过。测试保护重复句、大小写、否定、角色变化、缺页、内容移动、多处改动、重复锚点、页面别名以及中性 OCR 显示。另复现了“子章并入父章后裁图在子标题处截断”的反例；原生文字无法精确定位时改为整页上下文，该反例通过。

```bash
PROTOCOL_PDF_DIFF_OCR_CACHE=off .venv/bin/python -B -m unittest \
  tests.test_reader_accuracy_regressions tests.test_reader_focus \
  tests.test_prose_source_visuals tests.test_candidate_visual_evidence \
  tests.test_fallback_repagination tests.test_image_regions \
  tests.test_comparison_profile tests.test_reporting_review_accuracy \
  tests.test_content_only tests.test_evidence_alignment -q

.venv/bin/python -B tools/verify_source_repagination_slices.py \
  --output work/source-repagination-rerun --real-np --real-manual
```

输出目录须尚不存在；省略两个 `--real-*` 参数仅跑受控样本。真实输入先核对 SHA256；OIF 只读旧 p13/新 p15，手册只抽取 p88。合成样本每侧最多三页。无随机采样或种子。复跑结束并完成可见检查后删除该输出目录。

实际 HTML 已在浏览器打开并点击定位，确认 OCR 上下文提示、Np 首段原文和否定词显示；未改桌面 GUI，未重复桌面启动检查。最后的整页兜底只影响缺少字级定位的原生文字，已查看的 Np/手册精确定位和 OCR 图像区域路径不受影响。

## 保留边界与清理

- 重分页配对要求同角色、连续无标题页、页数不同、前后各至少四个且全文唯一的字面 token，以及一个句段内一次编辑。它只提供配对候选，不丢弃正文或提高可信度。多处编辑、重复文字、移动或边界缺少锚点仍可能产生复核噪声。
- 上下文导航依赖该片段在本节和所选原页中唯一；不明确时不猜位置。普通原生文字兜底显示整页，局部 OCR 只认明确图像对象。子章并入父章后的字级定位仍可能被既有边界过滤，本轮只保证不再用猜测章节边界截掉上下文。
- 原文整页高亮、复杂表格单元格、脚注与图注归属、公式语义、移动或缩放图形的语义比较未在本轮解决。
- 已删除四个任务目录 `work/source-repagination-20261007`、`work/source-repagination-20261007-fixed`、`work/source-repagination-20261007-manual`、`work/source-repagination-20261007-final-np`，合计 4,723,551 字节；包括首轮失败输出、生成 PDF、报告与摘要。确认四目录均不存在，本轮浏览器页与临时服务器已关闭。计数为逻辑文件大小，不是磁盘空闲量。原始 PDF、环境及其他任务文件保留；仅保留本记录、复跑脚本和维护测试。
