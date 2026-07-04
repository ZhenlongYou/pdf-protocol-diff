# PDF 协议差异对比工具

这个小工具用于把“旧协议 PDF”和“新协议 PDF”按章节/小节抽取出来，自动输出差异报告。它适合日常协议更新审阅：先告诉你新协议相对旧协议在哪一章、哪一节发生了新增、删除或修改，再给出关键片段，帮助你快速定位到 PDF 原文复核。

## 能输出什么

每次运行会在 `results/protocol_diff_时间戳/` 下生成：

- `protocol_diff_report.md`：推荐阅读版报告，包含汇总、抽取警告、章节级详细差异。
- `protocol_diff_report.txt`：纯文本版本，便于复制到邮件、IM 或审阅记录。
- `changes.csv`：结构化差异表，可以用 Excel 打开筛选。
- `parsed_sections.json`：调试用，记录工具识别到的章节和页码范围。

## 安装

在本项目目录运行：

```bash
python3 -m pip install -r requirements.txt
```

## PyCharm 直接运行

打开 `main.py`，只需要改顶部用户参数区：

```python
OLD_PDF_PATH = "/path/to/old.pdf"
NEW_PDF_PATH = "/path/to/new.pdf"
OUTPUT_DIR = "results"
```

然后运行：

```bash
python3 main.py
```

如果两个 PDF 路径留空，脚本会自动生成内置 demo PDF 并跑一遍，方便确认环境安装正确。

## 命令行运行

```bash
python3 main.py --old-pdf /path/to/old.pdf --new-pdf /path/to/new.pdf
```

可选参数：

- `--output-dir results`：指定报告输出目录。
- `--demo`：强制生成并比较内置示例 PDF。
- `--min-section-match-similarity 0.72`：章节被重命名/重编号时，仍判定为同一章节的最低相似度。调低会减少新增/删除，调高会减少误配。
- `--unchanged-similarity 0.985`：高于该相似度的章节默认视为未变化，不在报告中展开。
- `--max-snippets 8`：每个章节最多展示多少条差异片段。
- `--include-unchanged`：同时列出未变化章节。

## 识别逻辑和边界

工具会识别常见协议标题格式，例如 `第一章`、`第2节`、`1`、`1.1`、`1.1.1`、`一、`、`(一)`。如果 PDF 把编号和标题抽成两行，例如 `1` 下一行是 `适用范围`，工具会尝试合并为同一节标题。如果 PDF 没有明显标题，工具会退回到按页对比，并在报告中体现页码。

PDF 不是天生适合结构化比对的格式，所以请留意这些限制：

- 扫描版或图片版 PDF 通常抽不出文字，需要先 OCR。
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

第二条命令会用内置 demo PDF 验证从“PDF 输入”到“报告输出”的完整路径。
