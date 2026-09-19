# In-headset editing — design

**Status:** Draft, awaiting user review · **Branch:** `feature/in-headset-editing` · **Date:** 2026-09-19

## Goal

After a model appears, the user can explore it with their hands, see exact measurements,
and change it with both tiny and big edits, on **both CAD and mesh models**. They can
point at or circle a spot and ask for a change exactly there. Every change is a saved
version, so the next change builds on it and undo works.

## Guiding principle

**Small changes happen on the headset with no AI and apply instantly. Big changes go to
the AI, where waiting a few seconds is acceptable.**

The current app sends every follow-up through Gemini (3–10 s) plus a rebuild (1–5 s CAD,
30 s+ mesh). That is the bottleneck this design removes for small edits.

## Background: the two model kinds

| | CAD | Mesh (sculpt) |
|---|---|---|
| Made by | Gemini writes CadQuery Python; sandbox runs it | Text/image → generative 3D provider |
| Has | Named parts, real mm dimensions, editable script | One textured triangle surface, no parts, no real size |
| Today's follow-ups | LLM edits `last_script` | Resize (display only), recolor (re-sculpt); geometry edits refused (`clarify_mesh`) |

They are **not** linked. A mesh has no CAD file behind it. This design gives both kinds the
same user-facing tools while routing each action to a lane-appropriate engine.

## User-facing capabilities

| Capability | CAD | Mesh |
|---|---|---|
| Two-hand zoom / rotate, explode parts (CAD) | client | client |
| Overall dimensions label | from GLB bounds | from GLB bounds × real-size scale |
| Tape measure (pinch two surface points) | client raycast | client raycast |
| Set real size ("make it 8 cm tall") | codegen / param | uniform scale stored as `real_scale` |
| Tiny numeric edit (drag or "2 mm longer") | rewrite one `PARAMS` value, rerun sandbox, no LLM | region scale/move on selected vertices, client-side |
| Point / circle a spot | selects part(s) + region | selects vertex region with soft falloff |
| "Paint this" | part colour | region vertex/texture paint, client-side |
| "Drill a hole / add a loop / flatten bottom here" | LLM script edit with location hint | server boolean of a CAD shape into the mesh |
| Big semantic change ("give it wings") | LLM script edit with selection context | render view → image edit → image-to-3D |
| Undo / redo / "go back two" | version history | version history |
| Save to Drive | final version only | final version only |

## Architecture

### 1. Projects and versions (foundation)

Replace the single-pointer model storage with a project per object:

```
backend/storage/projects/<project_id>/
  v1.glb, v2.glb, …
  info.json        # current version, per-version: kind, op, summary, script?, params?, parent
```

- A **new object** creates a project. **Every edit** (AI or hand) appends a version.
- `SessionState` gains `project_id`, `version`, and a redo pointer. `last_script` etc. are
  derived from the current version's entry, so undo restores the full follow-up context.
- **Cap:** 20 versions per project; the oldest is pruned (v1 is kept as the original).
- **Hand drags save once**, on release, never per frame.
- **Cleanup task** (on startup and hourly): audio replies older than 1 h, staged ref
  photos older than 24 h, projects untouched for 7 days unless marked `saved_to_drive`.
- Existing `/media/glb` URLs keep working for old sessions; new builds serve from
  `/media/projects/<project_id>/vN.glb`.

### 2. Client-side edits and the swap contract

Today the client swaps geometry only on `rebuilt && model_id && glb_url`. Client-side
edits already show the result locally, so they must **not** trigger a reload:

- Client applies the edit live, then on release exports the GLB (`GLTFExporter`) and
  `POST /api/projects/{id}/versions` (multipart GLB + op metadata).
- The server stores it and returns the new version with `rebuilt=False` and
  `action="version_saved"`. The client updates its version pointer only.
- Undo/redo return `rebuilt=True` with the stored version's URL, so they go through the
  normal swap path.

### 3. CAD parameters (makes tiny CAD edits AI-free)

- `CODEGEN_SYSTEM_PROMPT` requires a top-level `PARAMS = {...}` dict of named
  dimensions in mm (convention `<part>_<dimension>_mm`, e.g. `ear_length_mm`), with the
  body of the script reading from it. Follow-up edits must preserve and update `PARAMS`.
- Server extracts `PARAMS` with `ast` (literal eval of that one assignment, not execution)
  and returns it in the response as `params`.
- `POST /api/projects/{id}/params` `{name: value}` rewrites that literal in the script
  and reruns the sandbox. No Gemini call. Target: under 3 s end to end.
- Part names must survive into GLB node names so the client can raycast → part → its
  params. Verify `_export_assembly` preserves names; fix if not.
- Scripts without `PARAMS` (older sessions, or a model that ignored the rule) still
  work: the panel just shows overall dimensions only.

### 4. Selection (point and circle)

- Right-hand ray (controller, or index-finger ray for hands). Short pinch = point;
  pinch-and-drag = lasso stroke projected onto the surface.
- The selection is `{parts: [...], center_m: [x,y,z], radius_m, normal, vertex_ids?}` in
  model-local coordinates. Snaps to a whole part when the stroke mostly covers one
  (CAD always, mesh when it has named nodes).
- The selection highlights on the model and stays active until cleared or used.
- Any voice command sent while a selection is active carries it (`selection` field on
  `/api/voice` and `/api/command`). The router puts it into the codegen payload as
  "user selected ear_l near (x,y,z) mm".

### 5. Mesh editing engines

- **Deterministic region ops (client):** scale, pull/push along the normal, flatten,
  smooth, paint, all with soft falloff over the selection. Voice maps through
  a small local table ("bigger", "pull out", "smooth", "paint red") in the client-side
  router. No AI.
- **Boolean features (server):** hole / loop / flat base via `trimesh` with the
  `manifold3d` engine, after a watertight repair pass. Replaces the current
  `clarify_mesh` refusal **for these operations only**. Failure answers with a spoken
  reason, and the model is left unchanged.
- **Semantic changes (server):** render the current model from the selection's view
  direction, edit that image with an image-editing model using the selection as a mask,
  then send it through the existing image-to-3D path (`mesh/factory.py` image lane).
  Result is "close to the original plus the change", not identical. That is a known,
  accepted limitation.

### 6. Hand-edit smoothness

- Live preview while dragging; commit on release.
- Fine mode (off-hand pinch held): hand movement ×0.1.
- Snapping: 1 mm lengths, 5° angles; selections snap to parts.
- Live value label next to the hand ("ear_length: 18 mm").
- Soft falloff for region ops.
- Undo gesture plus voice "undo".

### 7. Save to Drive

Uploads the current version only, to a per-project folder. Writing to Drive needs
OAuth or a service account (the existing API key is read-only). Last phase; optional.

## Phases (each one ships and demos on its own)

| # | Phase | Main files |
|---|---|---|
| 0 | Projects + versions + undo/redo + cleanup | `app/models.py`, `app/session.py`, new `app/projects.py`, `app/pipeline.py`, `app/main.py` |
| 1 | Two-hand navigation, dimensions label, tape measure, real size | `web-client/src/main.js`, new `web-client/src/interaction/*` |
| 2 | CAD `PARAMS`, param endpoint, dimension panel, part names in GLB | `ai/intent.py`, `cad/sandbox.py`, new `cad/params.py`, client panel |
| 3 | Point/circle selection, client region ops, version upload | client `interaction/selection.js`, `interaction/regionOps.js`, `app/main.py` |
| 4 | Mesh booleans (hole/loop/flat base), `clarify_mesh` narrowed | new `mesh/boolean.py`, `ai/intent.py`, `app/pipeline.py` |
| 5 | Semantic mesh edits via image edit → image-to-3D | new `mesh/edit.py`, `mesh/factory.py` |
| 6 | Save to Drive | `photos/drive.py` or new `storage/drive_upload.py` |

`main.js` is 922 lines; new interaction code goes in separate modules rather than growing it.

## Invariants that change (must be updated in `ai-docs/09-invariants.md` when done)

- `clarify_mesh` narrows: hole / loop / flat base on a mesh become supported (Phase 4).
- The swap triple remains the only path that reloads geometry; client-side edits
  deliberately return `rebuilt=False` (Phase 0/3).
- Storage stops being append-forever: capped history + TTL cleanup (Phase 0).

## Testing

- Backend: extend the existing `python -m app.test_pipeline` style. New unit tests for
  `projects.py` (append, prune, undo/redo, cleanup TTLs), `cad/params.py` (extract,
  rewrite, missing `PARAMS`), `mesh/boolean.py` (hole on a watertight and a repaired mesh).
- Client: pure-function tests runnable by `node` like `test_model_update.js`
  (selection math, falloff, snapping, fine-mode scaling).
- Manual: IWSDK desktop emulator for each phase; Quest 3 before merging a phase.
- `run_tests.sh` gains each new suite.

## Risks

- **Hand precision (~1 cm):** mitigated by part snapping, fine mode, voice-supplied numbers.
- **LLM ignoring `PARAMS`:** handled gracefully (panel degrades); repair prompt reminds.
- **Mesh booleans on messy sculpts:** repair can fail; answer with a spoken reason and
  keep the model unchanged.
- **Semantic mesh edits drift from the original:** accepted; undo is one word away.
- **Client GLB export size:** sculpts can be large; upload is gzip'd and happens once, on release.

## Open questions

- Deadline: if this is for a hackathon this weekend, cut to Phase 0 + Phase 1 +
  a Phase 3 slice (point → "drill a hole here" / "make this bigger" on both lanes).
- Image-editing model for Phase 5 (Gemini image editing is the default candidate; it
  reuses the existing `GEMINI_API_KEY`).
