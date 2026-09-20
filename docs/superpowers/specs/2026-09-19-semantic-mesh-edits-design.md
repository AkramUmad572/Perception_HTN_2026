# Semantic mesh edits (WS-H) — design

Circle a spot on a sculpt with your fingers, say what you want, get it. "Give it wings."

This is Phase 5 of `2026-09-19-in-headset-editing-design.md` and WS-H of the master plan.
Neither was ever started; this is its first design.

## Goal

A sculpt has no parts and no parameters, so today the only edits possible on one are region
ops (bigger / pull / flatten / smooth / paint), the three booleans (hole / loop / flat base)
and resize. Everything else hits the `clarify_mesh` refusal. This adds the missing category:
**semantic changes** — add wings, add a hat, give it horns — driven by pointing at a spot and
saying what you want.

## Guiding principle

There is no model that edits a 3D mesh semantically. There are excellent models that edit
**images**. So go around through 2D: photograph the sculpt, edit the photograph, and push
the edited photograph through the image-to-3D lane this project already ships.

## Verified before designing

Every load-bearing assumption was checked against the live APIs and this codebase, not
assumed:

| Claim | Result |
|---|---|
| `GEMINI_API_KEY` has image-output models | 6 available, incl. `gemini-2.5-flash-image` |
| Image-in → image-out edit works | 7.3–7.9 s, 1024×1024 |
| A drawn circle steers placement | Horn landed exactly in the circle |
| The drawn circle leaks into the output | **No** — 0 red pixels in the result |
| The source object survives the edit | Yes, unchanged in both trials |
| Image-to-3D can take a **local file** | Yes — HF TRELLIS is tried first with `image_path` |
| Polled jobs with per-stage status exist | `jobs.start` / `jobs.get` / `jobs.set_app_status` |
| Finger pinch is real hand tracking | `renderer.xr.getHand()`, `thumb-tip`/`index-finger-tip` |
| CAD rebuild latency (for contrast) | 56 ms simple, 295 ms an 11-part character |

## Architecture

```
1. pinch a spot on the sculpt      → selection.center (3D, model-local, radius 0)   [exists]
2. "give it wings"
3. router: mesh session + selection + not a known local op → action="semantic_edit"
4. CLIENT   render the current view to a PNG
            project selection.center → (cx, cy) via .project(camera)
            draw a fixed-radius circle at (cx, cy)
            POST multipart { png, text, session_id }
5. SERVER   start a job:
              a. Gemini image edit                              ~7.5 s
              b. write the edited PNG to ref_dir
              c. generate_mesh_glb_from_image(local + url)      ~30–60 s
              d. append_version(op="semantic_edit", parent=current)
6. client polls the job, HUD shows the stage, then the normal model swap
```

### Why the headset takes the picture

The headset is already drawing the sculpt every frame, so capturing one is a screenshot. It
also already knows the camera, which makes the 3D→2D conversion of the circled point a
single built-in `.project(camera)` call. A server-side render would need a headless GL stack
(fragile on macOS) *and* would still have to be sent the camera pose. The client render is
also WYSIWYG: Gemini edits exactly what the user is looking at.

### Why a drawn circle instead of a mask

Tested, and it works: a red circle drawn onto the render places the edit precisely and does
not appear in the output. This removes any need for a mask API, a second mask image, or an
alpha channel. It is the smallest thing that works.

### Selection: one tap, fixed radius

`selectionFromStroke` already returns a point selection (radius 0) for a quick tap, and
`isTapRelease` already separates tap from hold. The MVP uses that path only. The lasso path
is deliberately **not** on the critical path, because it is the part currently known to be
buggy.

## Components

| Unit | Responsibility |
|---|---|
| `backend/mesh/edit.py` (new) | One job: call Gemini with an image + instruction, return edited image bytes. No rendering, no 3D, no HTTP routing. |
| `POST /api/projects/{id}/semantic_edit` (new) | Accept multipart (png, text, session_id), start a job, return `job_id`. Never raises to the client. |
| `pipeline.semantic_edit` branch | Orchestrate: edit → write → image-to-3D → `append_version`. |
| `ai/intent.py` rung | Recognise a semantic edit on a mesh session with a selection. |
| client render helper | Render current view to PNG; project a 3D point to 2D; draw the circle. |

`mesh/edit.py` stays free of rendering and of the mesh lane so it can be tested with a fake
HTTP client and no GPU, in the style of `mesh/test_meshy.py` and `mesh/test_three_ws.py`.

## Data flow and contracts

The client sends model-local `selection.center` today. For this feature the client does the
projection itself and sends **only the finished PNG plus the instruction text** — the server
never needs the camera, the selection, or the model's transform. That keeps the server
contract tiny and keeps every coordinate-space question on the side that already has the
answers.

New version op: `semantic_edit`. The string is already reserved in `app/models.py:86` and in
the op set at `app/pipeline.py:969`; nothing produced it until now. `parent` is set to the
version being edited, so undo returns to the pre-edit sculpt.

## Latency budget

| Stage | Cost | Notes |
|---|---|---|
| Client render + project + draw | < 50 ms | one frame plus 2D drawing |
| Upload PNG | ~0.2–1 s | 1024² PNG over local Wi-Fi |
| Gemini image edit | **7.5 s** | measured, `gemini-2.5-flash-image` |
| Image-to-3D | **30–60 s** | the existing lane, already at `quality="draft"` |
| Version write + swap | < 1 s | |
| **Total** | **~40–70 s** | |

The 30–60 s is **not new cost**. It is exactly the wait already paid when building a model
from a Drive photo, which this project already ships and demos. WS-H adds the 7.5 s edit on
top. `quality="draft"` is already the fastest tier, so there is no free speed-up available
inside the existing lane.

**Mitigation that does not change the interaction:** push the edited image to the HUD as
soon as it returns at ~7.5 s, while the 3D build continues. This is not a confirmation gate
and adds no gesture — it replaces dead air with visible progress and surfaces a misread
instruction early.

## Error handling

Routes never raise to the client — catch, log, return `ok=False, action="clarify"`, per the
project's existing rule.

| Failure | Behaviour |
|---|---|
| Gemini returns no image part | Spoken "I couldn't picture that change." Model untouched. |
| Gemini HTTP error / timeout | Spoken "That edit didn't come back." Model untouched. |
| HF Space busy | Existing fallback to `three.ws` (why the PNG is written to `ref_dir`). |
| Image-to-3D fails | Spoken failure, no version appended, model untouched. |
| No selection | "Point at where you want it first." — the existing `_NO_SELECTION_REPLY`. |
| Not a mesh session | Falls through to current CAD behaviour, unchanged. |

Every failure leaves the current version in place. A semantic edit only ever *appends*.

## Known limitation: identity drift

This regenerates the whole mesh from a picture. The result is **a cousin of the original
plus the change**, not the original with something added — proportions, colour and detail
will shift. The in-headset-editing spec already names and accepts this. `parent` on the
version is the mitigation: undo returns cleanly to the pre-edit sculpt.

This should be reflected in what Percy says, so it reads as expected behaviour rather than a
bug.

## Testing

House style: plain scripts with a `__main__` runner printing `TOTAL: n/m passed`, no pytest,
registered in `run_tests.sh`.

- `backend/mesh/test_edit.py` — the Gemini call against a fake HTTP client: request shape,
  image part extraction, missing-image-part error, HTTP error, prompt construction. No
  network.
- `backend/app/test_pipeline.py` — the `semantic_edit` branch: appends with `op` and
  `parent` set on success; appends nothing on any failure.
- `backend/ai/test_intent.py` — the router rung claims a semantic edit on a mesh session
  with a selection, and does **not** claim hole / loop / flat base / region ops / resize,
  which must keep reaching their existing deterministic paths.
- `web-client/src/interaction/test_interaction.js` — the pure part of the 3D→2D projection
  and circle placement, testable in Node without Three.js.

The Gemini call is never exercised against the live API in the suite.

## Out of scope

- Lasso-sized selections (fixed radius only)
- A confirmation step before the 3D build (one-shot, by choice)
- CAD (untouched; falls through to existing behaviour)
- Any mask API or alpha channel
- WS-I / Save to Drive — separate, see the remaining-work doc

## Invariants that change

`ai-docs/09-invariants.md` needs updating when this lands: `clarify_mesh` narrows again.
Hole / loop / flat base already became supported in Phase 4; semantic edits join them, and
`clarify_mesh` retains only what remains genuinely unsupported on a sculpt.
