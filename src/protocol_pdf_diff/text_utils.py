"""Text normalization utilities shared by sectioning and diffing.

PDF extraction often inserts arbitrary line breaks and spacing. These helpers
apply conservative cleanup only: they avoid aggressive rewriting so the final
report still resembles the source protocol text a reviewer will see on screen.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_EMBEDDED_DRAFT_LETTER_WORD_RE = re.compile(r"\b[A-Za-z]*[a-z][DRAFT][a-z][A-Za-z]*\b")
_DRAFT_FRAGMENT_CORRECTION_WORDS = frozenset(
    {
        "characteristic",
        "characteristics",
        "compliance",
        "condition",
        "conditions",
        "and",
        "differential",
        "frequency",
        "measured",
        "measurement",
        "parameter",
        "parameters",
        "receiver",
        "reference",
        "reflection",
        "requirement",
        "requirements",
        "return",
        "signal",
        "signals",
        "than",
        "transmitter",
        "transmission",
        "value",
        "voltage",
        "waveform",
    }
)  # 只有删除残字后命中这些常见协议词，才认为是 DRAFT 水印污染。
_NUMBER_WORD_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_NUMBER_WORD_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
CHINESE_NUMBER_CHARS = "零〇一二两三四五六七八九十百千万"
CHINESE_COUNT_UNITS = (
    "数据包",
    "工作日",
    "小时",
    "分钟",
    "毫秒",
    "微秒",
    "纳秒",
    "皮秒",
    "字节",
    "比特",
    "波形",
    "样本",
    "报文",
    "通道",
    "sample",
    "samples",
    "waveform",
    "waveforms",
    "lane",
    "lanes",
    "bits",
    "bytes",
    "bit",
    "个",
    "次",
    "项",
    "条",
    "页",
    "章",
    "节",
    "点",
    "种",
    "类",
    "路",
    "组",
    "位",
    "天",
    "日",
    "周",
    "月",
    "年",
    "秒",
)


def _count_unit_pattern(unit: str) -> str:
    """Build a count-unit regex, protecting ASCII identifier prefixes."""

    escaped = re.escape(unit)
    if re.fullmatch(r"[A-Za-z0-9_]+", unit):
        return escaped + r"(?![A-Za-z0-9_])"
    return escaped


CHINESE_COUNT_UNIT_PATTERN = "|".join(
    _count_unit_pattern(unit) for unit in sorted(CHINESE_COUNT_UNITS, key=len, reverse=True)
)
_CHINESE_CONTEXT_NUMBER_RE = re.compile(
    rf"([{CHINESE_NUMBER_CHARS}]+)(?=\s*(?:{CHINESE_COUNT_UNIT_PATTERN}))",
    flags=re.I,
)
_CHINESE_ORDINAL_NUMBER_RE = re.compile(
    rf"第\s*([{CHINESE_NUMBER_CHARS}]+)\s*([章节条项部分])"
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}


def normalize_line(line: str) -> str:
    """Normalize one extracted line without destroying protocol numbering."""

    normalized = unicodedata.normalize("NFKC", line)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def normalize_for_similarity(text: str) -> str:
    """Normalize text for similarity scoring.

    This function deliberately keeps punctuation and digits because protocol
    deltas often hide in numbers, dates, voltage/current limits, or option names.
    """

    lines = [normalize_line(line) for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line)
    compact = _MULTI_BLANK_RE.sub("\n\n", compact)
    return compact.casefold()


def compact_inline(text: str) -> str:
    """Make a short single-line snippet for reports and CSV fields."""

    return _WHITESPACE_RE.sub(" ", " ".join(text.split())).strip()


def remove_draft_watermark_letter_artifacts(text: str) -> str:
    """Remove isolated DRAFT watermark letters that leaked into body text."""

    if "表格行:" in text:  # 结构化表格行可能合法包含单字母符号，不能按水印残片清理。
        return text
    cleaned = _EMBEDDED_DRAFT_LETTER_WORD_RE.sub(_clean_embedded_draft_letter_word, text)  # 先处理 RequirRements 这类词内污染。
    word_count = len(re.findall(r"[A-Za-z]{3,}", cleaned))  # 只有长正文句子才启用独立残片删除，降低误删符号的风险。
    if word_count < 4:
        return cleaned
    cleaned = re.sub(r"(?<![A-Za-z0-9_])[DRFT](?![A-Za-z0-9_])", " ", cleaned)  # 删除独立 D/R/F/T，保留常见正文冠词 A。
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def _clean_embedded_draft_letter_word(match: re.Match[str]) -> str:
    """Remove one leaked DRAFT letter from a long mixed-case word."""

    word = match.group(0)  # 取出包含疑似水印字母的完整单词。
    if len(word) < 4:
        return word
    for index, character in enumerate(word):
        if character not in "DRAFT":
            continue
        previous_character = word[index - 1] if index > 0 else ""
        next_character = word[index + 1] if index + 1 < len(word) else ""
        if not previous_character.islower() or not next_character.islower():
            continue
        candidate = word[:index] + word[index + 1 :]
        if candidate.casefold() in _DRAFT_FRAGMENT_CORRECTION_WORDS:
            return candidate  # 例如 trRansmitter -> transmitter，但 laneTraining 不会被改写。
    return word


def truncate(text: str, max_chars: int = 260) -> str:
    """Trim a report snippet while preserving the most useful prefix."""

    value = compact_inline(text)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def canonicalize_number_word_token(token: str) -> str | None:
    """Return the digit string for one compact English cardinal number token.

    This intentionally covers only compact protocol-review cases such as
    ``seven``/``7`` or ``twenty-one``/``21``. Context-sensitive callers should
    use ``canonicalize_number_word_tokens`` so identifiers like ``Gen seven``
    or ``report seven.pdf`` can stay protected.
    """

    normalized = token.casefold().replace("-", "").replace("‐", "").replace("‑", "")
    value = _compact_number_word_value(normalized)
    return str(value) if value is not None else None


def parse_number_word_phrase(tokens: list[str], start_index: int) -> tuple[str, int] | None:
    """Parse a short English cardinal phrase from a token stream.

    Returns ``(canonical_digit_string, consumed_token_count)``. The parser is
    deliberately small: it covers common protocol counts from zero through 999,
    including ``twenty one`` and ``one hundred and five``, while ignoring
    ordinals and domain-specific identifiers.
    """

    if start_index >= len(tokens):
        return None
    normalized = [_normalize_number_word_token(token) for token in tokens]
    first = normalized[start_index]

    if first in _NUMBER_WORD_UNITS and _NUMBER_WORD_UNITS[first] > 0:
        next_index = start_index + 1
        if next_index < len(normalized) and normalized[next_index] == "hundred":
            value = _NUMBER_WORD_UNITS[first] * 100
            consumed = 2
            tail_index = start_index + consumed
            if tail_index < len(normalized) and normalized[tail_index] == "and":
                tail_index += 1
                consumed += 1
            tail = _parse_under_hundred(normalized, tail_index)
            if tail:
                tail_value, tail_consumed = tail
                value += tail_value
                consumed += tail_consumed
            return str(value), consumed

    under_hundred = _parse_under_hundred(normalized, start_index)
    if under_hundred:
        value, consumed = under_hundred
        return str(value), consumed
    compact_value = _compact_number_word_value(first)
    if compact_value is not None:
        return str(compact_value), 1
    return None


def canonicalize_number_word_tokens(
    tokens: list[str],
    *,
    protected_previous_words: frozenset[str] = frozenset(),
    protected_next_words: frozenset[str] = frozenset(),
) -> list[str]:
    """Canonicalize English cardinal phrases inside a token stream.

    ``protected_previous_words`` and ``protected_next_words`` let callers avoid
    rewriting identifier-like contexts. For example, ``seven waveforms`` can
    compare equal to ``7 waveforms``, while ``Gen seven`` and
    ``report seven.pdf`` remain visible differences.
    """

    canonical: list[str] = []
    index = 0
    normalized_tokens = [_normalize_number_word_token(token) for token in tokens]
    while index < len(tokens):
        parsed = parse_number_word_phrase(tokens, index)
        if parsed:
            value, consumed = parsed
            previous_word = normalized_tokens[index - 1] if index > 0 else ""
            next_index = index + consumed
            next_word = normalized_tokens[next_index] if next_index < len(tokens) else ""
            if previous_word in protected_previous_words or next_word in protected_next_words:
                canonical.extend(tokens[index : index + consumed])
            else:
                canonical.append(value)
            index += consumed
            continue
        canonical.append(tokens[index])
        index += 1
    return canonical


def canonicalize_chinese_number_token(token: str) -> str | None:
    """Return the digit string for one Chinese cardinal number token.

    The parser is intentionally limited to ordinary counts used in protocol
    prose, for example ``七``/``7`` or ``一百零五``/``105``. It is not a general
    Chinese NLP parser; callers should apply it only in numeric contexts such as
    counts, units, or section ordinals.
    """

    normalized = normalize_line(token)
    if not normalized or any(char not in CHINESE_NUMBER_CHARS for char in normalized):
        return None
    value = _parse_chinese_number(normalized)
    return str(value) if value is not None else None


def canonicalize_chinese_number_expressions(text: str) -> str:
    """Canonicalize Chinese count expressions that are safely numeric.

    Chinese text has no mandatory word spaces, so a bare ``七`` inside a word is
    ambiguous. This function only rewrites numerals when a following measure
    word or an ordinal marker makes the numeric meaning clear: ``七个`` becomes
    ``7个`` and ``第七章`` becomes ``第 7 章``. That keeps common protocol counts
    quiet without erasing words such as ``一体化``.
    """

    def replace_ordinal(match: re.Match[str]) -> str:
        value = canonicalize_chinese_number_token(match.group(1))
        if value is None:
            return match.group(0)
        return f"第 {value} {match.group(2)}"

    def replace_count(match: re.Match[str]) -> str:
        value = canonicalize_chinese_number_token(match.group(1))
        return value if value is not None else match.group(0)

    value = _CHINESE_ORDINAL_NUMBER_RE.sub(replace_ordinal, text)
    return _CHINESE_CONTEXT_NUMBER_RE.sub(replace_count, value)


def _normalize_number_word_token(token: str) -> str:
    """Normalize a candidate number-word token for parsing only."""

    return token.casefold().replace("-", "").replace("‐", "").replace("‑", "")


def _compact_number_word_value(normalized: str) -> int | None:
    """Return a value for a compact cardinal token, if it is one."""

    if normalized in _NUMBER_WORD_UNITS:
        return _NUMBER_WORD_UNITS[normalized]
    if normalized in _NUMBER_WORD_TENS:
        return _NUMBER_WORD_TENS[normalized]
    for tens_word, tens_value in _NUMBER_WORD_TENS.items():
        if not normalized.startswith(tens_word):
            continue
        unit_word = normalized[len(tens_word) :]
        unit_value = _NUMBER_WORD_UNITS.get(unit_word)
        if unit_value and unit_value < 10:
            return tens_value + unit_value
    for unit_word, unit_value in _NUMBER_WORD_UNITS.items():
        if unit_value <= 0:
            continue
        prefix = f"{unit_word}hundred"
        if not normalized.startswith(prefix):
            continue
        tail = normalized[len(prefix) :]
        if not tail:
            return unit_value * 100
        if tail.startswith("and"):
            tail = tail[3:]
        tail_value = _compact_number_word_value(tail)
        if tail_value is not None and tail_value < 100:
            return unit_value * 100 + tail_value
    return None


def _parse_under_hundred(tokens: list[str], start_index: int) -> tuple[int, int] | None:
    """Parse a cardinal value below 100 from normalized tokens."""

    if start_index >= len(tokens):
        return None
    token = tokens[start_index]
    if token in _NUMBER_WORD_TENS and start_index + 1 < len(tokens):
        next_value = _NUMBER_WORD_UNITS.get(tokens[start_index + 1])
        if next_value and next_value < 10:
            return _NUMBER_WORD_TENS[token] + next_value, 2
    compact_value = _compact_number_word_value(token)
    if compact_value is not None and compact_value < 100:
        return compact_value, 1
    return None


def _parse_chinese_number(token: str) -> int | None:
    """Parse a compact Chinese cardinal number used in protocol counts."""

    if not token:
        return None
    if not any(char in _CHINESE_UNITS or char == "万" for char in token):
        digits: list[str] = []
        for char in token:
            digit = _CHINESE_DIGITS.get(char)
            if digit is None:
                return None
            digits.append(str(digit))
        return int("".join(digits))

    total = 0
    section = 0
    number = 0
    for char in token:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            continue
        if char in _CHINESE_UNITS:
            unit = _CHINESE_UNITS[char]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
            continue
        if char == "万":
            section += number
            if section == 0:
                section = 1
            total += section * 10000
            section = 0
            number = 0
            continue
        return None
    return total + section + number
