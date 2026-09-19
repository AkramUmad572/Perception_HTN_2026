"""
Named dimensions of a CAD script: the module-level ``PARAMS = {...}`` literal.

Generated CadQuery scripts declare their dimensions up front, e.g.::

    PARAMS = {"ear_length_mm": 16, "head_radius_mm": 16}

and read them in the body (``PARAMS["ear_length_mm"]``). This module reads and
rewrites that one literal **without executing the script**: everything here is
``ast`` plus text splicing, so it is safe on untrusted code and fast enough to
run on every drag of a dimension handle.
"""

from __future__ import annotations

import ast
import math


class ParamError(ValueError):
    """A PARAMS edit that cannot be applied. The message is speakable."""


def _find_params_node(tree: ast.Module) -> ast.Dict | None:
    """The value of the last module-level ``PARAMS = ...``, if it is a dict display."""
    found: ast.expr | None = None
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "PARAMS"
        ):
            found = stmt.value
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "PARAMS"
            and stmt.value is not None
        ):
            found = stmt.value
    return found if isinstance(found, ast.Dict) else None


def _number(node: ast.expr) -> float | None:
    """A numeric literal (optionally signed), never a bool."""
    sign = 1.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1.0 if isinstance(node.op, ast.USub) else 1.0
        node = node.operand
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return sign * float(node.value)
    return None


def _literal_items(d: ast.Dict) -> list[tuple[str, ast.expr, float]] | None:
    """(key, value node, value) per entry, or None if it is not a literal dict of numbers."""
    items: list[tuple[str, ast.expr, float]] = []
    for k, v in zip(d.keys, d.values):
        if k is None or not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            return None
        num = _number(v)
        if num is None:
            return None
        items.append((k.value, v, num))
    return items


def _parse_params(script: str) -> list[tuple[str, ast.expr, float]] | None:
    try:
        tree = ast.parse(script or "")
    except (SyntaxError, ValueError):
        return None
    node = _find_params_node(tree)
    if node is None:
        return None
    return _literal_items(node)


def extract_params(script: str) -> dict[str, float]:
    """The script's PARAMS as ``{name: float}``; ``{}`` when absent or not a literal dict of numbers."""
    items = _parse_params(script)
    if not items:
        return {}
    return {k: v for k, _, v in items}


def _spoken(name: str) -> str:
    """``ear_length_mm`` -> ``ear length``: dimension names are read aloud."""
    words = [w for w in str(name).split("_") if w]
    if len(words) > 1 and words[-1].lower() in ("mm", "cm", "m", "deg"):
        words = words[:-1]
    return " ".join(words) or "that"


def _format_value(v: float) -> str:
    v = round(float(v), 4)
    if v.is_integer():
        return str(int(v))
    return repr(v)


def _valid_value(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def _line_starts(data: bytes) -> list[int]:
    """Byte offset where each 1-based line begins; index with ``lineno - 1``.

    Matches the tokenizer: ``\\n``, ``\\r\\n`` and a lone ``\\r`` all end a line.
    """
    starts = [0]
    n = len(data)
    for i, b in enumerate(data):
        if b == 0x0A or (b == 0x0D and (i + 1 >= n or data[i + 1] != 0x0A)):
            starts.append(i + 1)
    return starts


def set_params(script: str, updates: dict[str, float]) -> str:
    """
    Rewrite the given PARAMS values, leaving every other byte of ``script`` intact.

    Every update is validated before anything is rewritten: either all values
    land or ``ParamError`` is raised. ``ast`` column offsets count UTF-8 bytes,
    so the splice works on the encoded script.
    """
    if not updates:
        return script
    items = _parse_params(script)
    if items is None:
        raise ParamError("This model has no named dimensions I can change.")
    known = {k for k, _, _ in items}
    clean: dict[str, float] = {}
    for name, value in updates.items():
        if name not in known:
            raise ParamError(f"This model has no dimension called {_spoken(name)}.")
        v = _valid_value(value)
        if v is None:
            raise ParamError(f"The {_spoken(name)} has to be a positive number.")
        clean[name] = v

    data = script.encode("utf-8")
    starts = _line_starts(data)
    edits: list[tuple[int, int, bytes]] = []
    for key, node, _ in items:
        if key in clean:
            start = starts[node.lineno - 1] + node.col_offset
            end = starts[node.end_lineno - 1] + node.end_col_offset
            edits.append((start, end, _format_value(clean[key]).encode("utf-8")))
    for start, end, text in sorted(edits, reverse=True):
        data = data[:start] + text + data[end:]
    return data.decode("utf-8")
