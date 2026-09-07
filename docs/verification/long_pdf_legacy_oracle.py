"""Frozen pre-optimization oracle from db45c03f8d4bc0c15485349403449920f52bc736.

Only the changed functions are frozen; unchanged grammar/geometry primitives
are injected by bind(). This checks optimization equivalence, not independent
proof of the entire historical recognition algorithm.
"""
from __future__ import annotations
import types
from protocol_pdf_diff import pdf_extract, text_utils

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
        normalized[start_index] == "a"
        and start_index + 1 < len(normalized)
        and normalized[start_index + 1] in _NUMBER_WORD_SCALES
    ):
        # ``a hundred`` and ``a million`` are ordinary cardinal phrases.  Keep
        # the substitution local to a following scale so an article in prose
        # can never become a count on its own.
        normalized[start_index] = "one"
        return parse_number_word_phrase(normalized, start_index)
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
            following_scales: list[int] = []
            for token in normalized[scale_end + 1 :]:
                if token in _NUMBER_WORD_SCALES:
                    following_scales.append(_NUMBER_WORD_SCALES[token])
                    continue
                if (
                    token in _NUMBER_WORD_UNITS
                    or token in _NUMBER_WORD_TENS
                    or token in {"a", "half", "and"}
                ):
                    continue
                break
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
                or not (
                    (
                        previous_word in _ENGLISH_COUNT_CONTEXT_VERBS
                        and next_word not in _NUMBER_WORD_SCALES
                    )
                    or _has_positive_english_count_context(
                        normalized_tokens,
                        next_index,
                    )
                )
            ):
                canonical.extend(tokens[index : index + consumed])
            else:
                canonical.append(value)
            index += consumed
            continue
        canonical.append(tokens[index])
        index += 1
    return canonical

def _repair_body_visual_subscript_order(
    text: str,
    words: list[dict[str, float | str]],
) -> str:
    """Rejoin coordinate-proven body subscripts without changing other lines.

    pdfplumber can put smaller lowered text on the next extracted line even
    though it visually touches a base symbol.  The existing
    :func:`_words_form_visual_subscript` geometry is sufficient to recognize
    that relation; this repair only binds one-to-one relations whose physical
    source lines each have one unique occurrence in the extracted text.

    Replacements are made at those occurrence-bound lines, not by increasing
    the page-wide y tolerance.  Complete coordinate coverage and a final
    non-whitespace character multiset check make the rewrite lossless.  Any
    missing word, duplicate occurrence, ambiguous edge, or non-adjacent raw
    line therefore leaves the original text untouched.
    """

    raw_lines = [
        normalized
        for line in text.splitlines()
        if (normalized := normalize_line(line))
    ]
    if not raw_lines or len(words) < 2:
        return text

    observed_words: list[dict[str, object]] = []
    for word in words:
        try:
            observed_text = normalize_line(str(word.get("text", "")))
            x0 = float(word["x0"])
            x1 = float(word["x1"])
            top = float(word["top"])
            bottom = float(word["bottom"])
        except (KeyError, TypeError, ValueError):
            return text
        if not observed_text or x1 <= x0 or bottom <= top:
            return text
        observed_words.append(
            {
                "text": observed_text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
            }
        )

    raw_characters = _non_whitespace_character_counts("\n".join(raw_lines))
    coordinate_characters = _non_whitespace_character_counts(
        "".join(str(word["text"]) for word in observed_words)
    )
    if not raw_characters or raw_characters != coordinate_characters:
        return text  # 坐标词未完整覆盖比较文本时，不能用局部几何改写整页正文。

    candidate_edges = [
        (base_index, suffix_index)
        for base_index, base_word in enumerate(observed_words)
        for suffix_index, suffix_word in enumerate(observed_words)
        if base_index != suffix_index
        and _body_words_form_visual_subscript(base_word, suffix_word)
    ]
    if not candidate_edges:
        return text
    endpoint_degrees = Counter(
        index
        for edge in candidate_edges
        for index in edge
    )
    one_to_one_edges = [
        edge
        for edge in candidate_edges
        if all(endpoint_degrees[index] == 1 for index in edge)
    ]
    if not one_to_one_edges:
        return text

    indexed_lines = _indexed_visual_word_lines(observed_words)
    line_for_word = {
        word_index: line_index
        for line_index, line in enumerate(indexed_lines)
        for word_index in line
    }
    coordinate_line_texts = [
        _words_to_visual_line([observed_words[index] for index in line])
        for line in indexed_lines
    ]
    coordinate_occurrences: dict[str, list[int]] = {}
    raw_occurrences: dict[str, list[int]] = {}
    for line_index, line in enumerate(coordinate_line_texts):
        coordinate_occurrences.setdefault(_character_signature(line), []).append(
            line_index
        )
    for line_index, line in enumerate(raw_lines):
        raw_occurrences.setdefault(_character_signature(line), []).append(line_index)

    coordinate_to_raw: dict[int, int] = {}
    for signature, coordinate_indexes in coordinate_occurrences.items():
        raw_indexes = raw_occurrences.get(signature, [])
        if len(coordinate_indexes) == len(raw_indexes) == 1:
            coordinate_to_raw[coordinate_indexes[0]] = raw_indexes[0]

    # pdfplumber can emit lowered spans in the same raw text line (with an
    # artificial space) even though its coordinate words correctly put those
    # spans on the immediately lower visual line.  Bind that exact composite
    # row only when every word on the lower line is a one-to-one suffix and the
    # combined character sequence occurs once in the raw text.  This covers
    # ``J RMS`` / ``J4u 03`` without doing global text-shaped substitutions.
    inline_replacements: dict[int, str] = {}
    inline_edges: set[tuple[int, int]] = set()
    edges_by_line_pair: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for edge in one_to_one_edges:
        base_line = line_for_word.get(edge[0])
        suffix_line = line_for_word.get(edge[1])
        if (
            base_line is None
            or suffix_line is None
            or suffix_line != base_line + 1
        ):
            continue
        edges_by_line_pair.setdefault((base_line, suffix_line), []).append(edge)
    for (base_line, suffix_line), edges in edges_by_line_pair.items():
        suffix_indexes_for_pair = {suffix_index for _base_index, suffix_index in edges}
        if set(indexed_lines[suffix_line]) != suffix_indexes_for_pair:
            continue
        combined_indexes = sorted(
            [*indexed_lines[base_line], *indexed_lines[suffix_line]],
            key=lambda index: (
                float(observed_words[index]["x0"]),
                float(observed_words[index]["x1"]),
                float(observed_words[index]["top"]),
                str(observed_words[index]["text"]),
            ),
        )
        combined_text = _words_to_visual_line(
            [observed_words[index] for index in combined_indexes]
        )
        raw_indexes = raw_occurrences.get(_character_signature(combined_text), [])
        if len(raw_indexes) != 1 or raw_indexes[0] in inline_replacements:
            continue
        rebuilt = _rebuild_body_subscript_visual_line(
            combined_indexes,
            observed_words,
            edges,
        )
        if _character_signature(rebuilt) != _character_signature(combined_text):
            continue
        inline_replacements[raw_indexes[0]] = rebuilt
        inline_edges.update(edges)

    accepted_edges: list[tuple[int, int]] = []
    for base_index, suffix_index in one_to_one_edges:
        if (base_index, suffix_index) in inline_edges:
            continue
        base_line = line_for_word.get(base_index)
        suffix_line = line_for_word.get(suffix_index)
        if base_line is None or suffix_line is None:
            continue
        base_raw_line = coordinate_to_raw.get(base_line)
        suffix_raw_line = coordinate_to_raw.get(suffix_line)
        if (
            suffix_raw_line is None
            and base_raw_line is not None
            and suffix_line != base_line
        ):
            adjacent_raw_line = base_raw_line + 1
            suffix_signature = _character_signature(
                coordinate_line_texts[suffix_line]
            )
            if (
                adjacent_raw_line < len(raw_lines)
                and _character_signature(raw_lines[adjacent_raw_line])
                == suffix_signature
                and adjacent_raw_line not in coordinate_to_raw.values()
            ):
                coordinate_to_raw[suffix_line] = adjacent_raw_line
                suffix_raw_line = adjacent_raw_line
                # 重复 suffix 只凭“唯一基行的紧邻原始行”绑定；重复基行或映射冲突仍 fail-closed。
        if base_raw_line is None or suffix_raw_line is None:
            continue
        if suffix_raw_line not in {base_raw_line, base_raw_line + 1}:
            continue  # 跨越普通正文行的较小文字不是可安全重排的相邻视觉下标。
        accepted_edges.append((base_index, suffix_index))
    if not accepted_edges and not inline_replacements:
        return text

    suffix_indexes = {suffix_index for _base_index, suffix_index in accepted_edges}
    suffix_for_base = dict(accepted_edges)
    touched_coordinate_lines = {
        line_for_word[index]
        for edge in accepted_edges
        for index in edge
    }
    replacements: dict[int, str] = dict(inline_replacements)
    for coordinate_line_index in touched_coordinate_lines:
        rebuilt_words: list[dict[str, object]] = []
        for word_index in indexed_lines[coordinate_line_index]:
            if word_index in suffix_indexes:
                continue
            word = dict(observed_words[word_index])
            suffix_index = suffix_for_base.get(word_index)
            if suffix_index is not None:
                suffix = observed_words[suffix_index]
                word["text"] = f'{word["text"]}{suffix["text"]}'
                word["x0"] = min(float(word["x0"]), float(suffix["x0"]))
                word["x1"] = max(float(word["x1"]), float(suffix["x1"]))
                word["top"] = min(float(word["top"]), float(suffix["top"]))
                word["bottom"] = max(
                    float(word["bottom"]),
                    float(suffix["bottom"]),
                )
            rebuilt_words.append(word)
        raw_line_index = coordinate_to_raw[coordinate_line_index]
        if raw_line_index in replacements:
            continue
        replacements[raw_line_index] = (
            _words_to_visual_line(rebuilt_words) if rebuilt_words else ""
        )

    repaired_lines = [
        replacements.get(line_index, line)
        for line_index, line in enumerate(raw_lines)
    ]
    repaired = "\n".join(line for line in repaired_lines if line)
    if _non_whitespace_character_counts(repaired) != raw_characters:
        return text
    return repaired

def bind():
    number_namespace = dict(vars(text_utils))
    number_namespace["parse_number_word_phrase"] = types.FunctionType(
        parse_number_word_phrase.__code__, number_namespace)
    number_namespace["canonicalize_number_word_tokens"] = types.FunctionType(
        canonicalize_number_word_tokens.__code__, number_namespace,
        argdefs=canonicalize_number_word_tokens.__defaults__)
    number_namespace["canonicalize_number_word_tokens"].__kwdefaults__ = canonicalize_number_word_tokens.__kwdefaults__
    geometry = types.FunctionType(_repair_body_visual_subscript_order.__code__, dict(vars(pdf_extract)))
    return number_namespace["parse_number_word_phrase"], number_namespace["canonicalize_number_word_tokens"], geometry


def _review_units_share_sentence_skeleton(left: str, right: str) -> bool:
    """Require local lexical continuity without treating a technical edit as disjoint prose."""

    if _review_unit_key(left) == _review_unit_key(right):
        return True
    if (
        _standalone_table_reference_number(left)
        and _standalone_table_reference_number(right)
    ):
        return True  # 完整表引用是通用结构定位语法，编号变化不应拆散同一章节。
    if _unit_has_unproven_label_syntax(left) or _unit_has_unproven_label_syntax(right):
        return False  # 无 schema provenance 时，冒号标签不猜测字段身份。
    if max(_review_similarity(left, right), _similarity(left, right)) >= 0.90:
        return True  # 短数值、状态词或大小写技术标识符变化仍是同一句。
    left_field = _assignment_field_key(left)
    right_field = _assignment_field_key(right)
    if left_field and left_field == right_field:
        return True  # 显式 `=` 左值稳定时，值槽的不透明技术枚举仍是同一条修改。
    left_words = _meaningful_review_words(left)
    right_words = _meaningful_review_words(right)
    if not left_words or not right_words:
        return False
    shared_words = left_words & right_words
    return len(shared_words) >= 2 and len(shared_words) / min(
        len(left_words), len(right_words)
    ) >= 0.60

def skeleton_oracle():
    from protocol_pdf_diff import compare
    return types.FunctionType(_review_units_share_sentence_skeleton.__code__, dict(vars(compare)))


def _match_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
    *,
    suppressed_old_table_unit_keys: set[str] | None = None,
    suppressed_new_table_unit_keys: set[str] | None = None,
) -> list[tuple[int | None, int | None, float, str]]:
    """Pair old/new sections using exact keys first, then global best scores.

    The second pass builds all viable fallback candidates and assigns the
    strongest pairs first. That is more conservative than matching each new
    section greedily in document order, especially when protocols contain many
    repeated boilerplate clauses.
    """

    exact_old_by_key: dict[str, list[int]] = {}
    for old_index, old_section in enumerate(old_sections):
        exact_old_by_key.setdefault(old_section.identity_key, []).append(old_index)
    exact_new_by_key: dict[str, list[int]] = {}
    for new_index, new_section in enumerate(new_sections):
        exact_new_by_key.setdefault(new_section.identity_key, []).append(new_index)

    matched_old: set[int] = set()
    matched_new: set[int] = set()
    rejected_exact_pairs: set[tuple[int, int]] = set()
    matches: list[tuple[int | None, int | None, float, str]] = []

    exact_candidates: list[tuple[float, int, int]] = []
    for identity_key, old_indexes in exact_old_by_key.items():
        for old_index in old_indexes:
            for new_index in exact_new_by_key.get(identity_key, []):
                old_section = old_sections[old_index]
                new_section = new_sections[new_index]
                body_similarity = _exact_identity_similarity(
                    old_section.body,
                    new_section.body,
                )
                if body_similarity is None:
                    rejected_exact_pairs.add((old_index, new_index))
                    continue
                if (
                    old_section.body.strip()
                    and new_section.body.strip()
                    and body_similarity < options.min_section_match_similarity
                ):
                    continue  # 同号同题也不能让长标题淹没两段完全无关的实际正文。
                similarity = max(
                    _section_similarity(
                        old_section.comparable_text,
                        new_section.comparable_text,
                    ),
                    body_similarity,
                )
                if similarity >= options.min_section_match_similarity:
                    exact_candidates.append((similarity, old_index, new_index))
                # 相同编号并不保证是同一条款：插入新条款会占用旧编号并整体后移。
                # 即使标题未变，也必须由实际可比文本达到用户阈值；证据不足时保守显示新增/删除。

    for similarity, old_index, new_index in sorted(
        exact_candidates,
        key=lambda item: (-item[0], abs(item[1] - item[2]), item[2], item[1]),
    ):
        if old_index in matched_old or new_index in matched_new:
            continue
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append((old_index, new_index, similarity, "similarity_exact"))

    old_table_unit_keys = suppressed_old_table_unit_keys or set()
    new_table_unit_keys = suppressed_new_table_unit_keys or set()
    old_title_counts = Counter(
        _review_unit_key(section.title) for section in old_sections
    )
    new_title_counts = Counter(
        _review_unit_key(section.title) for section in new_sections
    )
    fallback_candidates: list[tuple[float, int, int, str]] = []
    late_fallback_candidates: list[tuple[float, int, int, str]] = []
    for new_index, new_section in enumerate(new_sections):
        if new_index in matched_new:
            continue
        for old_index, old_section in enumerate(old_sections):
            if old_index in matched_old:
                continue
            if (old_index, new_index) in rejected_exact_pairs:
                continue
            raw_body_similarity = _section_similarity(
                old_section.body,
                new_section.body,
            )
            body_similarity = raw_body_similarity
            used_evidence_suppression = (
                raw_body_similarity < options.min_section_match_similarity
            )
            if used_evidence_suppression:
                old_matching_body = _section_matching_body(
                    old_section.body,
                    old_table_unit_keys,
                )
                new_matching_body = _section_matching_body(
                    new_section.body,
                    new_table_unit_keys,
                )
                if not old_matching_body or not new_matching_body:
                    continue
                body_similarity = _section_similarity(
                    old_matching_body,
                    new_matching_body,
                )  # 原始正文不足时才剔除结构化/已证明表格与 Figure 单元重试。
            if body_similarity < options.min_section_match_similarity:
                continue  # 正文证据不足时保守保留新增/删除，避免把完全重写的同名章节强配。
            score = _section_match_score(old_section, new_section)
            if score < options.min_section_match_similarity:
                continue
            comparable_similarity = _section_similarity(
                old_section.comparable_text,
                new_section.comparable_text,
            )  # 标题/位置组合分只能排序候选，不能替代用户声明的实际可比文本门槛。
            match_basis = (
                "evidence_suppressed_similarity_fallback"
                if used_evidence_suppression
                else "similarity_fallback"
            )
            if comparable_similarity < options.min_section_match_similarity:
                title_key = _review_unit_key(old_section.title)
                if (
                    not title_key
                    or title_key != _review_unit_key(new_section.title)
                    or old_title_counts[title_key] != 1
                    or new_title_counts[title_key] != 1
                ):
                    continue  # 低原始全文分只允许双侧唯一同题条款借表格剔除后的正文证据恢复。
                if not used_evidence_suppression:
                    if raw_body_similarity < max(
                        options.min_section_match_similarity,
                        0.85,
                    ):
                        continue  # 错误父层级只能由双侧唯一同题且强正文相似度越过，普通阈值不足以授权。
                    match_basis = "unique_title_body_fallback"
            candidate = (score, old_index, new_index, match_basis)
            if match_basis != "similarity_fallback":
                late_fallback_candidates.append(candidate)
            else:
                fallback_candidates.append(candidate)

    _consume_section_match_candidates(
        fallback_candidates,
        old_sections,
        new_sections,
        matched_old,
        matched_new,
        matches,
    )

    ordinary_matches = tuple(matches)  # 后置结构救援只能引用首轮普通配对，禁止候选互相循环自证。
    for old_index, new_index, match_basis in _structural_identity_rescue_pairs(
        old_sections,
        new_sections,
        exact_old_by_key,
        exact_new_by_key,
        matched_old,
        matched_new,
        {
            (old_index, new_index)
            for old_index, new_index, _similarity_value, _match_basis in matches
            if old_index is not None and new_index is not None
        },
        options.min_section_match_similarity,
    ):
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                match_basis,
            )
        )  # 保留实际全文分数；结构证据只授权配对，不伪造高相似度。

    for old_index, new_index, match_basis in _shifted_section_rescue_pairs(
        old_sections,
        new_sections,
        ordinary_matches,
        matched_old,
        matched_new,
        options.min_section_match_similarity,
    ):
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                match_basis,
            )
        )  # 编号后移只改变配对授权；报告继续显示实际全文相似度。

    # 表格/Figure 剔除候选及错误父层级下的唯一同题强正文候选只能兜底：
    # 先让编号结构、兄弟偏移和父子边界使用更强证据，避免抢走可结构化解释的配对。
    _consume_section_match_candidates(
        late_fallback_candidates,
        old_sections,
        new_sections,
        matched_old,
        matched_new,
        matches,
    )

    for old_index, new_index in _mapped_parent_unique_child_rescue_pairs(
        old_sections,
        new_sections,
        matches,
        matched_old,
        matched_new,
    ):
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                "structural_mapped_parent_unique_child",
            )
        )

    explicit_two_sided_window = all(
        value is not None
        for value in (
            options.old_start_page,
            options.old_end_page,
            options.new_start_page,
            options.new_end_page,
        )
    )
    document_relation_pairs = (
        []
        if explicit_two_sided_window
        else _document_relation_anchor_pairs(
            old_sections,
            new_sections,
            matches,
            matched_old,
            matched_new,
            options.min_section_match_similarity,
        )
    )
    for old_index, new_index in document_relation_pairs:
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                "document_relation_anchor",
            )
        )  # 双侧唯一标题和高段落骨架覆盖可越过错误父层级；仍保留真实全文分数。

    for old_index, new_index in _user_page_window_anchor_pairs(
        old_sections,
        new_sections,
        matches,
        matched_old,
        matched_new,
        options,
    ):
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                "user_page_window_anchor",
            )
        )  # 用户同时限定两侧页窗时，授权最相关的剩余正文进入比较，但不伪造相似度。

    for new_index, _new_section in enumerate(new_sections):
        if new_index not in matched_new:
            matches.append((None, new_index, 0.0, "unmatched"))

    for old_index, _old_section in enumerate(old_sections):
        if old_index not in matched_old:
            matches.append((old_index, None, 0.0, "unmatched"))
    return matches

def section_match_oracle():
    from protocol_pdf_diff import compare
    function = types.FunctionType(_match_sections.__code__, dict(vars(compare)))
    function.__kwdefaults__ = _match_sections.__kwdefaults__
    return function
