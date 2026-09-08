"""Occurrence-preserving evidence for publication figure/table catalogs.

Leader dots and the terminal locator are layout slots only after a complete
catalog entry proves them. Raw section bodies remain untouched for comparison.
"""
from collections import Counter
import re

CATALOG_TITLE = re.compile(r'(?i)^(?:list\s+of\s+(?:figures|tables|illustrations)|图目录|表目录|插图目录)$')
_ENTRY_START = re.compile(
    r'(?im)^\s*((?:figure|fig\.|table|图|表)\s+[A-Z0-9]+(?:[.\-–][A-Z0-9]+)*)[.:：]?\s+'
)
_LOCATOR = re.compile(r'(.+?)(?:\.{3,}|…{2,}|(?:\.\s*){3,})\s*(?:\d+|[ivxlcdm]+)\s*$', re.S | re.I)


def catalog_entries(section):
    if not CATALOG_TITLE.fullmatch(section.title.strip()):
        return None
    entries = Counter()
    covered = 0
    starts = list(_ENTRY_START.finditer(section.body))
    for index, match in enumerate(starts):
        end = starts[index+1].start() if index+1 < len(starts) else len(section.body)
        chunk = section.body[match.end():end].strip()
        locator = _LOCATOR.fullmatch(chunk)
        if locator is None:
            continue  # Missing layout-slot proof cannot normalize this entry.
        identifier, title = match.group(1), locator.group(1)
        entries[(' '.join(identifier.casefold().split()), ' '.join(title.split()))] += 1
        covered += len(section.body[match.start():end].strip())
    if sum(entries.values()) < 3 or covered < len(section.body.strip()) * .8:
        return None
    return entries


def catalog_identity_similarity(old, new):
    old_entries, new_entries = catalog_entries(old), catalog_entries(new)
    if not old_entries or not new_entries:
        return None
    shared = sum((old_entries & new_entries).values())
    return 2 * shared / (sum(old_entries.values()) + sum(new_entries.values()))
