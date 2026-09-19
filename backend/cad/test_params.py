#!/usr/bin/env python3
"""
Tests for cad/params.py (the PARAMS convention for CAD scripts).

Run with: python -m cad.test_params
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cad.params import ParamError, extract_params, params_for_part, set_params  # noqa: E402


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


# ── set_params ────────────────────────────────────────────────────────────────

SCRIPT = (
    "import cadquery as cq\n"
    "\n"
    "PARAMS = {\n"
    "    # ears  \n"
    '    "ear_length_mm": 16,   # tall\n'
    '    "head_radius_mm": 16.0,\n'
    '    "tip_r_mm" : 0.7 ,\n'
    "}\n"
    "\n"
    'head = cq.Workplane("XY").sphere(PARAMS["head_radius_mm"])  \n'
    'ear = PARAMS["ear_length_mm"] * 1  # 16 stays 16 here\n'
    "result = head\n"
)


def _raises(fn, *a):
    try:
        fn(*a)
    except ParamError as e:
        return str(e)
    raise AssertionError(f"expected ParamError for {a[1:]!r}")


def test_set_single_value_byte_preserving():
    new = set_params(SCRIPT, {"ear_length_mm": 20})
    assert new == SCRIPT.replace('"ear_length_mm": 16,', '"ear_length_mm": 20,', 1), new
    assert extract_params(new)["ear_length_mm"] == 20.0


def test_set_multiple_values():
    new = set_params(SCRIPT, {"head_radius_mm": 18.5, "tip_r_mm": 1})
    expected = SCRIPT.replace('"head_radius_mm": 16.0,', '"head_radius_mm": 18.5,').replace(
        '"tip_r_mm" : 0.7 ,', '"tip_r_mm" : 1 ,'
    )
    assert new == expected, new


def test_set_formatting():
    s = 'PARAMS = {"a_mm": 1}\n'
    assert set_params(s, {"a_mm": 2.5}) == 'PARAMS = {"a_mm": 2.5}\n'
    assert set_params(s, {"a_mm": 12.0}) == 'PARAMS = {"a_mm": 12}\n'
    assert set_params(s, {"a_mm": 0.1 + 0.2}) == 'PARAMS = {"a_mm": 0.3}\n'


def test_set_non_ascii_before_value():
    s = 'PARAMS = {"größe_mm": 5, "b_mm": 3}  # größe\nx = "ü"\n'
    new = set_params(s, {"b_mm": 9})
    assert new == 'PARAMS = {"größe_mm": 5, "b_mm": 9}  # größe\nx = "ü"\n', new
    new2 = set_params(s, {"größe_mm": 7})
    assert new2 == s.replace('"größe_mm": 5', '"größe_mm": 7'), new2


def test_set_crlf_line_endings():
    s = 'import math\r\nPARAMS = {\r\n    "a_mm": 1,\r\n    "b_mm": 2,\r\n}\r\n'
    assert set_params(s, {"b_mm": 30}) == s.replace('"b_mm": 2', '"b_mm": 30')


def test_set_replaces_negative_literal():
    assert set_params('PARAMS = {"o_mm": -3}\n', {"o_mm": 4}) == 'PARAMS = {"o_mm": 4}\n'


def test_set_empty_updates_is_identity():
    assert set_params(SCRIPT, {}) == SCRIPT


def test_set_duplicate_keys_all_rewritten():
    s = 'PARAMS = {"a_mm": 1, "a_mm": 2}\n'
    assert set_params(s, {"a_mm": 5}) == 'PARAMS = {"a_mm": 5, "a_mm": 5}\n'


def test_set_errors():
    _raises(set_params, SCRIPT, {"nope_mm": 3})
    _raises(set_params, "result = 1\n", {"a_mm": 3})
    _raises(set_params, 'x = 1\nPARAMS = {"a_mm": x}\n', {"a_mm": 3})
    _raises(set_params, "PARAMS = {\n", {"a_mm": 3})
    for bad in (0, -1, float("nan"), float("inf"), True, "5", None):
        _raises(set_params, SCRIPT, {"ear_length_mm": bad})


def test_set_error_is_atomic_and_speakable():
    msg = _raises(set_params, SCRIPT, {"ear_length_mm": 20, "ear_width_mm": 3})
    assert "ear width" in msg, msg
    for bad in ("ParamError", "/", "_mm", "`", "*"):
        assert bad not in msg, (bad, msg)
    msg2 = _raises(set_params, "result = 1\n", {"a_mm": 3})
    assert "_" not in msg2 and "PARAMS" not in msg2, msg2


# ── params_for_part ───────────────────────────────────────────────────────────

P = {
    "ear_length_mm": 16.0,
    "ear_base_r_mm": 5.5,
    "head_radius_mm": 16.0,
    "earring_d_mm": 3.0,
    "wheel_radius_mm": 7.0,
    "wheel_fl_offset_mm": 22.0,
}
EAR = {"ear_length_mm": 16.0, "ear_base_r_mm": 5.5}


def test_part_prefix_needs_underscore():
    assert params_for_part(P, "ear") == EAR


def test_part_left_right_uses_base_name():
    assert params_for_part(P, "ear_l") == EAR
    assert params_for_part(P, "ear_r") == EAR
    assert params_for_part(P, "ear_left") == EAR
    assert params_for_part(P, "ear_right") == EAR


def test_part_exact_and_specific():
    assert params_for_part(P, "head") == {"head_radius_mm": 16.0}
    assert params_for_part(P, "wheel") == {"wheel_radius_mm": 7.0, "wheel_fl_offset_mm": 22.0}


def test_part_corner_suffix_uses_base_name():
    # four wheels named by corner still share the wheel dimensions
    both = {"wheel_radius_mm": 7.0, "wheel_fl_offset_mm": 22.0}
    assert params_for_part(P, "wheel_fl") == both
    assert params_for_part(P, "wheel_rr") == both  # base "wheel_" prefix covers every wheel_* key
    assert params_for_part(P, "wheel_front") == both
    assert params_for_part({"hub_radius_mm": 2.8}, "hub_rr") == {"hub_radius_mm": 2.8}


def test_part_dedupe_suffix():
    assert params_for_part(P, "wheel_2") == {"wheel_radius_mm": 7.0, "wheel_fl_offset_mm": 22.0}
    # sandbox dedupe on a sided name: "ear_l" twice becomes "ear_l_2"
    assert params_for_part(P, "ear_l_2") == EAR


def test_part_specific_keys_combine_with_base():
    p = {"ear_l_tilt_mm": 2.0, "ear_length_mm": 16.0, "ear_r_tilt_mm": 3.0}
    assert params_for_part(p, "ear_l") == {"ear_l_tilt_mm": 2.0, "ear_length_mm": 16.0, "ear_r_tilt_mm": 3.0}


def test_part_no_match_and_no_mutation():
    before = dict(P)
    assert params_for_part(P, "tail") == {}
    assert params_for_part(P, "") == {}
    assert params_for_part({}, "ear") == {}
    assert P == before


# ── the codegen prompt follows the convention ─────────────────────────────────
# Every example in CODEGEN_SYSTEM_PROMPT is what the model copies, so each one
# must run through the real sandbox, expose PARAMS, and actually respond to them.

EXAMPLE_KEYS = [
    {"head_radius_mm", "ear_length_mm", "lug_hole_d_mm"},  # character
    {"plate_length_mm", "plate_width_mm", "lug_hole_d_mm"},  # keychain
    {"body_length_mm", "wheel_radius_mm", "wheelbase_mm"},  # toy car
]
# The dimension whose change must visibly change the model's overall size.
EXAMPLE_BUMP = ["head_radius_mm", "plate_length_mm", "body_length_mm"]


def _prompt_examples() -> list[str]:
    import re

    from ai.intent import CODEGEN_SYSTEM_PROMPT

    return re.findall(r"```python\n(.*?)```", CODEGEN_SYSTEM_PROMPT, re.S)


def _run(script: str):
    import tempfile

    from cad.sandbox import execute_cadquery_script

    res = execute_cadquery_script(script, output_dir=tempfile.mkdtemp(), flatten_color=False)
    assert res.get("ok"), res
    return res


def _mesh_node_names(glb_path: str) -> list[str]:
    import json
    import struct

    data = Path(glb_path).read_bytes()
    (length,) = struct.unpack("<I", data[12:16])
    gltf = json.loads(data[20:20 + length])
    return [n["name"] for n in gltf.get("nodes", []) if "mesh" in n]


def _extents(glb_path: str):
    import trimesh

    return [round(float(x), 5) for x in trimesh.load(glb_path).extents]


def test_prompt_has_three_examples():
    assert len(_prompt_examples()) == 3, len(_prompt_examples())


def test_prompt_examples_declare_params_first():
    import ast

    for i, ex in enumerate(_prompt_examples()):
        body = [s for s in ast.parse(ex).body if not isinstance(s, (ast.Import, ast.ImportFrom))]
        first = body[0]
        assert isinstance(first, ast.Assign) and first.targets[0].id == "PARAMS", (i, ast.dump(first)[:80])
        params = extract_params(ex)
        assert EXAMPLE_KEYS[i] <= set(params), (i, sorted(params))
        bad = [k for k in params if not k.endswith("_mm") or k != k.lower()]
        assert not bad, (i, bad)


def test_prompt_examples_run_and_every_part_has_params():
    for i, ex in enumerate(_prompt_examples()):
        res = _run(ex)
        assert res.get("multi_color"), (i, res)
        params = extract_params(ex)
        names = _mesh_node_names(res["glb_path"])
        assert len(names) >= 3, (i, names)
        orphans = [n for n in names if not params_for_part(params, n)]
        assert not orphans, (i, orphans)


def test_prompt_examples_respond_to_params():
    for i, ex in enumerate(_prompt_examples()):
        key = EXAMPLE_BUMP[i]
        params = extract_params(ex)
        bumped = set_params(ex, {key: params[key] + 4})
        assert bumped != ex
        before = _extents(_run(ex)["glb_path"])
        after = _extents(_run(bumped)["glb_path"])
        assert before != after, (i, key, before, after)


def test_prompt_and_followups_mention_params():
    import json

    from ai.intent import CODEGEN_SYSTEM_PROMPT, REPAIR_PROMPT, _build_user_payload

    assert "PARAMS = {" in CODEGEN_SYSTEM_PROMPT
    assert "<part>_<dimension>_mm" in CODEGEN_SYSTEM_PROMPT
    assert "PARAMS" in REPAIR_PROMPT
    REPAIR_PROMPT.format(error="e", script="s")  # braces still escaped correctly
    instruction = json.loads(_build_user_payload("x", current_script="s"))["instruction"]
    assert "PARAMS" in instruction, instruction


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
