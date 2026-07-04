"""Text normalization utilities shared by sectioning and diffing.

PDF extraction often inserts arbitrary line breaks and spacing. These helpers
apply conservative cleanup only: they avoid aggressive rewriting so the final
report still resembles the source protocol text a reviewer will see on screen.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 导入 re，执行正则匹配和文本规则识别。
import re
# Codex说明(自动生成)： 导入 unicodedata，提供本文件后续流程需要的库能力。
import unicodedata

# Codex说明(自动生成)： 计算并保存 _WHITESPACE_RE，供后续语句继续读取或更新。
_WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")
# Codex说明(自动生成)： 计算并保存 _MULTI_BLANK_RE，供后续语句继续读取或更新。
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


# Codex说明(自动生成)： 定义函数 normalize_line，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def normalize_line(line: str) -> str:
    """Normalize one extracted line without destroying protocol numbering."""

    # Codex说明(自动生成)： 计算并保存 normalized，供后续语句继续读取或更新。
    normalized = unicodedata.normalize("NFKC", line)
    # Codex说明(自动生成)： 计算并保存 normalized，供后续语句继续读取或更新。
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    # Codex说明(自动生成)： 返回 normalized.strip()，让调用方取得本函数的处理结果。
    return normalized.strip()


# Codex说明(自动生成)： 定义函数 normalize_for_similarity，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def normalize_for_similarity(text: str) -> str:
    """Normalize text for similarity scoring.

    This function deliberately keeps punctuation and digits because protocol
    deltas often hide in numbers, dates, voltage/current limits, or option names.
    """

    # Codex说明(自动生成)： 计算并保存 lines，供后续语句继续读取或更新。
    lines = [normalize_line(line) for line in text.splitlines()]
    # Codex说明(自动生成)： 计算并保存 compact，供后续语句继续读取或更新。
    compact = "\n".join(line for line in lines if line)
    # Codex说明(自动生成)： 计算并保存 compact，供后续语句继续读取或更新。
    compact = _MULTI_BLANK_RE.sub("\n\n", compact)
    # Codex说明(自动生成)： 返回 compact.casefold()，让调用方取得本函数的处理结果。
    return compact.casefold()


# Codex说明(自动生成)： 定义函数 compact_inline，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def compact_inline(text: str) -> str:
    """Make a short single-line snippet for reports and CSV fields."""

    # Codex说明(自动生成)： 返回 _WHITESPACE_RE.sub(' ', ' '.join(text.split())).strip()，让调用方取得本函数的处理结果。
    return _WHITESPACE_RE.sub(" ", " ".join(text.split())).strip()


# Codex说明(自动生成)： 定义函数 truncate，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def truncate(text: str, max_chars: int = 260) -> str:
    """Trim a report snippet while preserving the most useful prefix."""

    # Codex说明(自动生成)： 计算并保存 value，供后续语句继续读取或更新。
    value = compact_inline(text)
    # Codex说明(自动生成)： 检查条件 len(value) <= max_chars，根据结果选择后续执行路径。
    if len(value) <= max_chars:
        # Codex说明(自动生成)： 返回 value，让调用方取得本函数的处理结果。
        return value
    # Codex说明(自动生成)： 返回 value[:max_chars - 1].rstrip() + '…'，让调用方取得本函数的处理结果。
    return value[: max_chars - 1].rstrip() + "…"
