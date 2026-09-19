# WS-A: Projects, versions, undo/redo, cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every successful build becomes a numbered version inside a per-object project on disk, with undo/redo (voice and HTTP), client version upload, and TTL cleanup.

**Architecture:** A new pure-filesystem module `app/projects.py` owns `storage/projects/<pid>/{vN.glb,info.json}`. `app/pipeline.py` calls one helper, `_record_version`, after every `rebuilt=True` path; it moves the fresh GLB out of `glb_dir` into the project and rewrites `model_id`/`glb_url` on the session. Undo/redo are a pipeline branch (`action="undo"|"redo"`) reached both from a new router rung in `ai/intent.py` and from `POST /api/projects/{pid}/undo|redo`. Cleanup runs from a lifespan task in `app/main.py`.

**Tech Stack:** FastAPI 0.141 + pydantic v2, stdlib `shutil`/`tempfile`/`os.replace`, FastAPI `TestClient` (httpx 0.28) for route tests.

**Spec:** `docs/superpowers/specs/2026-09-19-in-headset-editing-design.md` §1, §2; master plan `docs/superpowers/plans/2026-09-19-in-headset-editing-master.md` section WS-A (the "Produces" block is binding).

## Global Constraints

- Commits must NOT contain a `Co-Authored-By: Claude` trailer or any Claude attribution.
- Python: `/Users/dimural/Perception_HTN_2026/.venv/bin/python`; run backend tests from `backend/` as `python -m <pkg>.test_<x>`.
- Tests are plain scripts with a `__main__` runner printing `TOTAL: n/m passed`, non-zero exit on failure. No pytest. Every new suite is registered in `run_tests.sh`.
- Full suite green: `PATH=/Users/dimural/Perception_HTN_2026/.venv/bin:$PATH ./run_tests.sh`.
- `reply` strings are spoken: no paths, no exception names, no markdown.
- Routes never raise to the client: catch, log, return `ok=False, action="clarify"`.
- Only `/Users/dimural/Perception_HTN_2026/ai-docs/08-state-and-contracts.md` is edited in ai-docs (plus a "New routes (WS-A)" section in it for the coordinator).
- `ai/intent.py`: only the undo/redo rung.
- The swap triple (`rebuilt && model_id && glb_url`) stays the only reload path; `version_saved` deliberately returns `rebuilt=False`.

## Design decisions (read before coding)

- `project_id = uuid4().hex[:12]`; `model_id = f"{project_id}-v{n}"`, always new.
- `info.json` = `{"current", "last_version", "versions", "saved_to_drive", "touched_at"}`. `last_version` is an **additive** field (not in the master plan's list) that makes version numbers never reused after a redo tail is truncated (otherwise v3 → undo → new edit would reuse `v3.glb`'s URL and the client/browser cache could show the stale file).
- `append_version` fills any meta key the caller did not pass (`kind`, `summary`, `script`, `params`, `mesh_prompt`, `color`, `base_size_m`) from the parent (= current) version. Explicit `None` is respected.
- `undo`/`redo` step through the version **list** (pruned versions are gone), clamp at the ends, and return `None` only when they cannot move at all.
- `prune` keeps v1, the newest `keep-1`, and always the current version.
- `_record_version` is best-effort: if the built GLB is not on disk, or anything fails, it logs and returns `None`, and the legacy `/media/glb/<id>.glb` URL stays. This keeps every existing mocked pipeline test valid.
- New object vs follow-up: new project when `ai.intent._is_new_object_request(transcript)` is true, the session has no live project, or the lane (`kind`) differs from the project's current version. Photo builds always create a project. `set_material`, `execute_script` and legacy `modify` append.
- Undo/redo restore `project_id, version, model_id, glb_url, last_backend(kind), last_script, last_summary, last_mesh_prompt, color, base_size_m` and clear `template/params`; `scale` is left alone (display zoom).
- Nothing to undo → `ok=True, action="noop", rebuilt=False`, reply "Nothing to undo." / "Nothing to redo." / "There's nothing to undo yet." Unknown project on a route → `ok=False, action="clarify"`.
- Cleanup skips dot-files, counts `{"audio", "ref", "projects"}`, and uses file mtime for audio/ref and `touched_at` (fallback dir mtime) for projects. Legacy `glb_dir` is not swept.
- Pre-existing issue found: `ai/test_intent.py` is not in `run_tests.sh` and has one stale failure (`settings_off` lacks `hf_space_enabled=False`, so `mesh_ready` is True). Task 5 fixes the test settings and registers the suite.

---

### Task 1: Models and settings

**Files:** Modify `backend/app/models.py`, `backend/app/config.py`. Test: `backend/app/test_projects.py` (create).

**Produces:** `VersionInfo` (exact master-plan fields; `params` uses `Field(default_factory=dict)`), `SessionState.project_id: str | None = None`, `SessionState.version: int | None = None`, `HistoryRequest(steps: int = 1, session_id: str = "default")`, `Settings.projects_dir = STORAGE / "projects"` created in `get_settings`.

- [ ] Step 1: create `app/test_projects.py` with runner and `test_models()` asserting `VersionInfo(project_id="p", version=1, kind="cad", op="generate", glb_url="/x", created_at=1.0).params == {}`, `SessionState().project_id is None and .version is None`, `HistoryRequest().steps == 1`, `get_settings().projects_dir.is_dir()`.

```python
def _check(name, cond, detail=""):
    if cond:
        print(f"  [ok] {name}"); return 1, 0
    print(f"  [FAIL] {name} {detail}"); return 0, 1
```

- [ ] Step 2: run `python -m app.test_projects` → ImportError on `VersionInfo`.
- [ ] Step 3: add the models and setting:

```python
class VersionInfo(BaseModel):
    project_id: str
    version: int
    kind: str
    op: str
    glb_url: str
    summary: str | None = None
    script: str | None = None
    params: dict[str, float] = Field(default_factory=dict)
    mesh_prompt: str | None = None
    color: str | None = None
    base_size_m: float | None = None
    parent: int | None = None
    created_at: float

class HistoryRequest(BaseModel):
    steps: int = 1
    session_id: str = "default"
```

`config.py`: `projects_dir: Path = STORAGE / "projects"` and `settings.projects_dir.mkdir(parents=True, exist_ok=True)`.

- [ ] Step 4: rerun, expect `TOTAL: 4/4 passed`.
- [ ] Step 5: register `python3 -m app.test_projects` in `run_tests.sh` as "Projects Tests"; commit "Add VersionInfo, session project pointer, projects_dir".

### Task 2: `projects.py` create / append / get / current / prune

**Files:** Create `backend/app/projects.py`; test in `app/test_projects.py`.

**Produces:** `MAX_VERSIONS`, `AUDIO_TTL_S`, `REF_TTL_S`, `PROJECT_TTL_S`, `create_project`, `append_version`, `get_version`, `current_version`, `prune`, plus helpers `load_info(settings, pid) -> dict | None` and `get_project(settings, pid) -> dict | None` (JSON-safe copy of info.json).

Tests use `SimpleNamespace(projects_dir=tmp/"projects", audio_dir=..., ref_dir=..., glb_dir=...)` and a helper `_glb(dir, name)` that writes `b"glTF" + bytes(20)`.

- [ ] Step 1: tests:
  - `create_project(s, "cad", src, op="generate", script="S1", summary="a box")` → v1, file moved (src gone, `<pid>/v1.glb` exists), `glb_url == f"/media/projects/{pid}/v1.glb"`, `current_version(...).version == 1`, `parent is None`, `info.json` has `current == 1`, `saved_to_drive is False`, a `touched_at`.
  - `append_version(s, pid, src2, op="set_material", color="#FF0000")` → v2, `parent == 1`, inherits `script == "S1"`, `kind == "cad"`, `summary == "a box"`, and `color == "#FF0000"`.
  - explicit `script=None` stays None.
  - `append_version` on an unknown pid raises `ValueError`; `get_version`/`current_version` on unknown or path-like pid (`"../x"`) return None.
  - prune: create + 24 appends with `MAX_VERSIONS=20` → 20 versions, first is v1, last is v25, `v2.glb` gone from disk, `v25.glb` present.
- [ ] Step 2: run → ImportError.
- [ ] Step 3: implement (see "Design decisions"): `_project_dir` validates pid with `^[A-Za-z0-9_-]{1,64}$`; `_write_info` sets `touched_at` then writes via `tempfile.mkstemp` in the project dir + `os.replace`; `_new_version` builds `VersionInfo` from allowed meta keys; `append_version` truncates the redo tail (deleting its files), computes `n = max(last_version, max existing) + 1`, inherits missing meta from the parent, moves the file, writes, then `prune`s.
- [ ] Step 4: run → all pass.
- [ ] Step 5: commit "Add project version store".

### Task 3: undo / redo / mark_saved_to_drive

**Produces:** `undo(settings, pid, steps=1)`, `redo(settings, pid, steps=1)`, `mark_saved_to_drive(settings, pid)`.

- [ ] Step 1: tests: v1..v3; `undo` → v2; `undo(steps=5)` → v1 (clamped); `undo` again → None; `redo(steps=2)` → v3; `redo` → None; undo to v2 then `append_version` → version **4** (never reuses 3), `v3.glb` deleted, versions `[1,2,4]`, `redo` → None; `mark_saved_to_drive` → `load_info(...)["saved_to_drive"] is True`; undo on unknown pid → None.
- [ ] Step 2: run → fail (AttributeError).
- [ ] Step 3: implement `_step(settings, pid, delta)` over list indexes; `undo`/`redo` call it with `-max(1, steps)`/`+max(1, steps)`.
- [ ] Step 4: pass. Step 5: commit "Add undo/redo over project versions".

### Task 4: cleanup

**Produces:** `cleanup(settings, now=None) -> {"audio": int, "ref": int, "projects": int}`.

- [ ] Step 1: tests: audio file mtime now-2h (deleted) and now-10min (kept); ref file now-25h (deleted) and now-1h (kept); `.gitkeep` 30 days old in audio (kept); project A untouched (cleaned with `now=time.time()+8*86400`), project B `mark_saved_to_drive` (kept), a fresh project C checked with `now=time.time()` (kept). Counts equal `{"audio": 1, "ref": 1, "projects": 1}` for the aged call. A missing directory does not raise.
- [ ] Step 2: fail. Step 3: implement with per-entry try/except. Step 4: pass. Step 5: commit "Add TTL cleanup for audio, ref photos and projects".

### Task 5: Router undo/redo rung

**Files:** Modify `backend/ai/intent.py` (new `_HISTORY_RE`, `_check_history`, one call at the top of `parse_intent` right after `_normalize_transcript`), `backend/ai/test_intent.py`, `run_tests.sh`.

**Produces:** `Intent(action="undo"|"redo", params={"steps": n}, reply="Undone."|"Redone.")`, no network call.

```python
_HISTORY_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "twice": 2, "thrice": 3}
_HISTORY_RE = re.compile(
    r"^(?:please\s+)?(?:(?:can|could) you\s+)?"
    r"(?P<verb>undo|redo|go back|go forward|step back)"
    r"(?:\s+(?:that|it|this|the last (?:change|edit|step)))?"
    r"(?:\s+(?P<n>\d+|one|two|three|four|five|twice|thrice)"
    r"(?:\s+(?:steps?|times|changes?|edits?|versions?))?)?"
    r"(?:\s+please)?[\s.!?]*$",
    re.I,
)
```

- [ ] Step 1: `test_history_rung()` using `parse_intent` with a `SimpleNamespace` settings that has no LLM keys and `last_backend="cad"`, `current_script="x"`: `undo`→(undo,1), `Undo.`→(undo,1), `undo that`→1, `go back`→1, `go back two`→2, `go back 3 steps`→3, `undo twice`→2, `redo`→(redo,1), `go forward two`→(redo,2), `please undo the last change`→1. Negatives (action not undo/redo): `go back to the round one`, `undo the hole and make it taller`, `build me a redo button`. Also a mesh session `undo` → undo (rung above the mesh branch). Fix the stale `settings_off` by adding `hf_space_enabled=False`. Register `python3 -m ai.test_intent` in `run_tests.sh` as "Intent Tests".
- [ ] Step 2: run → fails. Step 3: implement. Step 4: `TOTAL` all passed. Step 5: commit "Add undo/redo router rung and register intent tests".

### Task 6: Pipeline records versions

**Files:** Modify `backend/app/pipeline.py`, `backend/app/test_pipeline.py`.

**Produces:** `_record_version(session, settings, model_id, op, new_object=False) -> str | None`; `restore_version(session, info) -> None`.

Call sites: in `apply_intent` once after the branch chain: `if rebuilt and result_model_id and action in _VERSION_OPS` where `_VERSION_OPS = {"generate": "generate", "set_material": "set_material", "execute_script": "script", "create": "generate", "modify": "generate"}` and `new_object = action == "create" or (action == "generate" and _is_new_object_request(transcript or ""))`; if it returns an id, `result_model_id = versioned`. In `build_from_image`: each of the three success returns records with `op="photo", new_object=True`. In `execute_script_direct`: `op="script"`.

- [ ] Step 1: tests with a real temp dir (`settings.glb_dir`, `settings.projects_dir`), `_execute_with_retry` patched with a side effect that writes `glb_dir/<id>.glb` and sets `last_script`/`last_backend="cad"` like the real one; `save_session`/`synthesize_speech` patched:
  - "build me a box" → `model_id == f"{pid}-v1"`, `glb_url == /media/projects/<pid>/v1.glb`, file exists, `session.version == 1`.
  - follow-up "make it taller" → same pid, v2, parent 1, new model_id.
  - "build me a ring" → different pid.
  - set_material follow-up → op "set_material".
  - build_from_image success (patched `generate_mesh_glb_from_image` writing the file) → new project kind "mesh", op "photo".
  - legacy behaviour: mocked build with no file on disk → model_id unchanged (already covered by existing tests; assert once explicitly).
- [ ] Step 2: fail. Step 3: implement. Step 4: pass (existing 38 still pass). Step 5: commit "Record a project version for every rebuild".

### Task 7: Undo/redo in the pipeline + client version save

**Produces:** `apply_intent` branch for `undo`/`redo` (uses `intent.params["steps"]`, optional `intent.params["project_id"]`), `history_step(session, settings, project_id, direction, steps) -> CommandResponse`, `save_client_version(session, settings, project_id, glb_bytes, op, summary=None) -> CommandResponse`.

- [ ] Step 1: tests: after two builds (v1 script "S1", v2 script "S2"), `apply_intent(Intent(action="undo", params={"steps": 1}))` → `rebuilt`, `model_id == f"{pid}-v1"`, `session.last_script == "S1"`, glb_url v1; redo → v2 / "S2"; undo on a session with no project → `ok`, `action == "noop"`, not rebuilt, reply "There's nothing to undo yet."; at v1 undo → "Nothing to undo."; `history_step` with unknown pid → `ok False`, clarify. `save_client_version(..., b"glTF"+bytes(40), "hand_edit")` → `action == "version_saved"`, `rebuilt is False`, `model_id == f"{pid}-v3"`, file exists, session.version 3, inherited script; non-glTF bytes → clarify, no new version; unknown op string → stored as "hand_edit".
- [ ] Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit "Add undo/redo and client version save to the pipeline".

### Task 8: Routes, media mount, cleanup task

**Files:** Modify `backend/app/main.py`; tests in `app/test_projects.py` using `fastapi.testclient.TestClient`.

Routes: `GET /api/projects/{pid}` → `{"ok": True, **get_project}` or `{"ok": False, "error": "Unknown project"}`; `POST /api/projects/{pid}/undo` and `/redo` with `HistoryRequest` → `history_step`; `POST /api/projects/{pid}/versions` multipart (`glb` file, `op` form, optional `summary`, `session_id`) → `save_client_version`. Each wrapped in try/except returning a clarify `CommandResponse`. Mount `/media/projects`. Lifespan: `asyncio.create_task(_cleanup_loop())` running `projects.cleanup` via `asyncio.to_thread` immediately and then every `CLEANUP_INTERVAL_S = 3600`; cancelled on shutdown.

- [ ] Step 1: tests (patch `main.settings` dirs to temp, `app.session.SESSION_FILE` to temp, `app.pipeline.synthesize_speech`): create project directly with `projects.create_project`, then GET → ok and 1 version; POST versions (multipart) → `version_saved`; POST undo → rebuilt, v1 URL; POST redo → v2; GET unknown → ok False; POST undo on unknown → clarify; `GET /media/projects/<pid>/v1.glb` → 200; lifespan: patch `app.main.projects.cleanup` with a MagicMock, `with TestClient(app):` poll ≤2 s until called.
- [ ] Step 2: fail. Step 3: implement. Step 4: pass; run full `run_tests.sh`. Step 5: commit "Add project routes, media mount and hourly cleanup".

### Task 9: Docs

- [ ] Update `/Users/dimural/Perception_HTN_2026/ai-docs/08-state-and-contracts.md`: SessionState `project_id`/`version`, project storage + `info.json`, version recording rules, undo/redo restore, `version_saved` exception to the swap contract, cleanup TTLs, and a "New routes (WS-A) — for 02-api-surface.md" section. (ai-docs is git-excluded; nothing to commit.)
