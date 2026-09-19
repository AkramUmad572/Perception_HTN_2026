# In-headset editing: master plan

> **For agentic workers:** This is the coordination plan. Each workstream agent writes its
> own detailed TDD plan at `docs/superpowers/plans/2026-09-19-ws-<letter>-<name>.md`, commits
> it, then executes it with superpowers:executing-plans (TDD per task, commit per task).

**Goal:** Implement `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md` in
parallel workstreams that merge cleanly into `feature/in-headset-editing`.

**Architecture:** Wave 1 builds four independent units in separate worktrees (backend
versioning, client navigation/measurement, CAD params core, mesh boolean core). Wave 2 wires
them together and adds selection. Wave 3 adds semantic mesh edits and Drive.

**Tech stack:** FastAPI + pydantic v2, CadQuery 2.8, trimesh 4 + manifold3d, Three.js WebXR
(Vite), Node 20 for client pure-function tests.

**Spec:** `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md`

## Global constraints (every task)

- **Commits must NOT contain a `Co-Authored-By: Claude` trailer or any Claude attribution.**
- Python: use `/Users/dimural/Perception_HTN_2026/.venv/bin/python` (3.12, has cadquery,
  trimesh, manifold3d, fastapi). Run backend tests from `backend/` as `python -m <pkg>.test_<x>`.
- Test style matches the repo: plain scripts with a `__main__` runner that prints
  `TOTAL: n/m passed` and exits non-zero on failure. **No pytest.** Register every new suite
  in `run_tests.sh`.
- Full suite must stay green: `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh`.
- Respect `ai-docs/09-invariants.md`. The only invariants this project may change are the
  three listed in the spec's "Invariants that change" section.
- `reply` strings are spoken aloud: no paths, no exception names, no markdown.
- Routes never raise to the client: catch, log, return `ok=False, action="clarify"`.
- New client logic goes in `web-client/src/interaction/*.js` modules, not into `main.js`
  beyond thin wiring. Pure math lives in functions that Node can test without Three.js
  where possible.
- **Docs:** `ai-docs/` is git-excluded and exists only in the main checkout at
  `/Users/dimural/Perception_HTN_2026/ai-docs/`. Edit **only your assigned note** there,
  by absolute path. The coordinator updates shared notes (`README.md`, `09`, `10`, `11`).
- **Context rule:** at ~30% of your context window used, stop at a clean point, commit, write
  `docs/superpowers/handoffs/2026-09-19-ws-<letter>-handoff-<n>.md` (done / in progress /
  next step / files / gotchas / these global constraints), commit it, and return. Say so
  in your final message; the coordinator resumes with a fresh agent.

---

## Wave 1: parallel (separate worktrees)

### WS-A: Projects, versions, undo/redo, cleanup (spec §1, §2 server side)

**ai-docs note:** `08-state-and-contracts.md`

**Files:** create `backend/app/projects.py`, `backend/app/test_projects.py`; modify
`app/models.py`, `app/config.py`, `app/pipeline.py`, `app/main.py`, `ai/intent.py`
(undo/redo rung only), `ai/test_intent.py`, `app/test_pipeline.py`, `run_tests.sh`.

**Produces (exact interface, later waves depend on it):**

```python
# app/models.py
class VersionInfo(BaseModel):
    project_id: str
    version: int                 # 1-based, monotonically increasing, never reused
    kind: str                    # "cad" | "mesh"
    op: str                      # "generate" | "set_material" | "hand_edit" | "param_edit" | "boolean" | "semantic_edit" | "photo" | "script"
    glb_url: str                 # /media/projects/<project_id>/v<version>.glb
    summary: str | None = None
    script: str | None = None    # CAD only
    params: dict[str, float] = {}  # CAD PARAMS snapshot, filled by WS-C wiring later
    mesh_prompt: str | None = None
    color: str | None = None
    base_size_m: float | None = None
    parent: int | None = None
    created_at: float

class SessionState:  # add
    project_id: str | None = None
    version: int | None = None

# app/config.py: Settings.projects_dir = STORAGE / "projects"  (mkdir in get_settings)

# app/projects.py
MAX_VERSIONS = 20
AUDIO_TTL_S = 3600; REF_TTL_S = 86400; PROJECT_TTL_S = 7 * 86400
def create_project(settings, kind: str, glb_src: Path, **meta) -> VersionInfo   # moves glb to v1
def append_version(settings, project_id: str, glb_src: Path, **meta) -> VersionInfo  # truncates redo tail, prunes
def get_version(settings, project_id: str, version: int) -> VersionInfo | None
def current_version(settings, project_id: str) -> VersionInfo | None
def undo(settings, project_id: str, steps: int = 1) -> VersionInfo | None      # None if nothing to undo
def redo(settings, project_id: str, steps: int = 1) -> VersionInfo | None
def prune(settings, project_id: str, keep: int = MAX_VERSIONS) -> None      # keeps v1 + newest keep-1
def cleanup(settings, now: float | None = None) -> dict[str, int]           # counts deleted per kind; skips projects with saved_to_drive
def mark_saved_to_drive(settings, project_id: str) -> None
```

`info.json` per project: `{"current": int, "versions": [VersionInfo...], "saved_to_drive": bool, "touched_at": float}`, written via temp file + `os.replace` like `app/session.py`.

**Behaviour:**
- Every successful build path in `pipeline.py` that sets `rebuilt=True` records a version:
  a new object → `create_project`, a follow-up on the same object → `append_version`. The
  response `glb_url`/`model_id` point at the version file (`model_id = f"{project_id}-v{n}"`,
  so it is always new).
- Session follow-up fields (`last_script`, `last_summary`, `last_backend`,
  `last_mesh_prompt`, `color`, `base_size_m`) are restored from the version on undo/redo.
- Routes: `GET /api/projects/{pid}`, `POST /api/projects/{pid}/undo` and `/redo`
  (JSON `{steps, session_id}`), `POST /api/projects/{pid}/versions` (multipart: `glb`
  file + `op` + optional `summary`, `session_id`) → `rebuilt=False, action="version_saved"`.
  Undo/redo respond `rebuilt=True` with the stored version's URL (normal client swap).
- Mount `/media/projects` → `settings.projects_dir`.
- Router: `undo`, `go back`, `go back two`, `redo` → `Intent(action="undo"|"redo", params={"steps": n})`,
  a rung **above** colour/scale/codegen, no network call. Add cases to `ai/test_intent.py`.
- Cleanup: run once on startup and every hour (asyncio task in `main.py` lifespan/startup).

### WS-B: Client navigation, dimensions label, tape measure, real size (spec §6 partly, Phase 1)

**ai-docs note:** `07-client-xr.md`

**Files:** create `web-client/src/interaction/twoHand.js`, `measure.js`, `tapeMeasure.js`,
`dimensionsLabel.js`, `test_interaction.js`; modify `web-client/src/main.js` (wiring only),
`backend/ai/intent.py` (absolute-size rung only), `backend/ai/test_intent.py`, `app/pipeline.py`
(absolute size in the `set_scale` branch only), `run_tests.sh`.

**Produces:**

```js
// twoHand.js: pure, no Three.js import; vectors are {x,y,z}
export function twoHandTransform(prevL, prevR, curL, curR)
  // -> { scale: number, yaw: number /* radians about world Y */, translate: {x,y,z} }
// measure.js
export function distanceM(a, b)                      // metres
export function formatLength(m)                      // "4.2 mm" < 1 cm, "12.5 cm" < 1 m, else "1.20 m"
export function snap(value, step)                    // round to step
export const FINE_MODE_FACTOR = 0.1
// tapeMeasure.js / dimensionsLabel.js: Three.js objects built from the above
export function createTapeMeasure(scene) // -> { setStart(p), setEnd(p), clear(), update() }
export function createDimensionsLabel()  // -> { object3d, setSize({x,y,z} metres), follow(model) }
```

**Behaviour:**
- Both hands pinching on/near the model = two-hand mode: pinch-spread scales, twisting
  rotates about vertical, moving both translates. Scaling here changes display only; on
  release it calls nothing server-side (visual zoom, not a model edit).
- The dimensions label floats beside the model showing W × H × D using `formatLength`,
  with real sizes taken from the loaded object's world-space bounds.
- Tape measure: a toggle (desktop key `T`, and an overlay button); two right-hand
  pinches on the surface place endpoints via raycast; the label shows the distance.
- Voice "make it 8 cm tall" / "make it 50 millimetres wide": new rung parses an absolute
  size → `Intent(action="set_scale", params={"target_m": 0.08})` **for mesh sessions only**.
  The pipeline sets `scale = target_m / base_size_m`. CAD sessions keep going through
  codegen, which already handles "make it 8 cm tall" in real mm; never display-scale a CAD
  model, because CAD is shown life size (`ai-docs/04-cad-lane.md`).

### WS-C: CAD PARAMS core (spec §3 core)

**ai-docs note:** `04-cad-lane.md`

**Files:** create `backend/cad/params.py`, `backend/cad/test_params.py`; modify
`backend/ai/intent.py` (`CODEGEN_SYSTEM_PROMPT` + follow-up instructions + examples only),
`backend/cad/sandbox.py` (part names in GLB only), `backend/cad/test_sandbox.py`, `run_tests.sh`.

**Produces:**

```python
# cad/params.py
class ParamError(ValueError): ...
def extract_params(script: str) -> dict[str, float]
    # ast-parses the module-level `PARAMS = {...}` literal; {} when absent or not a literal dict of numbers
def set_params(script: str, updates: dict[str, float]) -> str
    # rewrites only those values in the PARAMS literal, preserving all other text byte-for-byte;
    # ParamError on unknown name, missing PARAMS, or non-finite / non-positive value
def params_for_part(params: dict[str, float], part: str) -> dict[str, float]
    # keys starting with f"{part}_" (also matches "ear" for part "ear_l" via the base name before "_l"/"_r")
```

**Behaviour:**
- Prompt: every CAD script starts with `PARAMS = {"<part>_<dim>_mm": value, ...}` and the
  body reads dimensions from it; follow-ups update `PARAMS` rather than inline numbers. Update
  all three examples in the prompt to follow the convention.
- GLB export: every assembly part becomes a GLB node named exactly as its `assy.add(name=...)`.
  Add a sandbox test that builds a 2-part assembly and checks node names in the exported GLB.
- No endpoint in this wave (it needs WS-A); WS-C only guarantees the functions + prompt + names.

### WS-D: Mesh boolean core (spec §5 booleans core)

**ai-docs note:** `05-mesh-lane.md`

**Files:** create `backend/mesh/boolean.py`, `backend/mesh/test_boolean.py`; modify
`backend/requirements.txt` (add `manifold3d`), `run_tests.sh`.

**Produces:**

```python
# mesh/boolean.py: all coordinates are model-local GLB units; sizes are mm,
# converted with units_per_mm (model units per real millimetre)
class BooleanError(ValueError): ...   # message is speakable
def load_mesh(glb_path: Path) -> trimesh.Trimesh
    # concatenates the scene; bakes texture to vertex colours first so booleans keep appearance
def repair(mesh) -> trimesh.Trimesh     # merge verts, fix normals, fill holes; BooleanError if still not watertight
def drill_hole(mesh, center, direction, diameter_mm, units_per_mm, depth_mm=None) -> trimesh.Trimesh  # None = through
def add_loop(mesh, center, normal, units_per_mm, outer_d_mm=10.0, hole_d_mm=4.0, thickness_mm=3.0) -> trimesh.Trimesh
def flatten_base(mesh, cut_fraction=0.05) -> trimesh.Trimesh      # cuts the lowest cut_fraction of height (Y-up)
def export_glb(mesh, dest: Path) -> None
```

**Behaviour:** booleans use `engine="manifold"`. New faces take the colour of the nearest
original vertex. Tests use synthetic meshes (icosphere, a deliberately holed mesh for repair,
a textured box written to GLB) and must check watertightness, volume change direction, and
that colours survive.

---

## Wave 2 (after Wave 1 merges): coordinator writes detailed plans then

- **WS-E wiring C:** `params` on `VersionInfo`/`CommandResponse`, `POST /api/projects/{pid}/params`
  (no LLM, rerun sandbox, new version, `op="param_edit"`), client dimension panel with drag,
  fine mode, snapping, live label; CAD absolute size via PARAMS when available.
- **WS-F selection + client region ops (spec §4, §5 deterministic, §6):** `interaction/selection.js`,
  `interaction/regionOps.js`, GLB export + upload to `/versions`, `selection` on `/api/voice` and
  `/api/command`, router passes selection into codegen payload.
- **WS-G wiring D:** router rung for hole/loop/flat-base on mesh with selection → `mesh/boolean.py`
  → new version; narrows `clarify_mesh` to everything else. Update invariants.

## Wave 3

- **WS-H semantic mesh edits (spec §5 semantic):** render → Gemini image edit with mask →
  existing image-to-3D lane → new version.
- **WS-I Save to Drive (spec §7).**

## Merge protocol (coordinator)

1. Each WS returns with its branch committed and its suite green.
2. Merge into `feature/in-headset-editing` in order A, C, D, B (A touches the most shared files).
   Expected conflicts: `run_tests.sh`, `ai/intent.py`, `ai/test_intent.py`, `app/pipeline.py`.
3. Full `run_tests.sh` after each merge; fix before the next.
4. Update `ai-docs/README.md`, `09-invariants.md`, `10-file-map.md`, `11-editing-roadmap.md`.

---

## Wave 2 addendum (token-lean: 2 Sonnet agents, no per-agent plans)

Workflow change (user-approved): agents code directly from this addendum, TDD, run **only their own suites**
while working, and send short reports. The coordinator runs the full suite at merge. Hand off only at ~60% context.

### Shared: `Selection` (defined by WS-FG in `app/models.py`; WS-E does not touch it)

```python
class Selection(BaseModel):
    parts: list[str] = []            # GLB node names hit (CAD part names; empty for most sculpts)
    center: list[float]              # [x,y,z] model-local GLB units (CAD GLB units are metres)
    normal: list[float]              # outward surface normal at center, model-local
    radius: float = 0.0              # model-local units; 0 = a point, not a circle
```
Sent as a JSON field `selection` on `/api/command` (`CommandRequest.selection: Selection | None`) and as a
multipart form field `selection` (JSON string) on `/api/voice`. It is passed through to `parse_intent(..., selection=...)`.

### WS-E: CAD params wiring + dimension panel

- Every CAD version records `params = cad.params.extract_params(script)` (`pipeline._record_version`).
- `CommandResponse.cad_params: dict[str, float] = {}`, filled from the session's current CAD version.
- `POST /api/projects/{pid}/params` body `{"updates": {name: value}, "session_id": "default"}` → `set_params`
  → the existing sandbox execute (no LLM) → `append_version(op="param_edit")` → `rebuilt=True`. A `ParamError` →
  `ok=False, action="clarify", reply=str(err)`.
- Client `web-client/src/interaction/paramPanel.js`: a floating list of the params for the pointed part
  (`params_for_part` logic mirrored in JS, or all of them); pinch-drag a row to change the value using `snap(…, 1mm)`,
  `FINE_MODE_FACTOR` while the left hand pinches; a live label via `formatLength`; POST on release. Main.js: wiring only.
- Files: `app/models.py` (cad_params only), `app/pipeline.py`, `app/main.py`, `app/test_pipeline.py`,
  `web-client/src/interaction/paramPanel.js`, `web-client/src/main.js`, `web-client/src/interaction/test_interaction.js`.

### WS-FG: selection, client region ops, mesh booleans wired

- Client `interaction/selection.js`: right ray/finger → short pinch = point, pinch-drag = lasso → `Selection`
  (snap to a whole part when ≥60% of the stroke hits one node). Highlight until cleared/used. Attach it to the next
  voice/text command (`PercyAssistant` sends the `selection` form field).
- Client `interaction/regionOps.js`: pure vertex ops with smooth falloff over the selection: `scaleRegion`,
  `pullRegion(±)`, `flattenRegion`, `smoothRegion`, `paintRegion`. A small local voice table ("bigger", "smaller",
  "pull out", "push in", "flatten", "smooth", "paint <colour>") runs them **client-side, with no server call**, then
  GLTFExporter → `POST /api/projects/{pid}/versions` (op `hand_edit`). The client handles `action="version_saved"`
  by updating its version pointer only (no reload).
- Router: when a selection is present on a CAD session, codegen payload gets
  `"selection": "user pointed at <parts> near (x,y,z) mm"`. On a **mesh** session, a new rung before `clarify_mesh`:
  hole / loop / flat base (+ optional size in mm) → `Intent(action="mesh_boolean", params={"op": "hole"|"loop"|"flat_base", "diameter_mm"?})`.
  Without a selection, hole/loop → clarify "Point at where you want it first."; flat base needs no selection.
- Pipeline `mesh_boolean`: `load_mesh` the current version GLB → op with `units_per_mm = longest_extent /
  (base_size_m * scale * 1000)` → `export_glb` → `append_version(op="boolean")` → `rebuilt=True`. `BooleanError` →
  clarify with its message, model unchanged. `clarify_mesh` stays for everything else.
- Files: `app/models.py` (Selection, CommandRequest.selection), `app/main.py` (/api/voice form field), `ai/intent.py`,
  `ai/test_intent.py`, `app/pipeline.py`, `app/test_pipeline.py`, `web-client/src/interaction/selection.js`,
  `regionOps.js`, `test_interaction.js`, `web-client/src/voice/PercyAssistant.js`, `web-client/src/main.js`.

**Expected overlap:** `models.py`, `pipeline.py`, `main.py`, `test_pipeline.py`, `main.js`, `test_interaction.js`:
all additive; the coordinator resolves them at merge.
