# 正式路径问题修复与验收

本轮验证对象是默认桌面比较流程及其完整报告，源码实现提交为 `7f6f48f1c84472899b599101d2492ce9f2cf1e20`。两份原始 OIF 完整输入通过真实窗口的开始按钮运行，输入选择以外的比较、进度、终止和报告逻辑未替换。

## 方案与验收条件

生产路径保留原生字词、字体、物理页和坐标身份，由同一份来源证据确定章节及图表、公式、正文归属。配对保留每次出现，单侧内容只有获得足够缺失证据才成为确定增删；证据不足时保留原文并明确待核实。目录按完整条目及出现次数匹配，页脚仅凭来源坐标和实际页码词分离，真实数字、版本和次数仍参与比较。

这些规则不依赖 OIF 文件名、页码、机构名或原句。OIF 特定页码只存在于验收工具和人工参考。重型解析器的比较结果、源数据及接入局限见[开源方案与候选实测](general-engine-evaluation-20260907.md)。Docling 等仍是独立候选，未将模型输出直接当作正式文字真值。

可观察条件是：已知共同正文不再误增删；页边行号不能生成正文高亮；表格截图不能跨入其他所有者；小图不能放大；真实数值、符号及出现次数变化仍可见；不能安全核对的页可定位，且不能形成自动一致性结论。

## 实际运行与原问题复核

| 检查对象 | 结果与边界 |
| --- | --- |
| OIF 5.2 / 5.3 全本 | 656 / 685 页，真实桌面流程 1008.138 秒，完整生成六份报告 |
| 原 p351、352、381 共同正文 | 三个独立固定原句在审计和显示增删/替换片段中均无误报 |
| p43–48 图目录 | 一份双侧修改，未把共有目录整节判新增或删除；245个旧Figure编号、264个新编号及19个新增编号另经完整源片段回归 |
| p550 表格条带 | 没有该页正文截图条带，完整表格区域保留 |
| p561 HCB 图框 | 两侧均未被检测为表格，完整Figure原图可读；没有原先的单侧页脚假修改 |
| 原 p382 行号/空白误标 | 不再成为正文差异；本页像素没有安全配对，明确列出旧、新382及原因，状态为未核对，不能称像素一致 |
| 图片显示 | 真实 WKWebView 共 1297 张图片全部加载，无损坏、过度放大或横向溢出；另查看 HCB 和展开的覆盖清单 |

当前视觉核对完成 128 / 353 对，保守未核对 225 对，另有 206 个单侧页无法安全配对；报告列出全部 431 条页码和具体原因。运行异常类别为 0 条。文本块数量、内容或坐标不兼容不能概括为已证明的版面重排，现已分别记录。报告仍提示需人工复核并禁止自动一致性结论。

同机此前完成的同一对 OIF 桌面流程为1703.406秒（28分23秒），本次减少 40.8%，约为原来的 1.69 倍速度。此对照包含准确性修复造成的工作量变化，不是相同输出的微基准；原始907页、89分钟无结果的文件未取得，不能声称已逐文件复现或保证任何907页文件均达到该耗时。

## 回归、停止与独立样本

最后行为变更后完整回归为1297项、157.824秒、1项条件跳过，无失败；默认桌面启动检查通过。新增真实PDF检查覆盖空白图形页加插页、文本重排及渲染失败，要求JSON和可见HTML同时保留物理页及原因。

真实桌面按钮验证包含读取阶段与视觉阶段取消、等待清理、同一窗口再次成功比较；后台停止约0.03–0.11秒，界面恢复约0.17–0.30秒。既有完整报告内容不变，取消只清理本次临时输出。取消相关生产源码与这些实测保持一致。

HN73双栏输入首次失败已经保留，修复后用未改的gold回归通过，因此它属于已知反例，不冒充盲测。另一个独立原创CP46家族在首次运行前冻结PDF与人工预期，首次通过重复2→3次和+1.50→-1.50mV及行归属检查；最终门禁再按当前源码重跑。DPOJET物理88页的表格条件关系通过限定核验；AMSER复杂数学、指数和阅读顺序未认证，风险标记保留。这些结果不能推导总体准确率。

11组目标故障注入覆盖空字形与词界、来源连续段落、裁剪归属、图框误表、尺寸、页脚数字身份和版本次数、双栏标题、完整目录、同一词跨度重复证明以及逐页覆盖披露。局部机制RED与实际全本GUI/报告GREEN共同绑定；没有运行整个旧引擎的全OIF RED，也不把局部间隙测试冒充全本错配复现。

## 证据与交付身份

- [实际完整GUI证据](/Users/mac/Documents/ProtocolPdfDiffReports/stable_accepted_20260908/evidence.json)、[原问题审计](/Users/mac/Documents/ProtocolPdfDiffReports/stable_accepted_20260908/user-defect-audit.json)、[原生报告排版及截图索引](/Users/mac/Documents/ProtocolPdfDiffReports/stable_accepted_20260908/report-layout/layout.json)。
- [本次OIF HTML报告](/Users/mac/Documents/ProtocolPdfDiffReports/stable_accepted_20260908/reports/protocol_diff_20260908_112205_924cdc23699f4f92bbd77da2e8a9a033/protocol_diff_report.html)、[完整来源及差异JSON](/Users/mac/Documents/ProtocolPdfDiffReports/stable_accepted_20260908/reports/protocol_diff_20260908_112205_924cdc23699f4f92bbd77da2e8a9a033/protocol_diff_data.json)。
- [最终可执行v2清单](/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/test-effectiveness/stable-source-v2-final.json)与[正式执行回执](/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/test-effectiveness/final-executed-receipt.json)。回执实际状态决定门禁结果；本文不以清单存在替代执行。
- [权威缺陷账本](escaped-defects.yaml)记录四项用户反例的同一路径重跑及对应RED/GREEN；正式清单只取这四项的同内容切片，完整账本也绑定SHA。性能/取消的既有执行回执另行保留。
- 最终提交、两份独立精确提交审查、main及GitHub交付事实见项目根[handoff](../../handoff.md)和[交付门禁回执](/Users/mac/Documents/ProtocolPdfDiffReports/stable_delivery_20260908/delivery-receipt.json)。

输入SHA-256：旧 `dc504341bd4190bfc98f32bf1bf5c683cc6cf5c22906f45a00a09b2870a90191`，新 `1fa2417c96f06bc8115bcc79b7d2f7a160e35b8cc5bbebe7051fd446833e2f72`。运行前后所有生产源码SHA一致。环境为macOS26.6.2 arm64、Python3.12.1、PyMuPDF1.28.0、pdfplumber0.11.10、pypdfium2 5.11.0、pywebview6.2.1。

[远端macOS/Windows检查](https://github.com/ZhenlongYou/pdf-protocol-diff/actions/runs/34182496589)的测试、构建及适用原生窗口检查成功；流程总状态仍失败，原因仅为GitHub制品存储配额不足，安装包未上传。未删除他人的制品或绕过失败状态。Windows冻结应用的真实整本OCR取消没有在本轮实测。


## 图片尺寸测量精度

正式v2首次执行的前39项通过，但最终窗口尺寸断言被V54差异掩膜触发：自然尺寸816×987、computedStyle816×987，DOMRect为816×987.015625。独立复核确认差值恰为[WebKit LayoutUnit的1/64 CSS像素](https://trac.webkit.org/wiki/LayoutUnit)。因此最终窗口检查同时要求DOMRect最多超出一个布局单位、计算后的CSS尺寸仍不超过自然尺寸加0.01；自检拒绝2/64偏差和真实CSS放大。其余既有尺寸反例的阈值未改，width100%放大变异仍必须失败。第一次BLOCKED回执及材料原样保存在最终证据目录的attempt-1-native-quantization中，不冒充首次全部通过。
