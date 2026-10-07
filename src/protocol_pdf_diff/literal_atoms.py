"""保留原文跨度的字面分词，供变化摘要和重分页配对共用。

数值与相邻单位分开，符号、小数和指数不拆散；型号整体保留。
不做数值求值、单位换算或大小写归一，导航继续使用自己的源词契约。
"""
import re

_NUMBER = r"[+−-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\.\d+|\d+)(?:[eE][+−-]?\d+)?"
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*(?:[./-][A-Za-z0-9_]+)*"
_ATOMS = re.compile(rf"{_IDENTIFIER}|{_NUMBER}|[\u3400-\u9fff]|[^\W\d_]+|[^\s]")


def literal_atom_spans(text):
    """返回完整非空白原文的 match 跨度；有歧义的连写型号宁可整体显示。"""
    return tuple(_ATOMS.finditer(text))


def literal_atoms(text):
    return [match.group() for match in literal_atom_spans(text)]
