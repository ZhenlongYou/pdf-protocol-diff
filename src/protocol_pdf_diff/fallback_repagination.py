"""仅对无编号连续正文证明纯重分页，真实改字仍走原有差异路径。"""

from dataclasses import replace
from hashlib import sha1
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
