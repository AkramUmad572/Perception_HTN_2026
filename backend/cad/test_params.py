#!/usr/bin/env python3
"""
Tests for cad/params.py (the PARAMS convention for CAD scripts).

Run with: python -m cad.test_params
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cad.params import ParamError, extract_params  # noqa: E402


# ── extract_params ────────────────────────────────────────────────────────────

def test_extract_basic():
    s = 'import cadquery as cq\nPARAMS = {"ear_length_mm": 16, "head_radius_mm": 16.0}\nresult = None\n'
    got = extract_params(s)
    assert got == {"ear_length_mm": 16.0, "head_radius_mm": 16.0}, got
    assert all(type(v) is float for v in got.values()), got


def test_extract_missing_is_empty():
    assert extract_params("import cadquery as cq\nresult = 1\n") == {}
    assert extract_params("") == {}


def test_extract_syntax_error_is_empty():
    assert extract_params('PARAMS = {"a_mm": 1\nresult = (') == {}


def test_extract_non_literal_is_empty():
    assert extract_params('x = 3\nPARAMS = {"a_mm": x}\n') == {}
    assert extract_params('PARAMS = {"a_mm": "5"}\n') == {}
    assert extract_params('PARAMS = {"a_mm": True}\n') == {}
    assert extract_params('B = {}\nPARAMS = {"a_mm": 1, **B}\n') == {}
    assert extract_params('PARAMS = {1: 2}\n') == {}
    assert extract_params('PARAMS = dict(a_mm=1)\n') == {}


def test_extract_negative_literal():
    assert extract_params('PARAMS = {"offset_mm": -3}\n') == {"offset_mm": -3.0}


def test_extract_only_module_level_last_wins():
    s = 'def f():\n    PARAMS = {"a_mm": 1}\n    return PARAMS\n'
    assert extract_params(s) == {}
    s2 = 'PARAMS = {"a_mm": 1}\nPARAMS = {"b_mm": 2}\n'
    assert extract_params(s2) == {"b_mm": 2.0}


def test_extract_annotated():
    assert extract_params('PARAMS: dict = {"a_mm": 4}\n') == {"a_mm": 4.0}


def test_extract_never_executes():
    s = (
        'PARAMS = {"a_mm": 7}\n'
        'raise SystemExit("executed!")\n'
        'data = open("/etc/passwd").read()\n'
    )
    assert extract_params(s) == {"a_mm": 7.0}


# ── runner ────────────────────────────────────────────────────────────────────

def run_all_tests():
    print("=" * 60)
    print("CAD PARAMS TESTS")
    print("=" * 60)
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [ok] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001 - report every failure kind
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"TOTAL: {passed}/{passed + failed} passed")
    if failed:
        print(f"\n{failed} TESTS FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
