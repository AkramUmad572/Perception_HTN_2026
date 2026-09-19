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
