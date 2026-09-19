<!-- WS-A additions for /Users/dimural/Perception_HTN_2026/ai-docs/08-state-and-contracts.md -->

<!-- 1. Add this row to the SessionState table, after the `template`, `params` row: -->

| `project_id`, `version` | "undo" / "go back two" — which project history the model on screen belongs to, and where in it |

<!-- 2. Append these sections after "### Persistence": -->

## Projects and versions

`app/projects.py`. One project per object; every rebuild or hand edit is a version.

```
backend/storage/projects/<project_id>/       project_id = uuid4().hex[:12]
  v1.glb, v2.glb, ...                         served at /media/projects/<pid>/vN.glb
  info.json   {"current", "last_version", "versions": [VersionInfo...],
               "saved_to_drive", "touched_at"}
```

- `VersionInfo` (`app/models.py`): `project_id, version, kind ("cad"|"mesh"), op, glb_url,
  summary, script, params, mesh_prompt, color, base_size_m, parent, created_at`.
  `op` ∈ `generate | set_material | hand_edit | param_edit | boolean | semantic_edit | photo | script`.
- Version numbers are **never reused**. `last_version` in info.json exists for that: after
  undo + a new edit the redo tail is deleted, and the new edit takes `last_version + 1`,
  not the dropped number. Reusing a number would reuse a URL the client and the browser
  cache have already seen with different geometry.
- `append_version` inherits any meta the caller leaves out (`kind, summary, script, params,
  mesh_prompt, color, base_size_m`) from the parent. Explicit `None` is kept.
- Cap: `MAX_VERSIONS = 20`; `prune` keeps v1 (the original), the newest 19, and always
  the current one.
- info.json is written via a temp file + `os.replace`, like `sessions.json`.

### Who records versions

`pipeline.py:_record_version` runs after every path that sets `rebuilt=True` (`generate`
both lanes, `set_material`, `execute_script`, legacy `create`/`modify`,
`build_from_image`'s three success paths, `execute_script_direct`). It moves the fresh
GLB from `glb_dir` into the project and rewrites `session.model_id` to
`f"{project_id}-v{n}"` and `session.glb_url` to the version URL.

- **New project** when `ai.intent._is_new_object_request(transcript)` is true, when
  the session has no live project, or when the lane (`kind`) differs from the project's
  current version. Photo builds always start one. Everything else appends.
- **Best effort.** If the GLB is not on disk or anything fails, it logs and returns
  `None`, and the legacy `/media/glb/<id>.glb` URL stays. Old sessions' `/media/glb`
  URLs keep working, and `glb_dir` is not swept.

### Undo / redo

`action="undo"|"redo"` with `params={"steps": n}` (voice rung in `ai/intent.py`, or
`POST /api/projects/{pid}/undo|redo`). `pipeline.py:restore_version` points the session
at the stored version and restores `last_backend (=kind), last_script, last_summary,
last_mesh_prompt, color, base_size_m`, and clears `template/params`. `scale` is left
alone (it is display zoom, not a version). Steps clamp at either end.

| Situation | Response |
|---|---|
| Moved | `rebuilt=True`, versioned `model_id` + `glb_url`, reply "Undone." / "Redone." |
| At the end of history | `ok=True, action="noop"`, "Nothing to undo." / "Nothing to redo." |
| Session has no project | `ok=True, action="noop"`, "There's nothing to undo yet." |
| Route with an unknown project id | `ok=False, action="clarify"`, "I can't find that model's history." |

### Cleanup

`projects.cleanup(settings, now=None) -> {"audio", "ref", "projects"}`, run from the
`app/main.py` lifespan task on startup and every `CLEANUP_INTERVAL_S = 3600`.

| What | TTL | Measured by |
|---|---|---|
| `audio_dir` files | `AUDIO_TTL_S = 3600` | file mtime |
| `ref_dir` files (staged photos) | `REF_TTL_S = 86400` | file mtime |
| project folders | `PROJECT_TTL_S = 7 days` | `touched_at` (dir mtime fallback); skipped when `saved_to_drive` |

Dot-files are never deleted. `touched_at` is refreshed on every info.json write:
create, append, undo, redo, prune, mark_saved_to_drive.

<!-- 3. In "## The swap contract", replace the paragraph starting
"The one deliberate exception is `set_scale`" with: -->

There are two deliberate exceptions:

- `set_scale` ships no new asset and has its own client branch.
- `version_saved` (`POST /api/projects/{pid}/versions`) returns `rebuilt=False` **with**
  a new `model_id` and `glb_url`. The client already shows the edited geometry (it
  exported the GLB itself), so it must update its version pointer only and not reload.
  Undo and redo are *not* exceptions: they return `rebuilt=True` and go through the
  normal swap.

<!-- 4. Append at the end of the file: -->

## New routes (WS-A), to move into 02-api-surface.md

| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| GET | `/api/projects/{pid}` | — | `{"ok": True, project_id, current, versions[], saved_to_drive, touched_at}` or `{"ok": False, "error"}` | Read-only |
| POST | `/api/projects/{pid}/undo` | `{steps=1, session_id="default"}` | `CommandResponse`, `action="undo"`, `rebuilt=True` | `noop` when there is nothing to undo; `clarify` for an unknown pid |
| POST | `/api/projects/{pid}/redo` | same | `CommandResponse`, `action="redo"` | same |
| POST | `/api/projects/{pid}/versions` | multipart `glb` (binary glTF), `op="hand_edit"`, `summary?`, `session_id` | `CommandResponse`, `action="version_saved"`, `rebuilt=False` | Silent (no audio). An unknown `op` is stored as `hand_edit`. A non-glTF upload → `clarify` |
| static | `/media/projects/<pid>/vN.glb` | — | GLB | Mounted on `settings.projects_dir` |

New `action` values: `undo`, `redo` (the client swaps via the triple, so it needs no
new branch), and `version_saved` (the client must *not* reload; WS-F wires it).
