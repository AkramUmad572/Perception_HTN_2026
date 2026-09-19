# WS-C: CAD PARAMS core implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CAD scripts carry a module-level `PARAMS = {...}` dict of named mm dimensions.
The server can read that dict and rewrite its values without executing or regenerating
the script, and every assembly part reaches the GLB as a node with its `assy.add(name=...)`
name.

**Architecture:** A new pure module `backend/cad/params.py` parses scripts with `ast` only
(it never runs them) and rewrites values by splicing source text at the AST value nodes'
byte offsets, so every other byte is preserved. `CODEGEN_SYSTEM_PROMPT` and its three
examples adopt the `PARAMS` convention. The follow-up instruction and the repair prompt
tell the model to keep `PARAMS` and edit it. The sandbox GLB export already names nodes
after the parts (verified by probe, see Task 4). WS-C adds a regression test to lock that in.

**Tech Stack:** Python 3.12 stdlib `ast`, CadQuery 2.8, trimesh 4.

**Spec:** `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md` §3; master plan
`docs/superpowers/plans/2026-09-19-in-headset-editing-master.md` "WS-C".

## Global Constraints

- **Commits must NOT contain a `Co-Authored-By: Claude` trailer or any Claude attribution.**
- Python: `/Users/dimural/Perception_HTN_2026/.venv/bin/python`. Run backend tests from `backend/` as `python -m <pkg>.test_<x>`.
- Tests are plain scripts with a `__main__` runner printing `TOTAL: n/m passed` and exiting non-zero on failure. No pytest. Register every new suite in `run_tests.sh`.
- Full suite stays green: `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh` from the worktree root.
- Respect `ai-docs/09-invariants.md`. WS-C changes no invariant.
- `reply` strings are spoken aloud, so `ParamError` messages must be speakable: no paths, no exception names, no markdown. Wave 2 will speak them.
- `extract_params` never executes the script (`ast` only). `set_params` preserves every other byte.
- In `ai/intent.py`, touch only prompt text (`CODEGEN_SYSTEM_PROMPT`, `REPAIR_PROMPT`) and the follow-up `instruction` string in `_build_user_payload`. No endpoints.
- Docs: edit only `/Users/dimural/Perception_HTN_2026/ai-docs/04-cad-lane.md`.
- Context rule: at about 30% context used, commit and write `docs/superpowers/handoffs/2026-09-19-ws-c-handoff-<n>.md`.

## File structure

| File | Responsibility |
|---|---|
| `backend/cad/params.py` (create) | `ParamError`, `extract_params`, `set_params`, `params_for_part`. Pure, stdlib only |
| `backend/cad/test_params.py` (create) | Unit tests for params.py plus prompt-example tests (the examples run in the sandbox, and extract returns the expected keys) |
| `backend/cad/test_sandbox.py` (modify) | Adds the GLB node-name test |
| `backend/ai/intent.py` (modify) | Prompt text and follow-up instruction only |
| `run_tests.sh` (modify) | Registers `cad.test_params` |

Prompt tests live in `cad/test_params.py`, not `ai/test_intent.py`, because WS-A and WS-B
both edit `ai/test_intent.py` and that would add merge conflicts.

## Design decisions

- **Which assignment:** the *last* module-level `PARAMS = {...}` (plain `Assign`, single
  `Name` target, or `AnnAssign`). That matches Python semantics. `set_params` edits that same node.
- **"Literal dict of numbers":** every key is a `str` constant, and every value is an
  `int`/`float` constant (not `bool`) or a unary `-`/`+` applied to one. Anything else
  (`**spread`, names, calls, strings) makes `extract_params` return `{}`. A `SyntaxError` also returns `{}`.
- **Byte offsets:** `ast` `col_offset` values count UTF-8 bytes. The splice works on
  `script.encode("utf-8")` with absolute byte offsets computed from line starts, so
  non-ASCII comments are safe.
- **Value formatting:** `round(v, 4)`. An integral value is written as `int` (`12`),
  otherwise as `repr(float)` (`2.5`).
- **Duplicate keys in the literal:** every occurrence is rewritten, so the script stays self-consistent.
- **Validation (`ParamError`, speakable):** the script has no `PARAMS` literal, `updates` has
  a name not in `PARAMS`, or a value is a bool, not a number, non-finite, or ≤ 0.
  Validation happens before any rewrite, so either all updates apply or none do.
- **`params_for_part`:** a key matches if it starts with `f"{part}_"`, or with
  `f"{base}_"` where `base` is `part` stripped of one trailing `_l`/`_r`/`_left`/`_right`
  or `_<digits>`. The digit suffix covers the sandbox's duplicate-name suffix `name_2`,
  which makes it a superset of the master-plan wording.

---

### Task 1: `extract_params`

**Files:**
- Create: `backend/cad/params.py`, `backend/cad/test_params.py`
- Modify: `run_tests.sh` (register the suite after the sandbox tests)

**Interfaces:**
- Produces: `class ParamError(ValueError)`, `def extract_params(script: str) -> dict[str, float]`

- [ ] **Step 1: Write the failing tests** in `cad/test_params.py`, using a runner that collects
  `test_*` functions, catches `AssertionError`/`Exception`, prints `[ok]`/`[FAIL]` and
  `TOTAL: n/m passed`, and returns 1 on failure. Cases:
  - a basic dict with an int and a float gives `{"ear_length_mm": 16.0, "head_radius_mm": 16.0}`, with values of type `float`
  - no `PARAMS` gives `{}`, and `""` gives `{}`
  - a `SyntaxError` script gives `{}`
  - a non-literal value (`{"a_mm": x}`) gives `{}`, a string value gives `{}`, a bool value gives `{}`, and `**other` gives `{}`
  - a negative literal `-3` gives `-3.0`
  - `PARAMS` defined inside a function is ignored (`{}`), and the last module-level assignment wins
  - an annotated `PARAMS: dict = {...}` works
  - no execution: a script whose body would `raise` and which contains `open("/etc/passwd")` still extracts
- [ ] **Step 2:** `cd backend && python -m cad.test_params` fails with an ImportError.
- [ ] **Step 3: Implement**

```python
def _find_params_node(tree) -> ast.Dict | None:
    found = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "PARAMS":
            found = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.target.id == "PARAMS" and stmt.value is not None:
            found = stmt.value
    return found if isinstance(found, ast.Dict) else None

def _number(node) -> float | None:  # Constant int/float (not bool), or +/- Constant
def _literal_items(d: ast.Dict) -> list[tuple[str, ast.expr, float]] | None
def extract_params(script: str) -> dict[str, float]
```

- [ ] **Step 4:** Tests pass. Register in `run_tests.sh`:

```bash
# Test 1b: CAD PARAMS tests
echo ""
echo ">>> Running CAD PARAMS Tests..."
echo ""
cd backend
if python3 -m cad.test_params; then
    echo "CAD PARAMS tests: PASSED"
else
    echo "CAD PARAMS tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..
```

- [ ] **Step 5:** Commit `Add cad.params.extract_params (ast-only PARAMS reader)`.

### Task 2: `set_params`

**Files:** modify `backend/cad/params.py`, `backend/cad/test_params.py`

**Interfaces:**
- Consumes: `_find_params_node`, `_literal_items` from Task 1
- Produces: `def set_params(script: str, updates: dict[str, float]) -> str`

- [ ] **Step 1: Failing tests:**
  - one value is updated (`16` becomes `20`), and `extract_params(new)["ear_length_mm"] == 20.0`
  - byte preservation: the new script equals the old one with only that value's span replaced (`old.replace('"ear_length_mm": 16', '"ear_length_mm": 20', 1) == new`). Comments, spacing, trailing whitespace, and a line using `PARAMS["ear_length_mm"]` stay the same
  - multi-line dict with comments and trailing commas: only the targeted values change
  - float formatting: `2.5` gives `2.5`, `12.0` gives `12`, and `0.1 + 0.2` gives `0.3`
  - non-ASCII comment on the same line before the value (`# größe`) still splices correctly
  - negative literal `-3` replaced by `4` gives `4`
  - several updates in one call
  - empty `updates` returns the script unchanged
  - `ParamError`: unknown name, missing PARAMS, non-literal PARAMS, `0`, `-1`, `nan`, `inf`, `True`, `"5"`. On error nothing is applied (atomic)
  - error message is speakable: no `ParamError`, no `/`, and the unknown name is spoken with spaces (`"ear length"`), not `ear_length_mm`
  - duplicate keys: both occurrences are rewritten
- [ ] **Step 2:** Run and see them fail (`set_params` is not defined).
- [ ] **Step 3: Implement.** Validate all updates first, then collect `(start_byte, end_byte, text)` for every
  matching value node, sort descending, splice into `script.encode()`, and decode.

```python
def _line_starts(data: bytes) -> list[int]  # byte offset of each line start (1-based index via [lineno-1])
def _format_value(v: float) -> str
def _spoken(name: str) -> str  # "ear_length_mm" -> "ear length"
def set_params(script: str, updates: dict[str, float]) -> str
```

- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `Add cad.params.set_params (byte-preserving PARAMS rewrite)`.

### Task 3: `params_for_part`

**Files:** modify `backend/cad/params.py`, `backend/cad/test_params.py`

**Interfaces:** Produces `def params_for_part(params: dict[str, float], part: str) -> dict[str, float]`

- [ ] **Step 1: Failing tests** with `P = {"ear_length_mm":16, "ear_base_r_mm":5.5, "head_radius_mm":16, "earring_d_mm":3, "wheel_radius_mm":7, "wheel_fl_offset_mm":22}`:
  - `"ear"` gives the two `ear_*` keys, not `earring_d_mm` (prefix needs the underscore)
  - `"ear_l"` and `"ear_r"` give the same as `"ear"`
  - `"ear_left"` gives the same as `"ear"`
  - `"head"` gives `head_radius_mm`
  - `"wheel_fl"` gives `wheel_fl_offset_mm` only (no base stripping for `fl`)
  - `"wheel_2"` gives `wheel_*` keys (sandbox dedupe suffix)
  - `"tail"` gives `{}`, and an empty part gives `{}`
  - the input dict is not mutated, and values are unchanged
- [ ] **Step 2:** Run and see them fail.
- [ ] **Step 3: Implement** with `re.sub(r"_(l|r|left|right|\d+)$", "", part)` as the base.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `Add cad.params.params_for_part`.

### Task 4: GLB part names regression test

**Files:** modify `backend/cad/test_sandbox.py`

Probe (before planning) showed that `_export_assembly` already passes
`node_name=name, geom_name=name`, and that the names survive `_paint_glb`'s reload and
re-export on the `flatten_color=True` path. So this task only adds a test. It is expected
to pass on first run. To prove the test is not vacuous, run it once against a
temporarily broken export (`node_name=f"x{i}"`), watch it fail, then revert.

- [ ] **Step 1: Write the test** `test_assembly_glb_node_names`. It builds a 2-part assembly (`base`, `peg`),
  exports with `flatten_color=False` and with `True`, parses the GLB JSON chunk (header
  12 bytes, chunk length at [12:16], JSON from byte 20), and asserts the set of node names
  with a `mesh` is exactly `{"base", "peg"}`. It also checks that the colours still differ
  on the False path, that bounds are in metres (max extent < 0.1), and that the export is watertight when loaded.
- [ ] **Step 2:** Break `node_name` temporarily, run, and see it FAIL. Revert and see it PASS.
- [ ] **Step 3:** Wire it into `run_all_tests` and the summary.
- [ ] **Step 4:** Commit `Test that assembly part names survive as GLB node names`.

### Task 5: Prompt adopts PARAMS

**Files:** modify `backend/ai/intent.py` (prompt text and instruction string only), `backend/cad/test_params.py`

**Interfaces:** Consumes `extract_params`, `execute_cadquery_script`, `CODEGEN_SYSTEM_PROMPT`, `REPAIR_PROMPT`, `_build_user_payload`

- [ ] **Step 1: Failing tests** in `cad/test_params.py`:
  - `_prompt_examples()` pulls the fenced ```` ```python ```` blocks out of `CODEGEN_SYSTEM_PROMPT` and asserts there are 3
  - each example: `extract_params` returns the expected key set (the character has `head_radius_mm`, `ear_length_mm` and more; the keychain has `plate_width_mm`, `hole_d_mm` and more; the car has `body_length_mm`, `wheel_radius_mm` and more), and `execute_cadquery_script(ex, flatten_color=False)` gives `ok` with `multi_color`
  - each example: `set_params` bumping one key by +2 still runs `ok` in the sandbox, and the value actually changes the geometry (the GLB bounds differ)
  - each example has no bare numeric `.box(`/`.sphere(`/`.circle(` literals left for a dimension that is in PARAMS. This is a soft check: the example's first non-import statement is the `PARAMS` assignment
  - the prompt mentions `PARAMS = {` and `<part>_<dimension>_mm`. `REPAIR_PROMPT` mentions `PARAMS`. `json.loads(_build_user_payload("x", current_script="s"))["instruction"]` mentions `PARAMS`
- [ ] **Step 2:** Run and see them fail.
- [ ] **Step 3: Implement.** Add a `## PARAMS (required)` section to the prompt. Rewrite the three examples so each
  starts (after imports) with `PARAMS = {...}` and reads every dimension from it. Update the
  Follow-ups bullets, the `_build_user_payload` instruction, and one line in `REPAIR_PROMPT`.
  Escaping: the prompt is a normal `"""` string, so `\\n` in the JSON sample stays as it is.
  `REPAIR_PROMPT` uses `.format`, so any braces added there must be doubled.
- [ ] **Step 4:** Tests pass. Run `python -m ai.test_intent` too, since the prompt changes must not break it.
- [ ] **Step 5:** Commit `Require PARAMS dict in CAD codegen prompt and examples`.

### Task 6: Docs and full suite

- [ ] Update `/Users/dimural/Perception_HTN_2026/ai-docs/04-cad-lane.md` with a "PARAMS" section (convention, `cad/params.py` API, ast-only, byte-preserving, part-name contract, GLTFLoader name sanitising note).
- [ ] Run `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh`. It must be green.
- [ ] Commit any remaining test or doc-adjacent changes. `ai-docs` is git-excluded, so there is nothing to commit for it.
