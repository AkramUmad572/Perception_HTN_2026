# WS-B: Client navigation, dimensions label, tape measure, real size — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two-hand zoom/rotate/move of the model, a floating W × H × D real-size label, a
pinch-to-pinch tape measure, and voice "make it 8 cm tall" for sculpts.

**Architecture:** All math lives in pure ES modules under `web-client/src/interaction/`
(`measure.js`, `twoHand.js`) and is tested by a Node script. Thin Three.js wrappers
(`dimensionsLabel.js`, `tapeMeasure.js`, shared `textSprite.js`) render it. `main.js` only
wires inputs to them. Backend gets one new router rung (`_check_absolute_size`, mesh sessions
only) and a `target_m` branch in the pipeline's `set_scale`.

**Tech Stack:** Three.js 0.181 (Vite), Node 20 ESM for client tests, Python 3.12 plain-script tests.

**Spec:** `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md` (§6, capabilities
table) and the WS-B section of `docs/superpowers/plans/2026-09-19-in-headset-editing-master.md`.

## Global Constraints

- Commits must NOT contain a `Co-Authored-By: Claude` trailer or any Claude attribution.
- Python: `/Users/dimural/Perception_HTN_2026/.venv/bin/python`; backend tests run from `backend/` as `python -m <pkg>.test_<x>`.
- Tests are plain scripts printing `TOTAL: n/m passed`, non-zero exit on failure. No pytest. Every new suite registered in `run_tests.sh`.
- Full suite green: `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh`.
- `reply` strings are spoken: no paths, exception names or markdown. Routes never raise.
- New client logic in `web-client/src/interaction/*.js`; `main.js` thin wiring only.
- Preserve pinch hysteresis (32 mm / 45 mm) and grab rebind across mesh swap.
- Absolute size applies to mesh sessions only. Never display-scale a CAD model.
- Two-hand scale is display zoom only; nothing is sent to the server on release.
- Update only `/Users/dimural/Perception_HTN_2026/ai-docs/07-client-xr.md` (router notes go in a "pending for 03" section at its bottom).

## File map

| File | Responsibility |
|---|---|
| `web-client/src/interaction/measure.js` | `distanceM`, `formatLength`, `snap`, `FINE_MODE_FACTOR`, `applyFineMode`, `formatDimensions`, `realSize`, `labelPosition`, `tapeNextStep` |
| `web-client/src/interaction/twoHand.js` | `twoHandTransform`, `midpoint`, `applyTwoHandToPosition`, `isTwoHandActive`, `clampNavScale`, `NAV_SCALE_MIN/MAX` |
| `web-client/src/interaction/textSprite.js` | canvas-texture sprite used by both labels |
| `web-client/src/interaction/dimensionsLabel.js` | `createDimensionsLabel()` |
| `web-client/src/interaction/tapeMeasure.js` | `createTapeMeasure(scene, frame?)` |
| `web-client/src/interaction/test_interaction.js` | Node tests for the pure modules |
| `web-client/src/main.js` | wiring: two-hand mode, label refresh, tape toggle (T / overlay button), desktop wheel zoom |
| `web-client/src/voice/PercyAssistant.js` | `cancelTalk()` so a two-hand gesture does not send a voice command |
| `web-client/index.html` | overlay "Tape" button |
| `backend/ai/intent.py` | `_check_absolute_size` rung (mesh sessions) |
| `backend/app/pipeline.py` | `set_scale` with `target_m` (+ axis fraction from GLB bounds) |
| `backend/ai/test_intent.py`, `backend/app/test_pipeline.py` | new cases |
| `run_tests.sh` | register `ai.test_intent` and the interaction suite |

## Design decisions

- **Nav scale lives on `modelRoot.scale`.** `updateGrab` currently forces scale to 1; it must
  keep the decomposed scale instead so a one-hand grab after a zoom keeps the zoom. The
  loaded object's own scale (`applyDisplaySize`) stays the "real size" scale, so the label
  and tape measure divide the zoom out: label = raw GLB size × object scale; tape distance
  is measured in `modelRoot`-local coordinates.
- **Left pinch is push-to-talk.** When two-hand mode engages, the left pinch's recording is
  cancelled (`percy.cancelTalk()`), not sent.
- **Two-hand mode is off while the tape measure is on** (the right pinch is the tape tool).
- **Axis-aware absolute size.** `base_size_m` is the longest edge. For "tall"/"wide"/"deep",
  the pipeline reads the GLB bounds and scales so that axis hits the target:
  `scale = target_m / (base_size_m * extent[axis] / max(extent))`. If the GLB can't be read,
  the fraction is 1.0. GLB Y-up: tall=Y, wide=X, deep/thick=Z; "long" or no word = longest.

---

### Task 1: measure.js + Node test runner

**Files:** Create `web-client/src/interaction/measure.js`, `web-client/src/interaction/test_interaction.js`; modify `run_tests.sh`.

**Produces:**
```js
distanceM(a, b) -> number                      // {x,y,z} metres
formatLength(m) -> string                      // "4.2 mm" | "12.5 cm" | "1.20 m"; non-finite -> "--"
snap(value, step) -> number                    // step <= 0 returns value
FINE_MODE_FACTOR = 0.1
applyFineMode(delta, fine) -> number
formatDimensions({x,y,z}) -> string            // "12.5 cm × 4.2 cm × 3.0 cm" (W × H × D)
realSize(rawSize, scale) -> {x,y,z}
labelPosition(min, max, right, margin=0.03) -> {x,y,z}  // beside the box along `right`
tapeNextStep(state) -> "start" | "end"         // state: {hasStart, hasEnd}
```

- [ ] Step 1: write `test_interaction.js` (same `test()`/assert style as `test_model_update.js`, prints `TOTAL: n/m passed`, `process.exit(1)` on failure) with cases:
  - `distanceM({0,0,0},{0.03,0.04,0}) ≈ 0.05`
  - `formatLength(0.0042)==="4.2 mm"`, `formatLength(0.125)==="12.5 cm"`, `formatLength(1.2)==="1.20 m"`, `formatLength(0)==="0.0 mm"`, boundary `formatLength(0.00999)==="1.0 cm"`, `formatLength(0.99999)==="1.00 m"`, `formatLength(NaN)==="--"`
  - `snap(0.0123, 0.001)≈0.012`, `snap(7, 5)===5`, `snap(8, 5)===10`, `snap(3, 0)===3`
  - `FINE_MODE_FACTOR===0.1`, `applyFineMode(0.05,true)≈0.005`, `applyFineMode(0.05,false)===0.05`
  - `formatDimensions({x:0.125,y:0.0042,z:0.03})==="12.5 cm × 4.2 mm × 3.0 cm"`
  - `realSize({x:1,y:2,z:4},0.05)` → `{0.05,0.1,0.2}`
  - `labelPosition({-0.1,0,-0.1},{0.1,0.2,0.1},{1,0,0})` → `{x:0.13,y:0.1,z:0}`
  - `tapeNextStep({hasStart:false,hasEnd:false})==="start"`, `({true,false})==="end"`, `({true,true})==="start"`
- [ ] Step 2: `node web-client/src/interaction/test_interaction.js` → fails (module not found).
- [ ] Step 3: implement `measure.js`:
```js
export const FINE_MODE_FACTOR = 0.1;
export function distanceM(a, b) { const dx=b.x-a.x, dy=b.y-a.y, dz=b.z-a.z; return Math.sqrt(dx*dx+dy*dy+dz*dz); }
export function formatLength(m) {
  if (!Number.isFinite(m)) return "--";
  const a = Math.abs(m);
  const mm = Math.round(a * 10000) / 10;          // 0.1 mm
  if (mm < 10) return `${mm.toFixed(1)} mm`;
  const cm = Math.round(a * 1000) / 10;           // 0.1 cm
  if (cm < 100) return `${cm.toFixed(1)} cm`;
  return `${a.toFixed(2)} m`;
}
export function snap(value, step) { if (!(step > 0)) return value; return Math.round(value / step) * step; }
export function applyFineMode(delta, fine) { return fine ? delta * FINE_MODE_FACTOR : delta; }
export function formatDimensions(s) { return [s.x, s.y, s.z].map(formatLength).join(" × "); }
export function realSize(raw, scale) { return { x: raw.x*scale, y: raw.y*scale, z: raw.z*scale }; }
export function labelPosition(min, max, right, margin = 0.03) { /* centre + right * (half-extent along right + margin) */ }
export function tapeNextStep(state) { return state.hasStart && !state.hasEnd ? "end" : "start"; }
```
  `labelPosition`: centre c; half = |right.x|*(max.x-min.x)/2 + |right.y|*(…)/2 + |right.z|*(…)/2; return c + right*(half+margin).
- [ ] Step 4: run → all pass. Register in `run_tests.sh` as "Interaction Tests" (`node web-client/src/interaction/test_interaction.js`).
- [ ] Step 5: commit `Add measure.js pure helpers with Node tests`.

### Task 2: twoHand.js

**Files:** Create `web-client/src/interaction/twoHand.js`; modify `test_interaction.js`.

**Produces:**
```js
twoHandTransform(prevL, prevR, curL, curR) -> { scale, yaw, translate:{x,y,z} }
  // scale = |curR-curL| / |prevR-prevL| (1 if prev span < 1e-4)
  // yaw = heading(cur) - heading(prev), heading(v)=atan2(v.x, v.z), wrapped to (-π, π]; 0 if either XZ span < 1e-4
  // translate = midpoint(cur) - midpoint(prev)
midpoint(a, b) -> {x,y,z}
applyTwoHandToPosition(pos, prevMid, t) -> {x,y,z}   // curMid + rotY(yaw) * (scale * (pos - prevMid)), curMid = prevMid + translate
isTwoHandActive({leftPinching, rightPinching, leftNear, rightNear, tapeMode}) -> boolean
NAV_SCALE_MIN = 0.1, NAV_SCALE_MAX = 10
clampNavScale(current, factor) -> number             // new total zoom, clamped
```
rotY(θ) applied to v: `x' = x cosθ + z sinθ; z' = -x sinθ + z cosθ` (Three.js convention: rotating (0,0,1) by θ gives (sinθ,0,cosθ)).

- [ ] Step 1: tests: identical hands → scale 1, yaw 0, translate 0; spread 0.2→0.4 → scale 2; both hands moved +0.1 x → translate.x 0.1, scale 1; R rotated from +x to −z about L-R midpoint (prev L(-0.1,0,0) R(0.1,0,0); cur L(0,0,0.1) R(0,0,-0.1)) → yaw = +π/2 ± 1e-6 (heading(+x)=π/2, heading(−z)=π, diff π/2); degenerate prev span → scale 1; vertical-only span → yaw 0; wrap: yaw never exceeds π in magnitude; `applyTwoHandToPosition({0.1,0,0},{0,0,0},{scale:2,yaw:0,translate:{0,0.1,0}})` → `{0.2,0.1,0}`; with yaw π/2, pos (0,0,0.1) about origin → (0.1,0,0); `isTwoHandActive` true only when all four true and tapeMode false; `clampNavScale(9, 2)===10`, `clampNavScale(0.2,0.1)===0.1`, `clampNavScale(1,1.5)===1.5`.
- [ ] Step 2: run → fails.
- [ ] Step 3: implement as specified above.
- [ ] Step 4: run → passes.
- [ ] Step 5: commit `Add two-hand transform math with Node tests`.

### Task 3: Absolute-size router rung (mesh only)

**Files:** Modify `backend/ai/intent.py`, `backend/ai/test_intent.py`, `run_tests.sh`.

**Produces:** `_check_absolute_size(text) -> Intent | None` returning
`Intent(action="set_scale", params={"target_m": float, "axis": "height"|"width"|"depth"|None}, reply="Made it 8 centimetres tall.")`.
Called in `parse_intent` inside the existing `session_backend == "mesh" and not is_new` block,
**before** `_check_scale_only` (and so before `clarify_mesh`, which `mm` would otherwise trigger).

Parsing: `(?P<num>\d+(?:\.\d+)?|<number word>)\s*(?P<unit>mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|m|met(?:er|re)s?|inch(?:es)?)\b` then an optional adjective
`(tall|high|wide|across|long|deep|thick)`. Number words: one–twenty, thirty…ninety, hundred.
Returns None when: no number+unit; `_PART_RE` matches ("make the ears 2 cm long"); a comparative
word is present (`bigger|smaller|larger|longer|shorter|taller|wider|deeper|thicker|more|less`),
since "2 cm taller" is relative; value ≤ 0. Units → metres: mm 0.001, cm 0.01, m 1, inch 0.0254.
Axis: tall/high → "height"; wide/across → "width"; deep/thick → "depth"; long/none → None.
Reply unit words: "millimetres" / "centimetres" / "metres" / "inches", number printed with `:g`.

- [ ] Step 1: add `test_absolute_size()` to `test_intent.py` (mesh session via `parse_intent`, same settings stub as `test_scale_fast_path`): cases
  `"make it 8 cm tall"` → target 0.08 axis height; `"make it 50 millimetres wide"` → 0.05 width;
  `"make it 1.5 metres long"` → 1.5 None; `"make it eight centimeters"` → 0.08 None;
  `"make it 30 mm deep"` → 0.03 depth (proves it beats `clarify_mesh`);
  `"make it 2 cm taller"` → action != set_scale-with-target_m; `"make the ears 2 cm long"` → no target_m;
  CAD session `"make it 8 cm tall"` → action != "set_scale". Register in `run_all_tests` + summary.
  Also fix pre-existing bug: `settings_off` in `test_parse_intent_mesh_routes_without_llm` lacks `hf_space_enabled=False` (keyless HF lane makes `mesh_ready` true), so add it.
- [ ] Step 2: `cd backend && python -m ai.test_intent` → new cases fail.
- [ ] Step 3: implement `_check_absolute_size` and the call site.
- [ ] Step 4: run → all pass. Register `ai.test_intent` in `run_tests.sh` ("Intent Router Tests").
- [ ] Step 5: commit `Add absolute-size voice rung for sculpt sessions`.

### Task 4: Pipeline `set_scale` with `target_m`

**Files:** Modify `backend/app/pipeline.py`, `backend/app/test_pipeline.py`.

**Produces:**
```python
def _glb_path_from_url(settings, glb_url) -> Path | None   # /media/glb/<f> -> glb_dir/<f>; /media/projects/<pid>/<f> -> projects_dir/<pid>/<f> when settings.projects_dir is a Path
def _axis_fraction(path, axis) -> float                    # extent[axis]/max(extent); 1.0 on axis None / error
```
`set_scale` branch: if `target_m` in params:
- `session.last_backend == "cad"` → `action="clarify"`, reply "I keep CAD parts at their real size. Ask me to change the dimension and I'll rebuild it." (never display-scale CAD).
- else `wanted = target_m / (base_size_m * fraction)`, clamp as today; if clamped, reply "That's outside what I can show, so I went as far as I can."
Otherwise the existing `factor` path is unchanged.

- [ ] Step 1: tests: (a) mesh session base 0.2, target 0.08, no axis → `display_size_m≈0.08`, `session.scale≈0.4`, `rebuilt False`, action `set_scale`; (b) writes a trimesh box extents (1,2,4) GLB to a temp `glb_dir`, `glb_url=/media/glb/<id>.glb`, axis height, target 0.08 → scale 0.8, display 0.16; (c) CAD session with target_m → action `clarify`, scale unchanged; (d) target 5.0 m → clamped to 2.0 display and the "outside" reply.
- [ ] Step 2: run `python -m app.test_pipeline` → fail.
- [ ] Step 3: implement.
- [ ] Step 4: pass.
- [ ] Step 5: commit `Apply absolute target size to sculpts in set_scale`.

### Task 5: Three.js label, tape measure, cancelTalk, main.js wiring

**Files:** Create `interaction/textSprite.js`, `interaction/dimensionsLabel.js`, `interaction/tapeMeasure.js`; modify `main.js`, `voice/PercyAssistant.js`, `index.html`.

**Produces:**
```js
makeTextSprite({ widthM=0.16, px=512 }) -> { sprite, setText(lines: string[]) }
createDimensionsLabel() -> { object3d, setSize({x,y,z}), follow(model, camera?), setVisible(bool) }
createTapeMeasure(scene, frame = null) -> { setStart(p), setEnd(p), clear(), update(), state(), distance() }
  // points stored in frame-local coords (so they ride along with the model and the distance ignores display zoom)
PercyAssistant.cancelTalk()   // abort recording without sending; safe if start() is still pending
```

main.js wiring:
- keep raw GLB size (`modelBaseSize`) at load; after every `applyDisplaySize` call `refreshDimensions()` → `label.setSize(realSize(modelBaseSize, currentModel.scale.x))`.
- each frame: `label.follow(currentModel, camera)`, `tape.update()`.
- `updateGrab`: keep decomposed scale (`modelRoot.scale.copy(s)`) instead of forcing 1.
- `pollHand` collects per-hand `{pinching, pos}`; after both polls, `updateTwoHand()`: when `isTwoHandActive`, end any one-hand grab, `percy.cancelTalk()` once, apply `twoHandTransform` per frame to `modelRoot` (position via `applyTwoHandToPosition`, quaternion premultiplied by yaw about world Y, scale via `clampNavScale`). When it ends, a still-pinching right hand near the model may re-grab via the normal path.
- tape mode: `T` key and overlay `#tapeButton` toggle; right-hand pinch start (and right controller selectstart) raycasts (controller target ray, then pinch-point → model centre) and calls `setStart`/`setEnd` per `tapeNextStep`; desktop click on the model does the same. Tape mode off → `tape.clear()`.
- desktop: mouse wheel over the canvas zooms (`clampNavScale`) when not presenting.
- [ ] Step 1: write the modules and wiring.
- [ ] Step 2: `cd web-client && npm install && npx vite build` → succeeds.
- [ ] Step 3: node suites still pass.
- [ ] Step 4: commit `Wire two-hand navigation, dimensions label and tape measure`.

### Task 6: Docs + full verification

- [ ] Update `/Users/dimural/Perception_HTN_2026/ai-docs/07-client-xr.md` (interaction modules, two-hand, label, tape, cancelTalk, updateGrab scale change) and add "Pending for 03-intent-router.md".
- [ ] `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh` green; `npx vite build` green.
