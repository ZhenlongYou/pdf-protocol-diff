"""Reversible text codec for internal structured table rows.

The human-readable row format uses `` | `` between cells and the first
unescaped ``=`` between a field name and value.  PDF cells may legitimately
contain all of those characters, so components are escaped before formatting
and decoded by every consumer.
"""

from __future__ import annotations

import re


def escape_table_component(value: str) -> str:
    """Escape structural characters inside one observed header or cell value."""

    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("|", "\\|")
        .replace("=", "\\=")
    )


def unescape_table_component(value: str) -> str:
    """Decode one component while preserving unknown backslash sequences."""

    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value) and value[index + 1] in "\\n|=":
            escaped_character = value[index + 1]
            result.append("\n" if escaped_character == "n" else escaped_character)
            index += 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def encode_table_field(label: str, value: str) -> str:
    """Encode one labeled cell without losing the header/value boundary."""

    return f"{escape_table_component(label)}={escape_table_component(value)}"


def split_table_cells(value: str) -> list[str]:
    """Split structural cell separators but retain compact ``|`` operators."""

    return [cell.strip() for cell in re.split(r"\s+\|\s+", value)]


def split_table_field(cell: str) -> tuple[str, str] | None:
    """Split the first unescaped field delimiter and decode both components."""

    escaped = False
    for index, character in enumerate(cell):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "=":
            return (
                unescape_table_component(cell[:index]),
                unescape_table_component(cell[index + 1 :]),
            )
    return None


def decode_table_cell(cell: str) -> str:
    """Decode an unlabeled cell for comparison and display."""

    return unescape_table_component(cell)
