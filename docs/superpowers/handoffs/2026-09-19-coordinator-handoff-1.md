# Coordinator handoff #1: Wave 1 done, start Wave 2

**From:** the coordinator session that ran brainstorming, spec, master plan and Wave 1.
**Reason:** the user's rule: at ~30% context, hand off to a fresh agent with the same model and effort.

## Where things are

- **Branch:** `feature/in-headset-editing` @ `fdbeac5` (main checkout `/Users/dimural/Perception_HTN_2026`).
- **Wave 1 is merged and green:** WS-A projects/versions/undo, WS-B two-hand nav + tape + dimensions label + absolute size (mesh only), WS-C CAD `PARAMS` core + part names in GLB, WS-D mesh boolean core.
- **Full suite:** `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh` → 10 suites, 514 tests, exit 0. `cd web-client && npx vite build` OK.
- **Nothing has been tried on a headset or in the emulator yet.** The user has **no API keys** (`backend/.env` doesn't exist). The Gemini key is the one they need soonest.

## Read these first

1. `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md`: the design.
2. `docs/superpowers/plans/2026-09-19-in-headset-editing-master.md`: waves, interfaces, global constraints, merge protocol.
3. `ai-docs/11-editing-roadmap.md`: status log, **coordinator gotchas**, **open issues for Wave 2**. `ai-docs/` is git-excluded and local only.
4. `ai-docs/README.md`, then the notes for what you touch: 02, 03, 04, 05, 07, 08, 09 are all current as of fdbeac5.
5. The WS plans in `docs/superpowers/plans/2026-09-19-ws-{a,b,c,d}-*.md`, for the interfaces as actually built.

## Deviations from the master-plan interfaces (all merged)

- WS-A: `info.json` has `last_version` (version numbers are never reused). Extra helpers: `load_info`, `get_project`, `version_url` (projects.py); `_record_version`, `restore_version`, `history_step`, `save_client_version` (pipeline.py); `HistoryRequest` (models.py). `VersionInfo.params` still defaults to `{}`; WS-E must fill it. New project when `_is_new_object_request`, no project, or a lane change.
- WS-B: absolute size is axis-aware (`params={"target_m", "axis"}`); the pipeline refuses `target_m` on CAD with a spoken clarify. `PercyAssistant.cancelTalk()` was added; `updateGrab` keeps `navScale`. `applyDisplaySize` world/local bug fixed.
- WS-C: `params_for_part` strips `_l/_r/_left/_right/_fl/_fr/_rl/_rr/_front/_rear/_back/_<digits>` repeatedly. `ParamError` messages are speakable. Toy-car and keychain prompt examples were fixed.
- WS-D: `drill_hole` `direction` points into the model; `add_loop` `normal` points out; `flatten_base` keeps its frame, `0 < cut_fraction <= 0.5`. A miss raises `BooleanError`. The output has vertex colours, no texture. For a sculpt: `units_per_mm = glb_longest_extent / (base_size_m * scale * 1000)`.

## Next: Wave 2 (per master plan)

Write the detailed interfaces for WS-E / WS-F / WS-G into the master plan (or a wave-2 addendum) **before** dispatching, the same way Wave 1 was done, then dispatch them in parallel with `isolation: "worktree"`, `model: opus`, `subagent_type: general-purpose`.

- **WS-E (wire C):** `VersionInfo.params` filled from `extract_params` on every CAD version; `CommandResponse` gets `cad_params`; `POST /api/projects/{pid}/params` `{updates, session_id}` → `set_params` → sandbox → new version (`op="param_edit"`), no LLM; client dimension panel (drag, `FINE_MODE_FACTOR`, `snap`, live label via `formatLength`); CAD absolute size via PARAMS when a matching key exists.
- **WS-F (selection + region ops):** `interaction/selection.js` (ray/lasso → `{parts, center_m, radius_m, normal, vertex_ids}` in model-local coordinates, part snapping), `interaction/regionOps.js` (scale/pull/push/flatten/smooth/paint with falloff), GLTFExporter → `POST /api/projects/{pid}/versions`, client handles `version_saved` (no reload), `selection` field on `/api/voice` + `/api/command`, router puts it in the codegen payload.
- **WS-G (wire D):** router rung for hole / loop / flat base on a **mesh** session with a selection → `mesh/boolean.py` → new version (`op="boolean"`); narrow `clarify_mesh` to everything else; update `09-invariants.md`.
- Likely overlap: WS-F and WS-G both touch `ai/intent.py`, `/api/voice` and the selection schema. **Define the `selection` pydantic model in the addendum first** so both build against it.

## Open decision for the user (don't decide alone)

- **Sandbox scoping:** `_run_in_sandbox` execs with separate globals/locals, so helper `def`s in generated scripts can't see `PARAMS`. It's mitigated in the prompt only. The fix is a single namespace inside the forked sandbox. It touches the security module (see the invariant "the subprocess is the security boundary"), so ask the user.

## Gotchas you will hit

- **Agent worktrees are created from `444b458` (main), not the feature branch.** Tell every agent to `git merge --ff-only feature/in-headset-editing` first.
- **Agents can't write `/Users/dimural/Perception_HTN_2026/ai-docs/`** (the harness blocks it). They commit proposed text to `docs/superpowers/handoffs/2026-09-19-ws-<x>-ai-docs-additions.md`; you apply it.
- **Before merging a branch:** check it for `.pyc`, `dist/`, `node_modules/` and Claude attribution. Root `.gitignore` exists now, but old worktrees predate it.
- **Conflict resolution:** `git checkout -m` relabels markers; match anchored `^<<<<<<< .*`, `^=======$`, `^>>>>>>> .*`. Comment rulers contain `=======`.
- The sandbox suite can take several minutes when agents run in parallel. Run the full suite with output to a unique log file in the scratchpad.
- **Usage limits** interrupted two agents once. Resume them with SendMessage to their agent id; their commits survive in the worktree.
- After merging, remove the agent worktrees (`git worktree remove --force`); keep the branches.

## Standing rules (from the user; they apply to you and every agent you start)

- **No `Co-Authored-By: Claude` or any Claude attribution in commits.**
- Update docs as you go: `ai-docs/` notes, plus the status log in `ai-docs/11-editing-roadmap.md`.
- **Do independent parts in parallel** with separate worktrees and agents.
- **At ~30% context, hand off:** commit, write `docs/superpowers/handoffs/2026-09-19-coordinator-handoff-<n>.md`, and start a fresh agent with the same model and effort to resume.
- Report progress to the main session in plain, jargon-free language. The user prefers very simple explanations.
