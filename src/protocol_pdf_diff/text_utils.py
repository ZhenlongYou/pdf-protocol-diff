"""Text normalization utilities shared by sectioning and diffing.

PDF extraction often inserts arbitrary line breaks and spacing. These helpers
apply conservative cleanup only: they avoid aggressive rewriting so the final
report still resembles the source protocol text a reviewer will see on screen.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

TABLE_NUMBER_DASH_CLASS = r"[\-\u2010\u2011\u2012\u2013\u2014\u2212]"

_WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_INVISIBLE_EXTRACTION_CONTROL_RE = re.compile(
    "[\u00ad\u200b\u2060\ufeff]"
)
_KNOWN_ADOBE_SYMBOL_PUA = str.maketrans(
    {
        "\uf028": "(",
        "\uf029": ")",
        "\uf02a": "*",
        "\uf02b": "+",
        "\uf02c": ",",
        "\uf02d": "−",
        "\uf02f": "/",
        "\uf03d": "=",
        "\uf03c": "<",
        "\uf03e": ">",
        "\uf061": "α",
        "\uf067": "γ",
        "\uf073": "σ",
        "\uf074": "τ",
        "\uf0a3": "≤",
        "\uf0a4": "⁄",
    }
)  # OIF PDFs use legacy Adobe Symbol code positions for basic formula punctuation/operators, alpha/gamma/sigma/tau, <=, and fraction slash.
_KNOWN_ENGINEERING_SYMBOL_LETTER_SUFFIXES = {
    "A": frozenset({"fe", "ne", "v"}),
    "C": frozenset({"p"}),
    "N": frozenset({"b", "ts"}),
    "R": frozenset({"d"}),
    "RL": frozenset({"cd"}),
    "Z": frozenset({"c", "p"}),
    "f": frozenset({"b"}),
    "z": frozenset({"c", "p"}),
}
_MEASUREMENT_CONTEXT_WORDS = frozenset(
    {
        "amplitude",
        "bandwidth",
        "capacitance",
        "current",
        "delay",
        "distance",
        "duration",
        "energy",
        "frequency",
        "height",
        "impedance",
        "inductance",
        "interval",
        "length",
        "limit",
        "mass",
        "maximum",
        "minimum",
        "noise",
        "power",
        "pressure",
        "rate",
        "resistance",
        "temperature",
        "time",
        "tolerance",
        "unit",
        "units",
        "voltage",
        "weight",
        "width",
    }
)


def normalize_table_number_dashes(value: str) -> str:
    """Normalize one supported table-number separator and its surrounding spaces."""

    return re.sub(
        rf"\s*{TABLE_NUMBER_DASH_CLASS}\s*",
        "-",
        value,
    )
_MEASUREMENT_CONTEXT_CJK = (
    "单位",
    "上限",
    "下限",
    "电压",
    "电流",
    "功率",
    "频率",
    "时间",
    "时延",
    "延迟",
    "长度",
    "距离",
    "温度",
    "电阻",
    "阻抗",
    "电容",
    "电感",
)
_COMPACT_MEASUREMENT_UNITS = frozenset(
    {
        "a",
        "ah",
        "b",
        "bps",
        "c",
        "db",
        "dbc",
        "f",
        "g",
        "gsym",
        "gt",
        "h",
        "hz",
        "j",
        "k",
        "kg",
        "m",
        "pa",
        "s",
        "sym",
        "ui",
        "v",
        "va",
        "var",
        "w",
        "wb",
        "ω",
        "ohm",
    }
)
_SI_UNIT_BASES = frozenset(
    {"a", "bps", "c", "f", "g", "h", "hz", "j", "k", "m", "pa", "s", "sym", "v", "w"}
)
_SI_PREFIXES = frozenset("yzafpnumcdhkMGTPEZY")
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
_NUMBER_WORD_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}
_ENGLISH_COUNT_CONTEXT_NOUNS = frozenset(
    {
        "attempt",
        "attempts",
        "bit",
        "bits",
        "byte",
        "bytes",
        "channel",
        "channels",
        "cycle",
        "cycles",
        "day",
        "days",
        "document",
        "documents",
        "error",
        "errors",
        "event",
        "events",
        "failure",
        "failures",
        "finding",
        "findings",
        "file",
        "files",
        "issue",
        "issues",
        "interval",
        "intervals",
        "item",
        "items",
        "lane",
        "lanes",
        "packet",
        "packets",
        "page",
        "pages",
        "pin",
        "pins",
        "port",
        "ports",
        "record",
        "records",
        "requirement",
        "requirements",
        "retry",
        "retries",
        "sample",
        "samples",
        "section",
        "sections",
        "step",
        "steps",
        "test",
        "tests",
        "time",
        "times",
        "transaction",
        "transactions",
        "update",
        "updates",
        "value",
        "values",
        "warning",
        "warnings",
        "waveform",
        "waveforms",
    }
)
_ENGLISH_CARDINAL_LIST_COMMA_SENTINEL = "pdfdiffcountlistcomma"
_ENGLISH_CARDINAL_LIST_WORD_PATTERN = "(?:" + "|".join(
    sorted(
        {
            *_NUMBER_WORD_UNITS,
            *_NUMBER_WORD_TENS,
            *_NUMBER_WORD_SCALES,
            "a",
            "and",
            "half",
        },
        key=len,
        reverse=True,
    )
) + ")"
_ENGLISH_CARDINAL_LIST_DIGIT_PATTERN = (
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)
_ENGLISH_CARDINAL_LIST_SCALE_PATTERN = "(?:" + "|".join(
    sorted(_NUMBER_WORD_SCALES, key=len, reverse=True)
) + ")"
_ENGLISH_CARDINAL_LIST_SCALED_DIGIT_PATTERN = (
    rf"{_ENGLISH_CARDINAL_LIST_DIGIT_PATTERN}"
    rf"(?:\s+{_ENGLISH_CARDINAL_LIST_SCALE_PATTERN})*"
)
_ENGLISH_CARDINAL_LIST_PHRASE_PATTERN = (
    rf"(?:{_ENGLISH_CARDINAL_LIST_SCALED_DIGIT_PATTERN}|"
    rf"{_ENGLISH_CARDINAL_LIST_WORD_PATTERN}"
    rf"(?:[\s-]+{_ENGLISH_CARDINAL_LIST_WORD_PATTERN})*)"
)
_ENGLISH_CARDINAL_LIST_DIGIT_RE = re.compile(
    rf"^{_ENGLISH_CARDINAL_LIST_SCALED_DIGIT_PATTERN}$"
)
_ENGLISH_CARDINAL_LIST_COMMA_RE = re.compile(
    r"(?i)(?<![\w-])"
    rf"(?P<left>{_ENGLISH_CARDINAL_LIST_PHRASE_PATTERN})"
    r"\s*,\s+"
    rf"(?=(?P<right>{_ENGLISH_CARDINAL_LIST_PHRASE_PATTERN})(?![\w-]))"
)
_ENGLISH_CARDINAL_LIST_CONNECTOR_COMMA_RE = re.compile(
    r"(?i)(?<![\w-])"
    rf"(?P<left>{_ENGLISH_CARDINAL_LIST_PHRASE_PATTERN})"
    r"\s*,\s+"
    r"(?=(?:and|or)\s+"
    rf"(?P<right>{_ENGLISH_CARDINAL_LIST_PHRASE_PATTERN})(?![\w-]))"
)
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

    normalized = unicodedata.normalize("NFC", line)  # 保留 ²/₁ 等上下标语义，禁止兼容归一把它们压成普通数字。
    normalized = _INVISIBLE_EXTRACTION_CONTROL_RE.sub("", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def normalize_for_similarity(text: str) -> str:
    """Normalize text for similarity scoring.

    This function deliberately keeps punctuation and digits because protocol
    deltas often hide in numbers, dates, voltage/current limits, or option names.
    """

    lines = [normalize_line(line) for line in text.splitlines()]
    # 私用码位没有字体来源就没有可证明语义；相似度层也保留它，避免把自定义字体静默等同为 Unicode。
    compact = "\n".join(line for line in lines if line)
    compact = _MULTI_BLANK_RE.sub("\n\n", compact)
    return compact.casefold()


def compact_inline(text: str) -> str:
    """Make a short single-line snippet for reports and CSV fields."""

    return _WHITESPACE_RE.sub(" ", " ".join(text.split())).strip()


def readable_symbol_font_glyphs(text: str) -> str:
    """Decode a small legacy Symbol-code whitelist for reader-facing text only.

    Raw extraction remains untouched for JSON audit and character-conservation
    checks.  This convenience mapping never authorizes semantic equality: a
    private-use code point remains a reviewable difference until extraction
    carries verified font provenance.
    """

    return text.translate(_KNOWN_ADOBE_SYMBOL_PUA)


def reader_safe_glyphs(text: str) -> str:
    """Render every private-use glyph intelligibly in reader-facing formats.

    The verified Adobe Symbol whitelist is decoded first. Any remaining PUA
    code point has no trustworthy meaning without embedded-font provenance, so
    reader formats name the observed code point instead of emitting a tofu box
    or a misleading glyph. Audit formats still retain the untouched extraction.
    """

    decoded = readable_symbol_font_glyphs(text)
    return re.sub(
        r"[\ue000-\uf8ff]",
        lambda match: f"〔未识别符号 U+{ord(match.group(0)):04X}〕",
        decoded,
    )


def reader_symbol_mapping_key(text: str) -> str:
    """Build a narrow reader-equivalence key for known Symbol-font mappings.

    Only spacing adjacent to the two operators in the verified mapping table is
    ignored.  Word spacing and all other characters stay exact so a real wording
    change cannot be downgraded to an encoding review.
    """

    decoded = readable_symbol_font_glyphs(text)
    return re.sub(r"\s*([<≤])\s*", r"\1", decoded)


def is_known_engineering_symbol_letter_suffix(base: str, suffix: str) -> bool:
    """Return whether a split letter suffix belongs to a verified symbol family."""

    return suffix in _KNOWN_ENGINEERING_SYMBOL_LETTER_SUFFIXES.get(base, frozenset())


def has_measurement_context(value: str) -> bool:
    """Return True only for visible physical-quantity or unit context."""

    normalized = normalize_line(value).casefold()
    words = set(re.findall(r"[a-z]+", normalized))
    return bool(words & _MEASUREMENT_CONTEXT_WORDS) or any(
        marker in normalized for marker in _MEASUREMENT_CONTEXT_CJK
    )


def has_observable_identifier_boundary(
    value: str,
    boundary_index: int,
    *,
    context: str = "",
) -> bool:
    """Keep a digit/letter join unless positive evidence proves number+unit spacing."""

    if boundary_index <= 0 or boundary_index >= len(value):
        return False
    left = value[boundary_index - 1]
    right = value[boundary_index]
    left_is_identifier_letter = left.isalpha() and left.lower() != left.upper()
    right_is_identifier_letter = right.isalpha() and right.lower() != right.upper()
    if not (
        (left.isdigit() and right_is_identifier_letter)
        or (left_is_identifier_letter and right.isdigit())
    ):
        return False

    token_start = boundary_index - 1
    while token_start > 0 and (value[token_start - 1].isalnum() or value[token_start - 1] == "_"):
        token_start -= 1
    token_end = boundary_index + 1
    while token_end < len(value) and (value[token_end].isalnum() or value[token_end] == "_"):
        token_end += 1
    token = value[token_start:token_end]
    if re.fullmatch(r"(?i)[+-]?\d+(?:\.\d+)?e[+-]?\d+", token):
        return False  # Scientific notation is one numeric token, not an identifier join.
    if re.fullmatch(r"(?i)(?:notes?|tests?|sections?|tables?|figures?)\d+", token):
        return False  # Compact cross-reference labels and their spaced forms are display variants.
    if re.fullmatch(r"(?i)0x[0-9a-f]+", token):
        return True  # Hexadecimal spelling is literal and cannot be split safely.
    if re.fullmatch(r"(?i)\d+x(?:\d|[^\W\d_])\w*", token):
        return False  # Existing math rules treat 2xT and 5x10 as multiplication notation.

    compact_measurement = re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?P<unit>[^\W\d_][^\W\d_]*)",
        token,
        flags=re.UNICODE,
    )
    observed_context = f"{value[:token_start]} {context}"
    if compact_measurement and has_measurement_context(observed_context):
        unit = compact_measurement.group("unit").replace("µ", "u").replace("μ", "u")
        if _looks_like_measurement_unit(unit):
            return False
    if compact_measurement:
        unit = compact_measurement.group("unit").replace("µ", "u").replace("μ", "u")
        if len(unit) > 1 and _looks_like_measurement_unit(unit):
            return False  # Multi-letter SI/unit spellings such as ps, MHz and dB are self-evident.
    return True


def identifier_boundary_signatures(value: str, *, context: str = "") -> tuple[str, ...]:
    """Return position-bound signatures for observable joined digit/letter tokens."""

    signatures: list[str] = []
    for index in range(1, len(value)):
        if not has_observable_identifier_boundary(value, index, context=context):
            continue
        signatures.append(
            f"{len(signatures)}:{value[index - 1].isdigit()}>{value[index].isdigit()}"
        )
    return tuple(signatures)


def micro_identifier_signatures(value: str, *, context: str = "") -> tuple[str, ...]:
    """Preserve micro/mu glyphs in identifiers while allowing proven micro-units."""

    signatures: list[str] = []
    for match in re.finditer(r"(?<!\w)(?P<token>[\w]*[µμ][\w]*)(?!\w)", value):
        token = match.group("token")
        suffix_match = re.fullmatch(r"(?:\d+(?:\.\d+)?)?[µμ](?P<unit>\w+)", token)
        observed_context = f"{value[:match.start()]} {context}"
        if suffix_match:
            unit = "u" + suffix_match.group("unit")
            formula_or_measurement_context = (
                has_measurement_context(observed_context)
                or "=" in observed_context
            )
            if formula_or_measurement_context and _looks_like_measurement_unit(unit):
                continue
        signatures.append(f"{len(signatures)}:{token.replace('µ', 'μ')}")
    return tuple(signatures)


def _looks_like_measurement_unit(value: str) -> bool:
    """Recognize standard compact unit symbols without inferring document vocabulary."""

    normalized = value.replace("µ", "u").replace("μ", "u")
    if normalized.casefold() in _COMPACT_MEASUREMENT_UNITS:
        return True
    if len(normalized) < 2 or normalized[0] not in _SI_PREFIXES:
        return False
    return normalized[1:].casefold() in _SI_UNIT_BASES


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


def canonicalize_numeric_scale_chain(number: str, scales: list[str]) -> str | None:
    """Return an exact product for one numeric value and a strict scale chain."""

    normalized_scales = [_normalize_number_word_token(scale) for scale in scales]
    scale_values = [_NUMBER_WORD_SCALES.get(scale) for scale in normalized_scales]
    if not scale_values or any(value is None for value in scale_values):
        return None
    concrete_scales = [value for value in scale_values if value is not None]
    if not all(
        left < right for left, right in zip(concrete_scales, concrete_scales[1:])
    ):
        return None
    try:
        product = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return None
    for scale in concrete_scales:
        product *= scale
    fixed = format(product, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


def parse_number_word_phrase(tokens: list[str], start_index: int) -> tuple[str, int] | None:
    """Parse a short English cardinal phrase from a token stream.

    Returns ``(canonical_digit_string, consumed_token_count)``. The parser
    accepts composable cardinal scales through billions, including ``twenty
    one``, ``one hundred and five`` and ``two thousand five hundred``. It still
    ignores ordinals and domain-specific identifiers.
    """

    if start_index >= len(tokens):
        return None
    normalized = [_normalize_number_word_token(token) for token in tokens]
    if (
        normalized[start_index : start_index + 2] == ["half", "a"]
        and start_index + 2 < len(normalized)
        and normalized[start_index + 2] in _NUMBER_WORD_SCALES
    ):
        scale_end = start_index + 2
        while (
            scale_end < len(normalized)
            and normalized[scale_end] in _NUMBER_WORD_SCALES
        ):
            scale_end += 1
        scaled = canonicalize_numeric_scale_chain(
            "0.5",
            normalized[start_index + 2 : scale_end],
        )
        if scaled is not None:
            return scaled, scale_end - start_index
    for half_index in range(start_index + 1, len(normalized) - 3):
        if normalized[half_index : half_index + 3] != ["and", "a", "half"]:
            continue
        first_scale_index = half_index + 3
        if normalized[first_scale_index] not in _NUMBER_WORD_SCALES:
            continue
        scale_end = first_scale_index
        while (
            scale_end < len(normalized)
            and normalized[scale_end] in _NUMBER_WORD_SCALES
        ):
            scale_end += 1
        target_scale = _NUMBER_WORD_SCALES[normalized[first_scale_index]]
        prefix_scales = [
            _NUMBER_WORD_SCALES[token]
            for token in normalized[start_index:half_index]
            if token in _NUMBER_WORD_SCALES
        ]
        if any(scale >= target_scale for scale in prefix_scales):
            continue  # Repeated/descending scale chains are not one atomic count.
        prefix = parse_number_word_phrase(tokens[start_index:half_index], 0)
        if prefix is None or prefix[1] != half_index - start_index:
            continue
        scaled_chain = canonicalize_numeric_scale_chain(
            f"{prefix[0]}.5",
            normalized[first_scale_index:scale_end],
        )
        if scaled_chain is not None:
            return scaled_chain, scale_end - start_index
    first_scale = next(
        (
            index
            for index in range(start_index, len(normalized))
            if normalized[index] in _NUMBER_WORD_SCALES
        ),
        -1,
    )
    scale_end = first_scale
    while (
        scale_end >= 0
        and scale_end < len(normalized)
        and normalized[scale_end] in _NUMBER_WORD_SCALES
    ):
        scale_end += 1
    chain_is_terminal = first_scale >= 0
    if first_scale >= 0 and scale_end < len(normalized):
        next_token = normalized[scale_end]
        if (
            next_token in _NUMBER_WORD_UNITS
            or next_token in _NUMBER_WORD_TENS
            or next_token in {"a", "half"}
        ):
            chain_is_terminal = False
        elif next_token == "and":
            final_scale = _NUMBER_WORD_SCALES[normalized[scale_end - 1]]
            following_scales = [
                _NUMBER_WORD_SCALES[token]
                for token in normalized[scale_end + 1 :]
                if token in _NUMBER_WORD_SCALES
            ]
            chain_is_terminal = any(
                scale >= final_scale for scale in following_scales
            )
    if (
        first_scale > start_index
        and scale_end - first_scale >= 2
        and chain_is_terminal
    ):
        multiplier = parse_number_word_phrase(tokens[start_index:first_scale], 0)
        if multiplier is not None and multiplier[1] == first_scale - start_index:
            scaled = canonicalize_numeric_scale_chain(
                multiplier[0],
                normalized[first_scale:scale_end],
            )
            if scaled is not None:
                return scaled, scale_end - start_index
    total = 0
    consumed = 0
    last_scale = 1_000_000_001
    index = start_index
    while index < len(normalized):
        segment_index = index
        if normalized[index] == "and" and consumed:
            if _parse_under_thousand(normalized, index + 1) is None:
                break
            segment_index = index + 1
        segment = _parse_under_thousand(normalized, segment_index)
        if segment is None:
            break
        segment_value, segment_consumed = segment
        if segment_value == 0 and consumed:
            break
        scale_index = segment_index + segment_consumed
        if scale_index < len(normalized) and normalized[scale_index] in {
            "thousand",
            "million",
            "billion",
        }:
            scale = _NUMBER_WORD_SCALES[normalized[scale_index]]
            if scale >= last_scale:
                break
            total += segment_value * scale
            last_scale = scale
            index = scale_index + 1
            consumed = index - start_index
            continue
        total += segment_value
        consumed = scale_index - start_index
        break
    if not consumed:
        return None
    fraction_start = start_index + consumed
    if normalized[fraction_start : fraction_start + 3] == ["and", "a", "half"]:
        # ``total`` is an integer, so spell the half-unit exactly.  Floating
        # formatting (notably ``:g``) rounds large counts and can collapse
        # 1,000,000.5 and 1,000,001.5 into the same comparison key.
        return f"{total}.5", consumed + 3
    return str(total), consumed


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
        previous_word = normalized_tokens[index - 1] if index > 0 else ""
        if index + 1 < len(tokens):
            scale_end = index + 1
            while (
                scale_end < len(tokens)
                and normalized_tokens[scale_end] in _NUMBER_WORD_SCALES
            ):
                scale_end += 1
            scaled_digit = canonicalize_numeric_scale_chain(
                tokens[index],
                normalized_tokens[index + 1 : scale_end],
            )
            if (
                scaled_digit is not None
                and previous_word not in protected_previous_words
                and _has_positive_english_count_context(normalized_tokens, scale_end)
            ):
                canonical.append(scaled_digit)
                index = scale_end
                continue
        parsed = parse_number_word_phrase(tokens, index)
        if parsed:
            value, consumed = parsed
            next_index = index + consumed
            next_word = normalized_tokens[next_index] if next_index < len(tokens) else ""
            if (
                previous_word in protected_previous_words
                or next_word in protected_next_words
                or not _has_positive_english_count_context(normalized_tokens, next_index)
            ):
                canonical.extend(tokens[index : index + consumed])
            else:
                canonical.append(value)
            index += consumed
            continue
        canonical.append(tokens[index])
        index += 1
    return canonical


def mark_english_cardinal_list_commas(value: str) -> str:
    """Preserve commas that provably join cardinal alternatives.

    The normal review tokenizer intentionally drops prose commas.  A comma in
    ``one, two, or three failures`` is structural, however: without a marker,
    the first alternatives cannot inherit the final count noun.  Only comma +
    whitespace boundaries whose adjacent tokens are themselves complete
    cardinal atoms are marked; thousands separators and ordinary prose commas
    are untouched.
    """

    def is_complete_cardinal(phrase: str) -> bool:
        words = phrase.replace("-", " ").split()
        if _ENGLISH_CARDINAL_LIST_DIGIT_RE.fullmatch(phrase):
            scale_start = next(
                (
                    index
                    for index, word in enumerate(words)
                    if _normalize_number_word_token(word) in _NUMBER_WORD_SCALES
                ),
                len(words),
            )
            if scale_start == len(words):
                return True
            return canonicalize_numeric_scale_chain(
                words[0],
                words[scale_start:],
            ) is not None
        parsed = parse_number_word_phrase(words, 0)
        if parsed is None:
            return False
        value, consumed = parsed
        return consumed == len(words) or bool(
            consumed == len(words) - 1
            and words[-1] in _NUMBER_WORD_SCALES
            and value.endswith(".5")
        )

    def replace(match: re.Match[str]) -> str:
        left = match.group("left")
        right = match.group("right")
        left_number = is_complete_cardinal(left)
        right_number = is_complete_cardinal(right)
        if not (left_number and right_number):
            return match.group(0)
        return f"{left} {_ENGLISH_CARDINAL_LIST_COMMA_SENTINEL} "

    marked = _ENGLISH_CARDINAL_LIST_COMMA_RE.sub(replace, value)

    def remove_connector_comma(match: re.Match[str]) -> str:
        left = match.group("left")
        right = match.group("right")
        left_number = is_complete_cardinal(left)
        right_number = is_complete_cardinal(right)
        return f"{left} " if left_number and right_number else match.group(0)

    return _ENGLISH_CARDINAL_LIST_CONNECTOR_COMMA_RE.sub(
        remove_connector_comma,
        marked,
    )


def _has_positive_english_count_context(tokens: list[str], next_index: int) -> bool:
    """Require an observed count noun instead of guessing from a prefix blacklist."""

    if next_index < len(tokens) and tokens[next_index] in _ENGLISH_COUNT_CONTEXT_NOUNS:
        return True
    if next_index < len(tokens) and tokens[next_index] in {
        "and",
        "or",
        "to",
        "through",
        _ENGLISH_CARDINAL_LIST_COMMA_SENTINEL,
    }:
        consumed = _english_count_value_consumed(tokens, next_index + 1)
        if consumed is not None:
            return _has_positive_english_count_context(
                tokens,
                next_index + 1 + consumed,
            )
    return bool(
        next_index + 1 < len(tokens)
        and tokens[next_index] not in _NUMBER_WORD_SCALES
        and re.fullmatch(r"[a-z][a-z-]*", tokens[next_index])
        and tokens[next_index + 1] in _ENGLISH_COUNT_CONTEXT_NOUNS
    )  # `twenty one idle intervals` 允许一个可见修饰词；公式/函数/枚举不会误折叠。


def _english_count_value_consumed(tokens: list[str], start_index: int) -> int | None:
    """Return a fully observed count endpoint length after a list connector."""

    parsed = parse_number_word_phrase(tokens, start_index)
    if parsed is not None:
        return parsed[1]
    if start_index >= len(tokens) or not re.fullmatch(
        r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?",
        tokens[start_index],
    ):
        return None
    scale_end = start_index + 1
    while (
        scale_end < len(tokens)
        and _normalize_number_word_token(tokens[scale_end]) in _NUMBER_WORD_SCALES
    ):
        scale_end += 1
    if scale_end > start_index + 1:
        return (
            scale_end - start_index
            if canonicalize_numeric_scale_chain(
                tokens[start_index],
                tokens[start_index + 1 : scale_end],
            )
            is not None
            else None
        )
    return 1


def is_english_count_context_noun(token: str) -> bool:
    """Return whether an observed noun safely proves ordinary count context."""

    return _normalize_number_word_token(token) in _ENGLISH_COUNT_CONTEXT_NOUNS


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


def _parse_under_thousand(tokens: list[str], start_index: int) -> tuple[int, int] | None:
    """Parse one strict 0..999 segment without consuming a scale word."""

    if start_index >= len(tokens):
        return None
    first_value = _NUMBER_WORD_UNITS.get(tokens[start_index])
    if (
        first_value is not None
        and 0 < first_value < 10
        and start_index + 1 < len(tokens)
        and tokens[start_index + 1] == "hundred"
    ):
        value = first_value * 100
        consumed = 2
        tail_index = start_index + consumed
        if tail_index < len(tokens) and tokens[tail_index] == "and":
            tail = _parse_under_hundred(tokens, tail_index + 1)
            if tail is None or tail[0] == 0:
                return value, consumed
            tail_value, tail_consumed = tail
            tail_end = tail_index + 1 + tail_consumed
            if tail_end < len(tokens) and tokens[tail_end] == "hundred":
                # ``one hundred and two hundred`` is a coordinated list,
                # not the malformed scalar ``102 hundred``.  Leave the
                # connector for the count-context lookahead.
                return value, consumed
            return value + tail_value, consumed + 1 + tail_consumed
        tail = _parse_under_hundred(tokens, tail_index)
        if tail is not None and tail[0] > 0:
            tail_value, tail_consumed = tail
            return value + tail_value, consumed + tail_consumed
        return value, consumed
    under_hundred = _parse_under_hundred(tokens, start_index)
    if under_hundred is not None:
        return under_hundred
    compact_value = _compact_number_word_value(tokens[start_index])
    if compact_value is not None and compact_value < 1_000:
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
