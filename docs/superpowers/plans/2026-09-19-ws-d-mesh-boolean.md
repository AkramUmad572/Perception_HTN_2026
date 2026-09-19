# WS-D Mesh Boolean Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure-Python module `backend/mesh/boolean.py` that loads a sculpt GLB, makes it watertight, and applies three boolean features (drill a hole, add a hanging loop, flatten the base) while keeping the sculpt's appearance.

**Architecture:** `load_mesh` flattens the GLB scene into one `trimesh.Trimesh` with the texture baked to per-vertex colours. `repair` runs a ladder of increasingly aggressive fixes, all on a colourless copy, and then transfers colours back by nearest vertex. Each feature builds a primitive cutter or adder in model-local units (`mm * units_per_mm`), runs a `trimesh` boolean with `engine="manifold"`, checks that the result actually changed, and re-colours every output vertex from the nearest input vertex. Failures raise `BooleanError` with a sentence that can be spoken aloud. Nothing is wired into the router or pipeline here (that is WS-G).

**Tech Stack:** Python 3.12, trimesh 5.1, manifold3d 3.x, numpy, scipy (cKDTree), Pillow (test texture only).

**Spec:** `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md` §5 (Boolean features), coordinated by `docs/superpowers/plans/2026-09-19-in-headset-editing-master.md` (WS-D).

## Global Constraints

- Commits must NOT contain a `Co-Authored-By: Claude` trailer or any Claude attribution.
- Python: `/Users/dimural/Perception_HTN_2026/.venv/bin/python`. Run backend tests from `backend/` as `python -m mesh.test_boolean`.
- Tests are plain scripts with a `__main__` runner that prints `TOTAL: n/m passed` and exits non-zero on failure. No pytest. Register the suite in `run_tests.sh`.
- Full suite green: `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh` from the worktree root.
- `BooleanError` messages are spoken: no paths, no exception names, no markdown, no underscores; one or two plain sentences.
- Synthetic meshes only, no network.
- The only ai-docs edit is `/Users/dimural/Perception_HTN_2026/ai-docs/05-mesh-lane.md` (absolute path, git-excluded).
- No router or pipeline wiring.

## Interface (exact, from the master plan)

```python
# mesh/boolean.py: all coordinates are model-local GLB units; sizes are mm,
# converted with units_per_mm (model units per real millimetre)
class BooleanError(ValueError): ...   # message is speakable
def load_mesh(glb_path: Path) -> trimesh.Trimesh
def repair(mesh) -> trimesh.Trimesh
def drill_hole(mesh, center, direction, diameter_mm, units_per_mm, depth_mm=None) -> trimesh.Trimesh
def add_loop(mesh, center, normal, units_per_mm, outer_d_mm=10.0, hole_d_mm=4.0, thickness_mm=3.0) -> trimesh.Trimesh
def flatten_base(mesh, cut_fraction=0.05) -> trimesh.Trimesh
def export_glb(mesh, dest: Path) -> None
```

Conventions fixed by this plan (WS-G relies on them):
- `direction` in `drill_hole` points **into** the model (the drilling direction). `center` is the surface point.
- `normal` in `add_loop` points **out of** the surface. The ring stands on the surface with its hole above it and its axis perpendicular to the normal.
- `flatten_base` does not translate the result; the model keeps its local frame.
- Every feature op calls `repair` on its input first, so callers can pass the raw `load_mesh` output.
- Every returned mesh is watertight and has `visual.kind == "vertex"`.

## Files

- Create `backend/mesh/boolean.py`: the module (one responsibility: boolean features on sculpts).
- Create `backend/mesh/test_boolean.py`: the synthetic-mesh suite.
- Modify `backend/requirements.txt`: add `manifold3d>=3.0.0` and `scipy>=1.11.0`, since cKDTree is imported directly.
- Modify `run_tests.sh`: add a "Mesh Boolean Tests" block.

Prototype findings (verified in scratch before writing this plan):
- A trimesh boolean with `engine="manifold"` returns `visual.kind None`. Colours must be re-transferred.
- `trimesh.repair.fill_holes` only fills trivial holes. It failed on a 3-face gap in an icosphere.
- Filling each boundary loop with a centroid fan closes arbitrary holes. The loop walk must split out a sub-cycle whenever it revisits a vertex (pinch vertices), or the centroid edges get used 4 times.
- scikit-image is not installed, so voxel/marching-cubes remeshing is unavailable. Don't use it.

---

### Task 1: Module skeleton, `load_mesh`, `export_glb`, colour baking

**Files:** create `backend/mesh/boolean.py` and `backend/mesh/test_boolean.py`; modify `backend/requirements.txt` and `run_tests.sh`.

**Produces:** `BooleanError`, `load_mesh`, `export_glb`, `_transfer_colors(dst, src) -> dst`, and test helpers `_textured_box_glb(dest)` (a unit cube: red where x<0, blue where x>0) and `_is_speakable(msg)`.

- [x] **Step 1: Write the failing test.** Tests: `test_load_bakes_texture` (loads the textured GLB, then checks vertex kind, that x<-0.1 is red and x>0.1 is blue, and volume≈1); `test_load_flattens_scene` (two boxes as separate nodes, one translated, come out as one mesh with the right bounds); `test_export_roundtrip_keeps_colors`; `test_load_missing_file_speakable`. The runner prints `TOTAL`.
- [x] **Step 2: Run** `cd backend && python -m mesh.test_boolean`. It should fail with an ImportError.
- [x] **Step 3: Implement.** `load_mesh`: `trimesh.load(path, force=None)`. Wrap a bare Trimesh in a Scene, then `scene.dump()` (a list of transformed geometries). For each Trimesh, `visual.to_color()` → `vertex_colors`, then build a `Trimesh(v, f, vertex_colors=c, process=False)`. Concatenate the pieces. Raise `BooleanError("I couldn't open that model to edit it.")` on a load failure or when there's no geometry. `export_glb`: `dest.write_bytes(trimesh.exchange.gltf.export_glb(trimesh.Scene(mesh)))`. `_transfer_colors`: build a cKDTree on the source vertices, query the destination vertices, and index into the source colours.
- [x] **Step 4: Run it and confirm it passes.** Add `manifold3d` and `scipy` to requirements, and add the runner block to `run_tests.sh`.
- [x] **Step 5: Commit** with the message `Add mesh boolean module skeleton with texture-to-vertex-colour loading`.

### Task 2: `repair`

**Produces:** `repair(mesh)`, which returns a new watertight mesh (`is_volume`) with colours transferred from the input, or raises `BooleanError`.

Ladder (stop at the first rung where `_solid(m)` is true, meaning `m.is_watertight and m.is_winding_consistent and m.volume > 0`):
1. A colourless copy `Trimesh(v, f, process=True)`. Then `merge_vertices()`, drop degenerate and duplicate faces, drop unreferenced vertices, and `fix_normals(multibody=True)`.
2. Merge vertices again at a relative tolerance (`digits` from 1e-6 × the bounding diagonal). This closes UV seams that are split by float noise.
3. Remove every face that touches a non-manifold edge (an edge used by 3 or more faces). Those become holes for the next rung.
4. `trimesh.repair.fill_holes`, then `_fill_boundary_loops` (a centroid fan per boundary loop with sub-cycle splitting), then merge and `fix_normals`.
5. Drop components that are still open, provided they hold less than 5% of the faces (debris). Keep the watertight ones.

If none of these produces a solid, raise `BooleanError("This sculpt has gaps I couldn't close, so I can't cut into it. The model is unchanged.")`. An empty mesh raises `BooleanError("There's no model to edit.")`. Negative volume after `fix_normals` is corrected with `invert()`.

- [x] **Step 1: Failing tests.** `test_repair_watertight_passthrough` (volume unchanged); `test_repair_small_hole` (3 faces removed from an icosphere); `test_repair_big_hole` (the cap y>0.7 removed); `test_repair_scattered_holes` (3% random faces removed, seed 0); `test_repair_split_seam` (a box with unmerged vertices, each face its own vertices); `test_repair_debris` (a sphere plus an open lone triangle far away); `test_repair_keeps_colors` (the textured box); `test_repair_hopeless_raises_speakable` (a flat open square of 2 triangles gives zero volume, so `BooleanError`, and `_is_speakable`).
- [x] **Step 2: Run** and confirm they fail with an AttributeError or ImportError on `repair`.
- [x] **Step 3: Implement** the ladder above.
- [x] **Step 4: Run** and confirm all pass.
- [x] **Step 5: Commit** with the message `Add watertight repair ladder for mesh booleans`.

### Task 3: `drill_hole`

**Produces:** `drill_hole(mesh, center, direction, diameter_mm, units_per_mm, depth_mm=None)`.

A cylinder of radius `diameter_mm/2*units_per_mm` along `direction`:
- through (`depth_mm is None`): it spans `center ± direction * 2 * diag`.
- blind: it spans from `center - direction * lead` to `center + direction * depth`, where `lead = max(radius, 1 mm)` so it starts outside the surface.

The mesh is repaired, then `difference(cutter, engine="manifold")`. Validation errors (all speakable):
- `units_per_mm <= 0` or not finite: "I don't know this model's real size yet, so I can't size the hole."
- `diameter_mm <= 0`: "The hole needs a size bigger than zero."
- `depth_mm <= 0`: "The hole needs a depth bigger than zero."
- a zero `direction`: "I couldn't tell which way to drill."
- a boolean exception, or an empty or non-watertight result: "The cut didn't work on this shape. The model is unchanged."
- volume unchanged (relative change < 1e-6): "That spot missed the model, so there was nothing to drill."

On success, the result is re-coloured via `_transfer_colors(result, repaired)`.

- [x] **Step 1: Failing tests.** Through hole on an icosphere: watertight, volume drops by about π r² × chord (loose bounds). Blind hole: volume drops less than a through hole of the same diameter. Hole in the repaired big-hole sphere is watertight. Textured box with a 0.2-unit hole at x=-0.25 along -Y: vertices with x<-0.1 are all red and x>0.1 all blue; the export/load round-trip keeps them; no vertex is grey. A miss (centre far away) raises a speakable error. Bad diameter or units raise speakable errors. A `units_per_mm` conversion check: with `units_per_mm=0.01` and `diameter_mm=20`, the hole radius is 0.1 model units (measure the hole wall vertices' distance from the axis, ≈0.1).
- [x] **Step 2: Run** and confirm they fail.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** and confirm they pass.
- [x] **Step 5: Commit** with the message `Add drill_hole boolean with colour transfer`.

### Task 4: `add_loop`

**Produces:** `add_loop(mesh, center, normal, units_per_mm, outer_d_mm=10.0, hole_d_mm=4.0, thickness_mm=3.0)`.

`trimesh.creation.annulus(r_min=hole_r, r_max=outer_r, height=thickness)` has its axis along Z. Rotate Z onto an `axis` that is perpendicular to `normal`: take `cross(normal, up)` with up = +Y, falling back to +X when that is parallel. Translate to `center + normal * (hole_r + (outer_r - hole_r) / 2)`. That puts the ring's bottom half a wall's depth inside the surface and its hole fully outside. Then `union(engine="manifold")`. Validation: `hole_d_mm >= outer_d_mm` gives "The loop's hole has to be smaller than the loop." Non-positive sizes give "The loop needs a size bigger than zero." Units, the normal, and the boolean failure use the same messages as the hole. Attachment check: `result.volume < repaired.volume + ring.volume - 1e-9 * scale` (they must overlap), otherwise "That spot missed the model, so the loop would float in the air." Colours come via `_transfer_colors`.

- [x] **Step 1: Failing tests.** A loop on the top of an icosphere (centre (0,1,0), normal +Y) is watertight with a bigger volume, one body (`len(result.split(only_watertight=False)) == 1`), max y above the sphere top (> 1.0) and below 1 + outer_d (loop height bound). A loop away from the model raises. `hole >= outer` raises. Textured box: the loop on top at x=-0.3 takes red.
- [x] **Step 2: Run** and confirm they fail.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** and confirm they pass.
- [x] **Step 5: Commit** with the message `Add add_loop boolean`.

### Task 5: `flatten_base`

**Produces:** `flatten_base(mesh, cut_fraction=0.05)`.

The cut plane is `y_cut = ymin + cut_fraction * height`. The cutter is a box spanning x and z at 3× the extents around the centre, from `ymin - height` up to `y_cut`. `difference(engine="manifold")`. Validation: `not 0 < cut_fraction < 0.5` gives "I can only trim a small slice off the bottom." Boolean failure and an empty result reuse the cut message. Colours come via `_transfer_colors`.

- [x] **Step 1: Failing tests.** For an icosphere: watertight, a smaller volume, `bounds[0][1] ≈ ymin + 0.05*h` (tol 1e-6), a flat-bottom check (the faces at min y have normals ≈ -Y and a total area > 0). Bad fraction raises a speakable error. Textured box: flattening keeps red/blue on both sides.
- [x] **Step 2: Run** and confirm they fail.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** and confirm they pass.
- [x] **Step 5: Commit** with the message `Add flatten_base boolean`.

### Task 6: Docs and full suite

- [x] Add a "Boolean features (mesh/boolean.py)" section to `/Users/dimural/Perception_HTN_2026/ai-docs/05-mesh-lane.md` covering the interface, conventions, the repair ladder, what it can't repair, and colour transfer. Note that it is not wired yet (WS-G).
- [x] Run `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh` and confirm it's green.
- [x] Commit any final test or runner fixes. The ai-docs file is git-excluded, so it doesn't go in a commit.
