# PDF 协议差异对比工具

这个小工具用于把“旧协议 PDF”和“新协议 PDF”按章节/小节抽取出来，自动输出差异报告。它适合日常协议更新审阅：先告诉你新协议相对旧协议在哪一章、哪一节发生了新增、删除或修改，再给出关键片段，帮助你快速定位到 PDF 原文复核。

## 能输出什么

每次运行会在 `results/protocol_diff_时间戳/` 下生成：

- `protocol_diff_report.html`：最直观的浏览器版报告，包含左侧差异导航、红绿高亮、旧/新片段并排对比。
- `protocol_diff_report.md`：Markdown 版报告，便于放进文档系统或代码仓库。
- `protocol_diff_report.txt`：纯文本版本，便于复制到邮件、IM 或审阅记录。
- `changes.csv`：结构化差异表，可以用 Excel 打开筛选。
- `protocol_diff_data.json`：机器可读结果，包含差异列表、旧/新章节位置、页码、片段和解析到的章节元数据。

## 安装

在本项目目录运行：

```bash
python3 -m pip install -r requirements.txt
```

如果 PyCharm 当前解释器是 `/Users/mac/anaconda3`，请用同一个解释器安装依赖：

```bash
/Users/mac/anaconda3/bin/python -m pip install -r requirements.txt
```

如果你是在 `/Users/mac/PycharmProjects/RinysProject` 根目录打开 PyCharm，
也可以在根目录运行 `python3 -m pip install -r requirements.txt`。根目录
依赖文件会把本工具以 editable package 安装进当前解释器，PyCharm 就能识别
`protocol_pdf_diff` 导入。

## PyCharm 直接运行

打开 `main.py`，只需要改顶部用户参数区：

```python
OLD_PDF_PATH = "/path/to/old.pdf"
NEW_PDF_PATH = "/path/to/new.pdf"
OLD_START_PAGE = None  # 例如 10；None 表示从第一页开始
OLD_END_PAGE = None    # 例如 25；None 表示到旧 PDF 最后一页
NEW_START_PAGE = None  # 新旧范围可以不同
NEW_END_PAGE = None
OUTPUT_DIR = "results"
```

然后运行：

```bash
python3 main.py
```

如果两个 PDF 路径留空，脚本会自动生成内置多页 demo PDF 并跑一遍。这个 demo 包含旧 4 页/新 5 页、页码漂移、重复页眉页脚、视觉图块变化、章节新增和正文修改，比最小样例更接近日常协议更新。
运行结束后优先打开终端打印出来的 `protocol_diff_report.html`，它比纯文本更适合快速看清“旧协议删了什么、新协议加了什么”。

## 命令行运行

```bash
python3 main.py --old-pdf /path/to/old.pdf --new-pdf /path/to/new.pdf
```

如果只想比较长 PDF 中的一段，可以给新旧 PDF 分别指定页码范围。页码是
PDF 阅读器常见的 1-based 页码，且包含起止页：

```bash
python3 main.py \
  --old-pdf /path/to/old.pdf --old-start-page 30 --old-end-page 45 \
  --new-pdf /path/to/new.pdf --new-start-page 34 --new-end-page 51
```

可选参数：

- `--output-dir results`：指定报告输出目录。
- `--demo`：强制生成并比较内置示例 PDF。
- `--old-start-page 30` / `--old-end-page 45`：只抽取旧协议指定页码范围。
- `--new-start-page 34` / `--new-end-page 51`：只抽取新协议指定页码范围。
- `--min-section-match-similarity 0.72`：章节被重命名/重编号时，仍判定为同一章节的最低相似度。调低会减少新增/删除，调高会减少误配。
- `--unchanged-similarity 0.985`：高于该相似度的章节默认视为未变化，不在报告中展开。
- `--max-snippets 8`：每个章节最多展示多少条差异片段。
- `--include-unchanged`：同时列出未变化章节。

## 识别逻辑和边界

工具会识别常见协议标题格式，例如 `第一章`、`第2节`、`1`、`1.1`、`1.1.1`、`一、`、`(一)`。如果 PDF 把编号和标题抽成两行，例如 `1` 下一行是 `适用范围`，工具会尝试合并为同一节标题。如果 PDF 没有明显标题，工具会退回到按页块和正文相似度比较，并在报告中明确提示该 fallback 状态。

对于 PCIe 测试规范这类文档，工具会尽量把 `2.13.2 Overview...` 下面的 `1. Connect...`、`14. Turn...` 等连续测试步骤保留在父章节正文里，而不是把每个步骤都拆成独立章节。这样报告更接近“第几章第几节发生了什么变化”的审阅方式。

默认比较不是“旧第 2 页对新第 2 页”这种硬对齐。只要识别到章节结构，工具会先按章节编号、标题和正文相似度匹配逻辑章节，页码只作为报告里的定位线索。新增内容导致后续页码整体后移时，同一章节仍会被匹配到一起；无标题 fallback 场景也会尽量通过正文相似度跨页匹配，而不是只看页号。

当你指定 `--old-start-page/--old-end-page` 或 `--new-start-page/--new-end-page`
时，工具会先只抽取各自范围内的文字，再做章节/正文相似度匹配。报告顶部会显示
源 PDF 总页数以及本次实际比较的“旧选择页 / 新选择页”，方便你确认没有把范围外
章节混进去。

PDF 不是天生适合结构化比对的格式，所以请留意这些限制：

- 扫描版或图片版 PDF 通常抽不出文字，需要先 OCR。
- 图片、矢量图、盖章等非文字视觉差异默认不比较；本工具关注可抽取文本。
- 重复页眉、页脚、保密标识和 `Page 1 of 4` 这类动态页码会尽量过滤，避免混进正文差异。
- 表格、页眉页脚、复杂多栏排版可能导致文本顺序和肉眼看到的不完全一致。
- 相似度匹配用于处理重命名或重编号章节；如果协议中大量章节正文模板高度相似，建议调高 `--min-section-match-similarity` 后复跑。
- 自动报告用于快速定位差异，最终协议结论仍建议回到源 PDF 复核。

## 从 Codex 下载交付包

如果你是从 Codex 的 `outputs/` 下载本工具，请下载推荐的 zip 文件，解压后进入项目目录运行上面的安装和验证命令。`outputs/DOWNLOADS.md` 会标明推荐的最新版本。

## 验证

安装依赖后运行：

```bash
python3 -m unittest discover -s tests
python3 main.py
```

第二条命令会用内置多页 demo PDF 验证从“PDF 输入”到“HTML/CSV/JSON 报告输出”的完整路径。
