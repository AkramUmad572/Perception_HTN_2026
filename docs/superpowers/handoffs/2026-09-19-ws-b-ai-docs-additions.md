# WS-B: proposed ai-docs additions

The worktree harness blocks edits to the main checkout's git-excluded `ai-docs/`.
Coordinator: replace `ai-docs/07-client-xr.md` with everything below the line, then
move its final section ("Pending for 03-intent-router.md") into `03-intent-router.md`.
The pipeline bullets in that section also belong in `05-mesh-lane.md` (sizing) and
`08-state-and-contracts.md` (size contract).

---

# 07 — The client (WebXR)

`web-client/` — Vite + Three.js `immersive-ar`, no framework. 2 246 lines of JS total.

```
src/main.js                1102  scene, hands, grabbing, mesh swap, materials, interaction wiring
src/PhotoPicker.js          237  floating AR photo carousel
src/voice/PercyAssistant.js 297  orchestration: hold → POST → swap → speak (+ cancelTalk)
src/voice/PTTRecorder.js    122  MediaRecorder push-to-talk
src/voice/VoiceState.js     122  state machine + subscribers
src/interaction/measure.js   73  pure: distanceM, formatLength, snap, FINE_MODE_FACTOR, realSize, realScaleFor, labelPosition, tapeNextStep
src/interaction/twoHand.js   77  pure: twoHandTransform, applyTwoHandToPosition, isTwoHandActive, clampNavScale
src/interaction/textSprite.js      canvas-texture label sprite (depthTest off, draws over the model)
src/interaction/dimensionsLabel.js createDimensionsLabel() → {object3d, setSize, follow, setVisible}
src/interaction/tapeMeasure.js     createTapeMeasure(scene, frame, {toReal}) → {setStart, setEnd, place, clear, update, state, distance}
src/interaction/test_interaction.js  Node test for the two pure modules (run_tests.sh)
```

New interaction code goes in `src/interaction/`, with the math in pure modules (no
Three.js import) so `node` can test it. `main.js` only wires inputs to them.

**The client holds no model logic.** It renders whatever the response describes.

## Voice state machine

`VoiceState.js` — `idle | listening | thinking | speaking | error | muted`.

Exposed as `window.voiceState` for in-world HUD code. `subscribe(cb)` fires on every
transition with `{state, isMuted, errorMessage}`. `main.js:updateVoiceIndicator` maps
states to the coloured ring/dot that floats in the scene.

`muted` is a separate flag, not a state — `get state()` returns `MUTED` when muted, but
the underlying `_state` is preserved.

## Push-to-talk

Hold to talk. **There is no wake-word trigger and no VAD in the live path** — an earlier
VAD/KWS design was replaced by hold-to-talk, and `src/test_vad_timing.js` is a leftover
from it.

Inputs that begin a hold (`main.js`): left controller trigger, left hand pinch, the
overlay **Hold** button, `Space` on desktop. `M` toggles mute.

`PTTRecorder`: `MAX_HOLD_MS = 18000` — an 18-second cap auto-ends the hold via
`onMaxHold`, so a stuck trigger cannot record forever. Blobs under 200 bytes are
rejected server-side with "Hold to talk a bit longer".

## Hands and grabbing

```js
PINCH_THRESHOLD_START = 0.032   // 32 mm — pinch begins
PINCH_THRESHOLD_END   = 0.045   // 45 mm — pinch ends
PINCH_MIN_DURATION_MS = 50
GRAB_RANGE            = 0.20    // 20 cm from model centre
```

Start and end thresholds differ on purpose — **hysteresis**. A single threshold makes
the grab flicker as the hand trembles at the boundary.

Left hand talks, right hand grabs. `pollHand` reads thumb/index tip positions each
frame; grabbing requires the pinch point to be within `GRAB_RANGE` of the model centre.

Quirk (pre-existing, left alone): `wasPinching[key] = isPinching` is written every
frame with `isPinching = gap < START`, so a pinch that drifts from 32 mm to 40 mm to
50 mm never fires the "end" branch (`wasOpen && wasPinching`). The new two-hand and
tape code do **not** reuse it; they keep their own true hysteresis latch `held[key]`
(on below START, off above END or when the hand is lost), with `heldPos[key]`.

### Display zoom lives on `modelRoot.scale`

`navScale` (0.1–10, `clampNavScale`) is a **display-only** zoom from the two-hand
spread or the desktop mouse wheel. Nothing is sent to the server. `updateGrab` now
does `modelRoot.scale.setScalar(navScale)` after decomposing, where it used to force
scale 1; otherwise a one-hand grab would snap a zoomed model back to 1×.

The loaded object's own scale (`applyDisplaySize`) remains the "real size" scale.
Anything that reports real size must divide the zoom out; see the label and the
tape measure below.

### Two-hand mode

`updateTwoHand()` runs after both `pollHand` calls, every frame.

- **Engages** when `isTwoHandActive`: both hands `held`, both within `GRAB_RANGE` of
  the model's box, and tape mode off. On engage: any one-hand grab ends, and
  `percy.cancelTalk()` drops the left pinch's push-to-talk recording. It was a gesture,
  not speech, and must not be sent as a command.
- **Continues** while both hands are held, even after they spread out of range.
- Per frame: `twoHandTransform(prevL, prevR, curL, curR)` → `{scale, yaw, translate}`
  (incremental). The position goes through `applyTwoHandToPosition` about the previous
  midpoint, yaw is premultiplied about world Y, and the scale multiplies `navScale`.
- While it is on, `updateGrab` is skipped and the right hand's `beginGrab` path is
  blocked (`!twoHandOn`). When one hand lets go, a still-pinching right hand re-grabs
  through the normal path.
- Yaw convention: `heading(v) = atan2(v.x, v.z)`, the same as Three.js rotation about
  +Y.

`PercyAssistant.cancelTalk()` aborts the recorder, goes idle and sends nothing. If it
lands while `recorder.start()` is still awaiting the mic, a `_cancelPending` flag makes
`beginTalk` abort as soon as the mic opens.

### Dimensions label

`createDimensionsLabel()` is a canvas sprite, "W × H × D" in `formatLength` units
(mm < 1 cm, cm < 1 m, else m). `follow(currentModel, camera)` runs each frame and
places it beside the model's world box, to the viewer's right.

Real size = `realSize(modelBaseSize, realScaleFor(lastBackend, currentModel.scale.x))`:

- `modelBaseSize` is the raw GLB bounds, captured at load.
- **CAD GLBs are already real metres**, so their scale is 1. The display clamp
  (0.04–1 m) must not leak into the label.
- **Sculpts** use their display scale, which the server sets from `base_size_m * scale`.

`refreshDimensions()` runs after a swap and after every `set_scale`. The label ignores
`navScale` by construction.

### Tape measure

Toggle it with `T` (desktop), the overlay `#tapeButton`, or
`window.PerceptionCAD.setTapeMode(bool)`. While it is on, the right hand measures and
does not grab.

- **Hands:** the rising edge of `held[1]` raycasts along the right controller's target
  ray. If that misses (the pinch is touching the model), it raycasts from the pinch
  point toward the model centre.
- **Controllers:** `selectstart`. **Desktop:** click on the model.

`tape.place(p)` alternates start and end, and a third point starts over. Points are
stored in `modelRoot`-local coordinates, so they follow the model and the distance
ignores `navScale`. `toReal` converts display metres to real metres (the CAD clamp
case). The tape clears on every model swap and when the tool is turned off.

`window.PerceptionCAD` also exposes `isTwoHand()`, `getTapeDistanceM()` and
`getNavScale()` for debugging.

## The mesh swap

`main.js:setModelFromResponse(data)` — the single place geometry changes.

Swap condition:

```js
const needsNewModel = data.rebuilt && data.model_id && data.glb_url;
const glbUrlChanged = data.glb_url && data.glb_url !== lastLoadedGlbUrl;  // defensive
```

Sequence when it fires:

1. Cache-bust: `?t=${Date.now()}` — the same path can legitimately repeat.
2. Load the GLB, measure bounds, `applyDisplaySize(obj, data.display_size_m)`.
3. `prepareVisibleMaterials(obj, color, textured)` — see below.
4. **Save grab state** (offset, position, quaternion) if currently held.
5. Dispose the old object's geometries and materials explicitly, then remove it.
6. Attach the new one, and **rebind the grab** if one was active.
7. If not grabbed and presenting, `placeModelInFrontOfUser(0.7)` — 0.7 m ahead.

Step 4/6 is why the model can change *in your hand* without leaving it. Preserve it.

Non-swap branches:

- `action === "set_scale"` → `applyDisplaySize` on the loaded object, no fetch, then
  `refreshDimensions()`. `applyDisplaySize` detaches the object while it measures.
  Before this fix, it subtracted a world-space centre from a local position, so a
  voice resize inside a placed `modelRoot` pushed the model off-centre.
- `data.color` with no new model and `!lastTextured` → repaint in place (CAD only).
- `action === "find_photos"` → early return; the picker handles it.

## Materials

`prepareVisibleMaterials(root, hex, textured)`:

- **textured** (mesh lane): keep the provider's baked materials. Do not tint — you would
  destroy the texture.
- **untextured** (CAD lane): apply `hologramMaterial(hex)`, a translucent emissive look
  that reads against passthrough.

`data.textured || data.backend === "mesh"` decides. `ensureOutwardNormals` fixes inverted
normals that come out of some CadQuery exports.

## PhotoPicker

`PhotoPicker.js` — a floating carousel placed 0.75 m ahead, cards 28 × 28 cm.

Pinch-drag sideways to swipe; pinch-release without moving more than `SWIPE_M = 0.03`
(3 cm) to pick. That threshold is what separates "browse" from "choose" on the same
gesture.

Textures load with `crossOrigin = "anonymous"` and fall back to a flat blue card if the
image fails.

## Timeouts are ordered

| Layer | Value | File |
|---|---|---|
| Client abort | 180 000 ms | `PercyAssistant.REQUEST_TIMEOUT_MS` |
| Vite proxy | 300 000 ms | `vite.config.js` `timeout` / `proxyTimeout` |

**The proxy is deliberately the more patient of the two.** A slow build then surfaces as
a spoken error message from the client, rather than as a connection the proxy severed
first — which the user experiences as silence. If you change one, keep the ordering.

## Dev notes

- `npm run dev` / `npm run dev:https` — both `vite --host`; the IWSDK plugin supplies
  HTTPS and the Quest 3 emulator on localhost.
- The emulator activates on `localhost` only, and is bypassed for `OculusBrowser` user
  agents — so a real headset hitting the LAN IP gets the real WebXR path.
- `src/test_model_update.js` and `src/test_vad_timing.js` are standalone scripts, not a
  wired-up test runner.
- `src/interaction/test_interaction.js` **is** wired into `run_tests.sh` (Node ESM,
  prints `TOTAL: n/m passed`). Keep new interaction math in pure modules so it can be
  tested there.

## Pending for 03-intent-router.md (coordinator: move this)

New rung `ai/intent.py:_check_absolute_size`, inside the existing
`session_backend == "mesh" and not is_new` block, **before** `_check_scale_only`, and so
before the `clarify_mesh` return. That order matters: "make it 30 mm deep" contains `mm`,
a `_CAD_FORCE_RE` cue, and would otherwise be refused as `clarify_mesh`.

- Fires on a number (digits or one–twenty, thirty…ninety, hundred) plus a unit (`mm`,
  `millimetre(s)`, `cm`, `centimetre(s)`, `m`, `metre(s)`, `inch(es)`, US spellings
  too), with an optional `tall|high|wide|across|long|deep|thick`.
- Result: `Intent(action="set_scale", params={"target_m": float, "axis": "height"|"width"|"depth"|None}, reply="Made it 8 centimetres tall.")`.
- Requires a whole-model resize cue (`make it/this/that`, `resize`, `scale it`,
  `set it`, `it should be`) **or** a bare size phrase ("12 cm wide"). So
  "add a 5 mm hole" and "put a 3 cm loop on top" are not resizes.
- Returns None on relative words (`bigger … taller … more less by`: "2 cm taller"
  is not absolute), on `_PART_RE` matches ("make the ears 2 cm long"), and on new-object
  requests.
- **Mesh sessions only.** CAD sessions keep going through codegen (real mm). The
  pipeline also refuses `target_m` for a CAD session with a spoken clarify.

Pipeline (`app/pipeline.py` `set_scale` branch, for the 05/08 notes):

- `scale = target_m / (base_size_m * axis_fraction)`.
- `axis_fraction = extent[axis] / max(extent)` is read from the GLB, which
  `_glb_path_from_url` resolves from `/media/glb/...` or `/media/projects/...` (the
  latter once `settings.projects_dir` exists). It is 1.0 when there's no axis or the
  file can't be read.
- The clamp is unchanged. If it bites, the reply becomes "That's outside what I can
  show, so I went as far as I can."

`ai.test_intent` is now registered in `run_tests.sh`. Its `settings_off` stub also
needed `hf_space_enabled=False`: the keyless HF lane made `mesh_ready` true, so that
test was failing before this work.
