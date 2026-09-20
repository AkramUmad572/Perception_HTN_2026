# Remaining work — in-headset editing + Composio

**Status doc, not an implementation plan.** It records what is on `main` today, what is
left, and the decisions that have to be made before a TDD plan can be written for each
piece. Several items below are blocked on a decision or on credentials, not on code — a
detailed plan written now would be guesswork. Per-workstream plans go in
`docs/superpowers/plans/` as usual once the open questions in each section are answered.

**Baseline:** `main` @ `3bfa524` (Composio app pulls), which includes the merge of PR #1
(in-headset editing, `b695367`).

**Source specs:**
- `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md` (Phases 0–6)
- `COMPOSIO_PLAN.md` (build order + implementation todos)

---

## 0. Fix first: `main` is red

`./run_tests.sh` fails on `main` right now — 1 suite, 1 assertion:

```
Intent tests: FAILED
  [FAIL] parse_intent pull_app get the pikachu keychain from my photos i want to 3d print it
TOTAL: 179/180 passed
```

**Cause.** `3bfa524` inserted `route_composio(...)` into `parse_intent` at
`backend/ai/intent.py:1217` — **above** the `is_photo_search` rung. The utterance
"get the pikachu keychain from my photos I want to 3d print it" now matches the Composio
app-pull pattern and returns `action="pull_app"` before the photo-search rung is reached.
`backend/ai/test_intent.py:887` still asserts `action == "find_photos"`.

These are two genuinely different downstream paths:

| action | Pipeline | Client UX |
|---|---|---|
| `find_photos` | returns `candidates` synchronously (`pipeline.py:520`) | `PhotoPicker` |
| `pull_app` | `start_pull_job` → async job + `job_id` (`pipeline.py:487`) | `SearchHUD` → `ItemPicker` |

**This looks intentional, not accidental.** `COMPOSIO_PLAN.md`'s `intent-pipeline` todo
says "image-find **before the old Drive regex**", and `image-sources` describes searching
Photos / Drive / Figma / Gmail rather than Drive alone. The Composio path is meant to
supersede the Drive-only photo search for image finds.

**Decision needed — pick one:**

- **(a) The test is stale.** Update `test_photo_search_fast_path` to assert `pull_app`.
  Before doing this, confirm end-to-end that the job → HUD → `ItemPicker` path actually
  reaches a pinchable photo card, because that is what the old assertion was protecting.
  This is the option `COMPOSIO_PLAN.md` implies.
- **(b) The router is too greedy.** Make `route_composio` defer to `is_photo_search` when
  the utterance names "my photos" and nothing else, keeping the old picker for the
  single-source case.

Do not merge anything else until this is resolved — a red `main` makes every later change
impossible to evaluate.

### Also unwired

`backend/composio_app/test_adapters.py` and `test_router.py` are tracked but **not in
`run_tests.sh`**, so they never run in the suite. They pass when invoked directly. Add them
to the `for M in ...` loop at the bottom of `run_tests.sh` alongside the others.

---

## 1. Phase status (in-headset editing spec)

Numbering follows the spec's phase table (`...-design.md:141`). The master plan's "Waves"
are a different grouping — Wave 3 = Phases 5 + 6, which is the part that was never started.

| # | Phase | Status |
|---|---|---|
| 0 | Projects, versions, undo/redo, cleanup | Done |
| 1 | Two-hand nav, dimensions label, tape measure, real size | Done |
| 2 | CAD `PARAMS`, param endpoint, dimension panel, GLB part names | Done |
| 3 | Point/circle selection, client region ops, version upload | Built, structurally incomplete — §2 |
| 4 | Mesh booleans (hole / loop / flat base) | Done |
| 5 | Semantic mesh edits via image-edit → image-to-3D | **Not started** — §3 |
| 6 | Save to Drive | **Not started, blocked** — §4 |

WS-A/B/C/D each have a detailed plan doc in this directory. **WS-H (Phase 5) and WS-I
(Phase 6) never got one** — they exist only as two stub lines in the master plan. That is
why "part 3" (Wave 3) has no visible definition anywhere.

---

## 2. Phase 3 — per-part selection and editing

The pieces exist and the pure math passes (54 assertions in
`web-client/src/interaction/test_interaction.js`). What is missing is not a typo; it is
that **selection was never designed to constrain a CAD edit**.

### What is actually wired

- `interaction/selection.js` — `selectionFromStroke`, `isTapRelease`, `dominantPart`
- `interaction/regionOps.js` — `scaleRegion` / `pullRegion` / `flattenRegion` /
  `smoothRegion` / `paintRegion` / `parseRegionCommand`
- `main.js` — select mode, hover highlight, tap-select, `applyRegionCommand` →
  `GLTFExporter` → `POST /api/projects/{id}/versions`
- `PercyAssistant.js:216` — `parseRegionCommand` on the active selection

### Gap 2a — on CAD, selection is a prompt hint, never a constraint

`_describe_selection()` has exactly one call site (`backend/ai/intent.py:1304`): it appends
`"user pointed at ear_l near (x, y, z) mm"` to the codegen payload. Nothing prevents the
model rewriting the whole script. **This is why per-part editing feels flaky — it is a
suggestion to an LLM, not an enforced scope.**

| | What selection does | Deterministic? |
|---|---|---|
| CAD | adds a sentence to the codegen prompt | No |
| Sculpt | region ops + booleans on real vertices | Yes |

**Options to make it real (decision needed):**

1. **Route through `PARAMS` instead.** When a part is selected and the utterance is
   dimensional, resolve it to `params_for_part()` and drive
   `POST /api/projects/{id}/params` — no LLM, already deterministic, already built. Cheapest
   path and it reuses Phase 2 end to end. Does not cover shape changes, only dimensions.
2. **Constrain codegen structurally.** Extract the selected part's sub-expression from the
   script, send only that to the model, splice the result back. Much more powerful, much
   more work, and needs a reliable script-slicing story in `cad/params.py`.
3. **Accept it as a hint** and set expectations in the reply copy.

Recommendation: **(1) for dimensional edits now, (2) later if needed.** (1) is a small,
testable change and turns the most common case deterministic.

### Gap 2b — region commands fall through silently

`PercyAssistant.js:216` gates on `this.selection` being non-null *at the moment the
utterance is parsed*. If the selection was cleared by a model swap or a mode change, the
command quietly becomes an ordinary LLM request. No error, no spoken hint — just the wrong
thing happening. Needs either a sticky selection across swaps or an explicit "nothing is
selected" reply.

### Gap 2c — region ops are sculpt-only

They mutate vertex buffers directly, so they cannot apply to a CAD model at all. Worth
stating in the UI/reply copy so it is not read as a bug.

---

## 3. Phase 5 — semantic mesh edits (not started)

`backend/mesh/edit.py` does not exist. The only trace is the literal `"semantic_edit"` in an
op-enum comment (`app/models.py:86`) and in the op set at `app/pipeline.py:969` — placeholder
slots with nothing that produces them.

**What the spec calls for** (`...-design.md:107`, §5): render the current sculpt from the
selection's view direction → edit that image with an image-editing model, using the
selection as a mask → feed it through the existing image-to-3D lane (`mesh/factory.py`) →
save as a new version. The spec explicitly accepts that the result is "close to the
original plus the change", not identical.

**Why it matters:** on a sculpt, the only edits that work today are region ops, the three
booleans, and resize. Anything semantic — "give it pointier ears", "make it look angrier" —
hits the `clarify_mesh` refusal. This is the largest remaining capability hole.

**Open before a plan can be written:**
- Which image-editing model, and is there a key for it? (Gemini image edit is named in the
  master plan's Wave 3 line; nothing in `.env.example` covers it.)
- How is the mask derived from a `Selection` — project the lasso radius into screen space,
  or render a separate mask pass?
- Does the re-generated mesh replace the version outright, or append with `parent` set so
  undo returns to the pre-edit sculpt? (`VersionInfo.parent` already supports the latter.)
- Latency budget: this is a render + image edit + full image-to-3D rebuild. Almost certainly
  needs the `jobs.py` async path rather than a blocking request.

---

## 4. Phase 6 — Save to Drive (not started, blocked)

`projects.mark_saved_to_drive()` exists and is tested, but **nothing in production code
calls it** — only `app/test_projects.py`. There is no upload function, no route, no OAuth.
`photos/drive.py` is read-only: `list_images`, `download_file`, `ensure_local_file`.

**Blocked, not just unbuilt.** The spec notes writing to Drive needs OAuth or a service
account; the current API key is read-only.

**Consequence worth fixing regardless of Phase 6:** `saved_to_drive` is the flag that
exempts a project from the 7-day TTL sweep in `projects.cleanup()`. Since nothing can set
it, **every project is eventually deleted.** Until Drive upload lands, there is no way for a
user to keep a model. Consider an interim "keep this" path that sets the flag without Drive.

**Open before a plan can be written:**
- OAuth flow vs service account — who holds the credential, and where does the token live?
- Note that Composio already brokers Drive access (`composio_app/adapters.py`). Publishing
  through Composio's Drive adapter may be cheaper than a separate OAuth integration. Worth
  checking before writing any new Drive code.

---

## 5. Smaller spec items never built

- **No undo gesture.** Spec §6 asks for "undo gesture plus voice undo". Voice undo works via
  `/api/command`. The string `undo` appears nowhere in `web-client/src/`.
- **`POST /undo`, `POST /redo`, `GET /api/projects/{id}` are dead endpoints.** Built and
  tested, never called. The client only hits `/versions`, `/params`, `/resize`.
- **No version-history UI.** Nothing surfaces what those routes expose.
- **No 5° angle snapping.** Spec §6 asks for "1 mm lengths, 5° angles". The 1 mm snap exists
  (`dragParamValue`); angle snapping was never written.

---

## 6. Composio remaining work

From `COMPOSIO_PLAN.md`'s own todo table, still open:

- `env-composio` — marked "done on the env-key side **except a valid project API key**".
  Nothing works against the live API without it.
- `seed-demo` — Pikachu in Photos and Drive, optional Figma frame, sample CAD request in
  Gmail / Notion / GitHub. Needed for the demo script to run at all.
- Build-order steps 3–5 (pickup from the six apps, publish to four, "build that" from
  `last_brief`) — code exists in `composio_app/`; **none of it is verified against the live
  API**, only against the unit tests, which are themselves not in the runner (§0).

---

## 7. Cross-cutting

- **No client wiring tests.** `main.js` is now 1677 lines with hand tracking, mode
  switching, HUD, picker and region ops in it. Only the pure-math `interaction/*` modules
  are covered.
- **Unguarded animation loop.** `main.js:1411` calls six new per-frame functions with no
  `try/catch`. three.js `WebGLAnimation.onAnimationFrame` calls the callback *before*
  re-queuing the next frame, so a single uncaught throw stops rendering permanently — the
  headset freezes until the session is reloaded. A `try/catch` around that block is ~5 lines
  and converts a lost demo into one logged bad frame. **Cheapest risk reduction available.**
- **No CI.** Nothing runs `run_tests.sh` automatically, which is how `main` went red
  unnoticed.
- **`ai-docs/` never updated.** The master plan's merge protocol step 4 called for updating
  `README.md`, `09-invariants.md`, `10-file-map.md`, `11-editing-roadmap.md`. The spec also
  lists three invariants (`...-design.md:151`) that should have been rewritten once Phases
  0/3/4 landed. `clarify_mesh` in particular is documented as refusing operations it now
  supports.
- **Storage grows without bound.** `glb_dir` has no TTL sweep, and every build now writes two
  GLB copies (one in `glb/`, one in `projects/<id>/`).

---

## Suggested order

Cheap-and-unblocking first, then the two real features.

1. **§0** — resolve the routing decision, get `main` green, wire the Composio suites into
   `run_tests.sh`. Blocks everything else.
2. **§7** — `try/catch` the animation loop. Five lines, removes the worst in-headset failure
   mode.
3. **§2a option 1** — per-part dimensional edits via `PARAMS`. Reuses Phase 2, makes the
   thing you noticed as broken deterministic for the common case.
4. **§2b** — stop region commands falling through silently.
5. **§6** — Composio API key + seed data, then verify steps 3–5 against the live API.
6. **§3 — Phase 5 semantic edits.** Answer the four open questions, then write
   `2026-XX-XX-ws-h-semantic-mesh-edits.md` and execute it.
7. **§4 — Phase 6 Drive.** Check the Composio Drive adapter first; it may remove the OAuth
   work entirely. Add the interim "keep this" flag regardless so projects stop expiring.
8. **§5** — undo gesture, history UI, angle snapping. Polish.

Items 1, 2, 4 and the §4 interim flag are small and well-understood — they can be done
without a separate plan doc. Items 6 and 7 need their own plans once their open questions
are answered.
