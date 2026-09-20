# Circle to edit — unified design

Point at a part with your fingers, say what you want. One gesture, both lanes.

**Supersedes the scope of** `2026-09-19-semantic-mesh-edits-design.md`, which covered only
the sculpt-semantic corner of this table. That spec stays valid for its own lane and becomes
Phase 4 here.

## Goal

Today the model kind decides what editing even feels like. CAD has a hover-and-drag dimension
panel; sculpts have voice region ops. They use different hit-tests, different state, and a
selection means something different in each. This unifies the front half — **one pinch, one
selection, one mental model** — and dispatches on what the user actually said.

## The insight

The gesture and the hit-test are identical for both lanes. Only the last step differs, and it
branches on `session.last_backend`, which the router already splits on. So this is not two
features; it is one selection mechanism feeding a dispatch table that is already half full.

```
pinch a spot  →  raycast  →  selection {center, normal, parts}  →  dominantPart()
                                          │
                 ┌────────────────────────┴────────────────────────┐
              CAD                                                MESH
       ┌────────┴────────┐                          ┌──────────────┼──────────────┐
  dimensional        structural                  region         boolean        semantic
  PARAMS rewrite     codegen + part hint      client verts    mesh/boolean     WS-H
      ~67 ms            ~15 s                   instant          ~1 s          40-70 s
       NEW              exists                  exists          exists          NEW
```

## Measured, not assumed

| | |
|---|---|
| CadQuery rebuild, 2-part | 56 ms |
| CadQuery rebuild, 11-part character | 295 ms |
| CAD codegen follow-up ("add wings to it") | **14.8 s** — the LLM is ~99% of it |
| CAD PARAMS rewrite + rebuild | **67 ms** |
| Gemini image edit | 7.5 s |
| Real CAD GLB part names in this repo's storage | `chassis, body, roll_cage, seat_l, seat_r, wheel_fl…` (15 nodes) |

The 67 ms vs 14.8 s gap is the whole point: **a dimensional edit does not need an LLM**, and
today it pays for one anyway.

## Dispatch table

| Lane | Utterance | Destination | Cost | Status |
|---|---|---|---|---|
| CAD | "make it longer", "thinner", "5 mm wider" | PARAMS rewrite, no LLM | ~67 ms | **new** |
| CAD | "add wings", "put a hole through it" | codegen, part named in payload | ~15 s | exists |
| Mesh | "bigger", "smooth", "paint it red" | client vertex ops | instant | exists |
| Mesh | "drill a hole", "add a loop", "flatten the base" | `mesh/boolean.py` | ~1 s | exists |
| Mesh | "give it wings", "add a hat" | WS-H image edit → image-to-3D | 40-70 s | planned |

**Fail-safe:** anything the table does not claim falls through to codegen — today's behaviour.
The worst case of a miss is the status quo, never a break.

## Where the CAD dimensional path lives: the client

It mirrors region ops exactly. `regionOps.parseRegionCommand` runs **client-side** inside
`PercyAssistant.sendTextCommand` and never touches the server when it matches. The CAD
dimensional path does the same: the client already holds `cadParams` (`main.js:436`, filled
from `CommandResponse.cad_params`) and the live selection, so it can resolve the target
parameter locally and `POST /api/projects/{id}/params` directly — skipping the router and the
LLM entirely.

State ownership stays as it is: `main.js` owns model state (`cadParams`, `currentModel`),
`PercyAssistant` owns the voice turn, and the pure logic lives in
`web-client/src/interaction/partEdit.js` so Node can test it without Three.js.

## Safety: the dispatch must not swallow fast paths

This is the main risk, and it is not hypothetical — while planning WS-H, `make` in the
semantic verb list would have captured "make it red" and turned an instant recolour into a
40-70 s regeneration. It was caught by running the candidate regexes against the utterances
that are fast today.

Rules that follow from that:

1. **Only claim with a resolved target.** The CAD path requires a selection, a part name, and
   a parameter key that actually exists in `cadParams`. No key, no claim.
2. **Never claim a recolour.** Colour changes have their own fast paths in both lanes.
3. **Never claim an addition.** "add", "give", "put" are structural; they belong to codegen
   (CAD) or WS-H (mesh).
4. **Every fast utterance is a test case.** The dispatch test asserts that each currently-fast
   phrase still reaches its own path, not a new one.

## Spoken replies set latency expectations

Same gesture, 67 ms or 70 s depending on lane and utterance. Without different copy that reads
as "sometimes it hangs". Replies are chosen per destination:

| Destination | Reply |
|---|---|
| PARAMS rewrite | "Done." — it is already finished when spoken |
| Client region op | "Done." |
| Mesh boolean | "Drilling that hole." |
| CAD codegen | "Rebuilding that." |
| WS-H semantic | "That'll take a minute." |

## Phases

| # | Phase | Lands on |
|---|---|---|
| 1 | `partEdit.js` — pure parse + resolve for CAD dimensions | the workflow in use today |
| 2 | Wire it into `PercyAssistant` + `main.js`, reply copy | |
| 3 | Selection lifecycle: stop region commands falling through silently | mesh |
| 4 | WS-H semantic edits (its own spec and plan already written) | mesh |

Each phase ships and demos on its own. Phase 4 is last because it is the largest, the slowest,
and the only one that cannot be demonstrated without first building a sculpt.

## Testing

House style: `TOTAL: n/m passed`, no pytest, registered in `run_tests.sh`.

- `web-client/src/interaction/test_interaction.js` — `partEdit.js` parse/resolve/plan, and a
  **dispatch guard**: every fast utterance still routes where it does today.
- `backend/ai/test_intent.py` — unchanged fast paths stay unchanged.
- Backend needs no new suite for phases 1–3: the `/params` route and `set_params` already
  exist and are covered.

**Known baseline:** `main` has one pre-existing failing assertion (`ai.test_intent`, the
`pull_app` vs `find_photos` routing decision). That is a product decision, not this work.
This work must add **zero** new failures against that baseline.

## Out of scope

- Lasso-sized selections — one tap, fixed radius
- Structural CAD edits becoming deterministic — they stay on codegen
- Angle snapping, undo gesture, version-history UI
