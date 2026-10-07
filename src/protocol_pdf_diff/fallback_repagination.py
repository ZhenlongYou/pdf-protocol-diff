"""仅对无编号连续正文证明纯重分页，真实改字仍走原有差异路径。"""

from dataclasses import replace
from hashlib import sha1
from difflib import SequenceMatcher
import re
from .evidence_alignment import literal_key


def coalesce_exact_fallback_runs(old_sections, new_sections):
    def eligible(sections):
        return bool(sections) and all(
            s.section_id.startswith("P") and len(s.number_path) == 1
            and s.number_path[0].startswith("fallback-content:")
            and s.role == sections[0].role for s in sections
        ) and all(a.end_page + 1 == b.start_page for a, b in zip(sections, sections[1:]))

    if not eligible(old_sections) or not eligible(new_sections):
        return old_sections, new_sections
    key = literal_key("\n".join(s.body for s in old_sections))
    if not key or key != literal_key("\n".join(s.body for s in new_sections)):
        return old_sections, new_sections
    identity = "fallback-content:" + sha1(key.encode()).hexdigest()

    def merge(sections):
        return [replace(sections[0], number_path=(identity,), end_page=sections[-1].end_page,
                        body=key, page_bodies=tuple(part for s in sections for part in s.page_bodies))]
    return merge(old_sections), merge(new_sections)


def coalesce_anchored_fallback_runs(old_sections, new_sections):
    """用唯一前后锚点配对一次局部改动；保留双方全文，不授予判等结论。

    仅覆盖无标题连续页、页数改变、一个句段内的一次编辑。移动、多个编辑、
    重复锚点或不守恒的分句结果继续交原匹配路径，不能靠相似度吞掉不确定性。
    """
    def eligible(sections):
        return bool(sections) and all(
            s.section_id.startswith("P") and len(s.number_path) == 1
            and s.number_path[0].startswith("fallback-content:")
            and s.role == sections[0].role for s in sections
        ) and all(a.end_page + 1 == b.start_page for a, b in zip(sections, sections[1:]))

    if (not eligible(old_sections) or not eligible(new_sections)
            or old_sections[0].role != new_sections[0].role
            or len(old_sections) == len(new_sections)):
        return old_sections, new_sections
    # 汉字按字保留，其余词、数字和符号保持字面身份，不折叠大小写或正负号。
    def tokens(value):
        return re.findall(r"[\u3400-\u9fff]|[^\W\u3400-\u9fff]+|[^\w\s]", value)

    # 仅去掉物理页边界：例如单位 V. 被挤到下一页时不能变成一个新列表项。
    # 页内换行及所有原页文本仍分别保留在 body / page_bodies。
    bodies = [" ".join(s.body for s in sections) for sections in (old_sections, new_sections)]
    sides = [tokens(body) for body in bodies]
    # 先线性收紧范围，再验证真实编辑区间；避免整本重复文本进入二次复杂度匹配。
    a = c = next((i for i, (old, new) in enumerate(zip(*sides)) if old != new), min(map(len, sides)))
    tail = 0
    while tail < min(len(sides[0])-a, len(sides[1])-c) and sides[0][-tail-1] == sides[1][-tail-1]:
        tail += 1
    b, d = len(sides[0])-tail, len(sides[1])-tail
    # 最邻近的 8 个字面 token 为局部锚点；不足 4 个或全文出现多次就不接受。
    # 这是配对证据范围，不限制待比较全文，也不删除超出范围的内容。
    left, right = sides[0][max(0, a-8):a], sides[0][b:b+8]
    if min(len(left), len(right)) < 4 or sides[1][max(0, c-8):c] != left or sides[1][d:d+8] != right:
        return old_sections, new_sections
    if any(sum(side[i:i+len(anchor)] == anchor for i in range(len(side)-len(anchor)+1)) != 1
           for side in sides for anchor in (left, right)):
        return old_sections, new_sections
    # 唯一锚点仍不能证明跨条件段的移动。单次编辑必须局限在一个原始句段，
    # 且新增/删除内容不能在另一位置已有同一字面串（可能是重复或移动）。
    from .compare import _split_units
    for body, side, start, end in zip(bodies, sides, (a, c), (b, d)):
        units = _split_units(body)
        if literal_key(" ".join(units)) != literal_key(body):
            return old_sections, new_sections
        offset, touched = 0, 0
        for unit in units:
            next_offset = offset + len(tokens(unit))
            if max(start, offset) < min(end, next_offset):
                touched += 1
            offset = next_offset
        if touched > 1:
            return old_sections, new_sections
    # 前后缀只是候选范围；其中必须确实仅有一个字面编辑，不能夹带移动/多个改动。
    edits = [op for op in SequenceMatcher(None, sides[0][a:b], sides[1][c:d], autojunk=False).get_opcodes()
             if op[0] != "equal"]
    if len(edits) != 1:
        return old_sections, new_sections
    for removed, other in ((sides[0][a:b], sides[1]), (sides[1][c:d], sides[0])):
        if removed and any(other[i:i+len(removed)] == removed for i in range(len(other)-len(removed)+1)):
            return old_sections, new_sections
    identity = "fallback-content:" + sha1(repr((left, right)).encode()).hexdigest()
    def merge(sections, body):
        return [replace(sections[0], number_path=(identity,), end_page=sections[-1].end_page,
                        body=body, page_bodies=tuple(part for s in sections for part in s.page_bodies),
                        heading_provenance="fallback-repagination-anchors")]
    return merge(old_sections, bodies[0]), merge(new_sections, bodies[1])
