# WS-C: proposed additions to `ai-docs/04-cad-lane.md`

The harness blocks edits outside the worktree, so the coordinator should apply this text
to `/Users/dimural/Perception_HTN_2026/ai-docs/04-cad-lane.md`. Two edits:

1. Insert the new section below **before** `## Legacy template path`.
2. In `## Colour and the flatten_color flag`, after the sentence
   "`_export_assembly` (~L355) preserves them.", add:
   "It also preserves part **names**: each `assy.add(name=...)` becomes a GLB mesh node
   (and mesh) of exactly that name, on both colour paths. See the PARAMS section."

---

## PARAMS: named dimensions (in-headset editing, spec §3)

Every generated CAD script declares its dimensions up front, right after the imports:

```python
PARAMS = {"head_radius_mm": 16, "ear_length_mm": 16, "lug_hole_d_mm": 3.2}
head = cq.Workplane("XY").sphere(PARAMS["head_radius_mm"])
```

That is what makes a small CAD edit AI-free. The server rewrites one number and reruns the
sandbox, with no Gemini call.

### Convention (enforced by the prompt, not by code)

| Rule | Where |
|---|---|
| One module-level `PARAMS = {...}` dict literal, first statement after the imports | `CODEGEN_SYSTEM_PROMPT` "## PARAMS (required)" |
| Keys are `<part>_<dimension>_mm`, lower_snake_case; `<part>` is the `assy.add` name; mirrored/repeated parts use the base name (`ear_l`/`ear_r` → `ear_*`, `wheel_fl..rr` → `wheel_*`) | same |
| Values are plain positive numbers, never expressions | same |
| Every `assy.add` part has at least one key | same |
| Follow-ups edit `PARAMS` values rather than inline numbers, and add `PARAMS` if missing | prompt "## Follow-ups", `_build_user_payload` instruction |
| Repairs keep `PARAMS` | `REPAIR_PROMPT` |

All three prompt examples (character, keychain, toy car) follow the convention.
`cad/test_params.py` runs each through the real sandbox and checks three things: the
first statement is `PARAMS`, every GLB part maps to at least one param, and bumping one
param changes the model's bounds. **If you edit an example, that test is your guard.**

### `cad/params.py` (pure, stdlib only)

| Function | Behaviour |
|---|---|
| `extract_params(script) -> dict[str, float]` | `ast.parse` only, **never executes**. Last module-level `PARAMS = {...}` (plain or annotated). `{}` if absent, on syntax error, or if any key isn't a str literal or any value isn't a (signed) int/float literal (bools rejected) |
| `set_params(script, updates) -> str` | Rewrites only those values, every other byte identical (comments, spacing, CRLF, non-ASCII). Splices at AST value-node offsets on the **UTF-8 bytes**, because `ast` column offsets count bytes. All updates are validated first, so a bad one applies nothing. Duplicate keys are all rewritten. Values are rounded to 4 dp; integral values are written as ints (`12`, not `12.0`) |
| `ParamError(ValueError)` | No PARAMS literal, unknown name, or a value that isn't a finite positive number (bools rejected). **Message is speakable**, e.g. "This model has no dimension called ear width." |
| `params_for_part(params, part)` | Keys starting with `part_`, plus the base name after stripping trailing `_l/_r/_left/_right/_fl/_fr/_rl/_rr/_front/_rear/_back/_<digits>` repeatedly (`ear_l_2` → `ear`). `_<digits>` also covers the sandbox's duplicate-name suffix |

A script without `PARAMS` (older sessions, or a model that ignored the rule) still builds
normally. `extract_params` returns `{}` and the client panel shows overall size only.

### Part names reach the GLB

`_export_assembly` adds each part with `geom_name=name, node_name=name`. The names survive
`_paint_glb`'s reload/re-export on the `flatten_color=True` path. Guarded by
`cad/test_sandbox.py:test_assembly_glb_node_names`, which checks both colour paths and
also metres, watertightness and colours. Client side, Three.js `GLTFLoader` sanitises
node names (it strips spaces, `.`, `:`, `/`, `[]`), which is harmless for snake_case names.

### Gotcha: helper functions cannot see `PARAMS`

`_run_in_sandbox` calls `exec(script, safe_globals, local_vars)` with **separate** globals
and locals. Module-level assignments land in `local_vars`, and a `def` body looks names
up in globals, so any helper that reads `PARAMS` (or any top-level name) fails with
`NameError: name 'X' is not defined`. The prompt and `REPAIR_PROMPT` both say to pass
every dimension in as an argument, and the examples do. Module-level `for` loops and
list comprehensions (inlined in 3.12) are fine; generator expressions and nested
functions are not. Fixing it in the sandbox (one namespace dict) would remove this whole
class of codegen failures, but it touches the security module, so it is left as a
separate decision.
