# Semantic mesh edits (WS-H) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pinch a spot on a sculpt, say "give it wings", and get a new version of that sculpt
with wings — by editing a render of it with Gemini and pushing the result through the
existing image-to-3D lane.

**Architecture:** The headset renders the current view to a PNG, projects the pinched 3D
point to 2D, draws a circle there, and uploads it. The server edits that PNG with Gemini
(~7.5 s), writes it to `ref_dir`, feeds it to `generate_mesh_glb_from_image` (~30–60 s), and
appends a version with `op="semantic_edit"` and `parent` set. The whole thing runs as a
polled job so the HUD can show progress.

**Tech Stack:** FastAPI + pydantic v2, httpx via `app.httpclient.get_http_client()`, Gemini
`gemini-2.5-flash-image`, trimesh, Three.js WebXR (Vite), Node 20 for client pure-function
tests.

**Spec:** `docs/superpowers/specs/2026-09-19-semantic-mesh-edits-design.md`

> **Status: implemented** (commit `de7f91b`). All seven tasks executed. Verified end to end
> against the live Gemini API with the 3D build stubbed: 9.4 s for the edit, the change landed
> inside the circle, the circle did not appear in the output, and the result was appended as a
> new version with `op="semantic_edit"` and `parent` set.
>
> Two deviations from the plan as written, both recorded in the commit:
> - `parent=` is **not** passed to `append_version`; that function derives it and `_RESERVED`
>   strips any caller value.
> - The plan placed the router rung before `_check_scale_only`. It is placed **after** it, last
>   among the mesh rungs, so every deterministic path keeps winning.

## Global Constraints

- **Commits must NOT carry any AI-assistant attribution: no `Co-Authored-By` trailer, no
  "generated with" line, no tool name in the message or the author field.**
- Python: use `/Users/dimural/Perception_HTN_2026/.venv/bin/python` (3.12). Run backend tests
  from `backend/` as `python -m <pkg>.test_<x>`.
- Test style matches the repo: plain scripts with a `__main__` runner that prints
  `TOTAL: n/m passed` and exits non-zero on failure. **No pytest.** Register every new suite
  in `run_tests.sh`.
- Full suite must stay green: `./run_tests.sh` (it selects `.venv` itself).
- `reply` strings are spoken aloud: no paths, no exception names, no markdown.
- Routes never raise to the client: catch, log, return `ok=False, action="clarify"`.
- **Never call the live Gemini API from a test.** Fake the HTTP client, as
  `mesh/test_meshy.py` does.
- New client logic goes in `web-client/src/interaction/*.js`; `main.js` gets thin wiring only.

## Pre-flight: `main` is currently red

`./run_tests.sh` fails on `main` before any of this work
(`ai.test_intent`, the `pull_app` vs `find_photos` routing decision — see
`docs/superpowers/plans/2026-09-19-remaining-work.md` §0). **Resolve that first**, or you
cannot tell whether your own changes broke something.

## File Structure

| File | Responsibility |
|---|---|
| `backend/mesh/edit.py` (create) | One job: image bytes + instruction → edited image bytes, via Gemini. No 3D, no routing, no rendering. |
| `backend/mesh/test_edit.py` (create) | Faked-HTTP tests for the above. |
| `backend/app/config.py` (modify) | Add `gemini_image_model`. |
| `backend/app/pipeline.py` (modify) | `semantic_edit` orchestration + job. |
| `backend/app/main.py` (modify) | `POST /api/projects/{id}/semantic_edit`. |
| `backend/ai/intent.py` (modify) | Router rung for semantic edits on mesh sessions. |
| `web-client/src/interaction/viewCapture.js` (create) | Pure 2D math: project a point, place the circle. |
| `web-client/src/main.js` (modify) | Render-to-PNG + POST wiring only. |

---

### Task 1: Gemini image edit client

**Files:**
- Create: `backend/mesh/edit.py`
- Test: `backend/mesh/test_edit.py`
- Modify: `backend/app/config.py`, `run_tests.sh`

**Interfaces:**
- Consumes: `app.httpclient.get_http_client`, `app.config.Settings`
- Produces:
  ```python
  class EditError(ValueError): ...          # message is speakable
  async def edit_image(
      png: bytes, instruction: str, settings: Settings, timeout_s: float = 90.0
  ) -> bytes                                 # returns edited image bytes
  ```

- [x] **Step 1: Add the model setting**

In `backend/app/config.py`, beside `gemini_model`:

```python
    gemini_model: str = "gemini-2.0-flash"
    # Image-in / image-out model for semantic sculpt edits (mesh/edit.py).
    gemini_image_model: str = "gemini-2.5-flash-image"
```

- [x] **Step 2: Write the failing test**

Create `backend/mesh/test_edit.py`:

```python
#!/usr/bin/env python3
"""Gemini image-edit client tests — mocked HTTP, no live API."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesh.edit import EditError, edit_image


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            req = httpx.Request("POST", "https://generativelanguage.googleapis.com/")
            raise httpx.HTTPStatusError("err", request=req, response=self)

    def json(self):
        return self._payload


def _settings():
    return SimpleNamespace(gemini_api_key="k", gemini_image_model="gemini-2.5-flash-image")


def _ok_payload(raw: bytes):
    return {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png",
                        "data": base64.b64encode(raw).decode()}}]}}]}


def _check(name, cond, detail=""):
    if cond:
        print(f"  [ok] {name}")
        return 1, 0
    print(f"  [FAIL] {name} {detail}")
    return 0, 1


def _add(total, result):
    return total[0] + result[0], total[1] + result[1]


def test_returns_edited_bytes():
    print("\n=== Test: edit_image returns the image part ===")
    seen = {}

    async def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        return _Resp(_ok_payload(b"EDITED"))

    client = SimpleNamespace(post=fake_post)
    with patch("mesh.edit.get_http_client", lambda: client):
        out = asyncio.run(edit_image(b"SRC", "give it wings", _settings()))

    t = _check("returns the decoded image bytes", out == b"EDITED", out)
    t = _add(t, _check("uses the configured image model",
                       "gemini-2.5-flash-image" in seen["url"], seen["url"]))
    parts = seen["json"]["contents"][0]["parts"]
    t = _add(t, _check("sends the instruction text",
                       any("wings" in p.get("text", "") for p in parts)))
    t = _add(t, _check("sends the source image inline",
                       any("inline_data" in p for p in parts)))
    return t


def test_missing_image_part_is_speakable():
    print("\n=== Test: a text-only reply raises a speakable error ===")

    async def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({"candidates": [{"content": {"parts": [{"text": "I can't."}]}}]})

    client = SimpleNamespace(post=fake_post)
    t = (0, 0)
    with patch("mesh.edit.get_http_client", lambda: client):
        try:
            asyncio.run(edit_image(b"SRC", "give it wings", _settings()))
            t = _add(t, _check("raises EditError", False, "no exception"))
        except EditError as err:
            msg = str(err)
            t = _add(t, _check("raises EditError", True))
            t = _add(t, _check("message is speakable",
                               "." not in msg.split()[-1][:-1] and "Error" not in msg, msg))
    return t


def test_http_error_is_speakable():
    print("\n=== Test: an HTTP error raises a speakable error ===")

    async def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({"error": "boom"}, status_code=500)

    client = SimpleNamespace(post=fake_post)
    t = (0, 0)
    with patch("mesh.edit.get_http_client", lambda: client):
        try:
            asyncio.run(edit_image(b"SRC", "give it wings", _settings()))
            t = _add(t, _check("raises EditError", False, "no exception"))
        except EditError:
            t = _add(t, _check("raises EditError", True))
    return t


def test_no_key_raises():
    print("\n=== Test: a missing key raises before any HTTP call ===")
    s = SimpleNamespace(gemini_api_key="", gemini_image_model="m")
    t = (0, 0)
    try:
        asyncio.run(edit_image(b"SRC", "x", s))
        t = _add(t, _check("raises without a key", False))
    except EditError:
        t = _add(t, _check("raises without a key", True))
    return t


def run_all_tests():
    print("=" * 60)
    print("GEMINI IMAGE EDIT TESTS")
    print("=" * 60)
    total = (0, 0)
    for fn in TESTS:
        try:
            total = _add(total, fn())
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {fn.__name__} crashed: {exc}")
            total = _add(total, (0, 1))
    passed, failed = total
    print("\n" + "=" * 60)
    print(f"TOTAL: {passed}/{passed + failed} passed")
    if failed:
        print(f"\n{failed} TESTS FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


TESTS = [
    test_returns_edited_bytes,
    test_missing_image_part_is_speakable,
    test_http_error_is_speakable,
    test_no_key_raises,
]

if __name__ == "__main__":
    sys.exit(run_all_tests())
```

- [x] **Step 3: Run it to verify it fails**

Run: `cd backend && ../.venv/bin/python -m mesh.test_edit`
Expected: FAIL with `ModuleNotFoundError: No module named 'mesh.edit'`

- [x] **Step 4: Write the implementation**

Create `backend/mesh/edit.py`:

```python
"""Semantic sculpt edits: edit a render of the model with Gemini.

There is no model that edits a 3D mesh semantically, but there are good ones
that edit images. The headset sends a PNG of what it is looking at, with a
circle drawn where the user pinched; this asks Gemini to make the change
inside that circle and hands the edited image back. Turning that image into a
mesh is app/pipeline.py's job, not this module's.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.config import Settings
from app.httpclient import get_http_client

logger = logging.getLogger(__name__)


class EditError(ValueError):
    """Message is spoken aloud: no paths, no exception names."""


_PROMPT = (
    "Edit this picture of a 3D model. {instruction}. "
    "Make the change only inside the red circle. "
    "Do not draw the red circle in your output. "
    "Keep the rest of the object identical, same colours, same proportions, "
    "same camera angle. Plain white background."
)

_NO_IMAGE = "I couldn't picture that change"
_FAILED = "That edit didn't come back"


def _image_part(payload: dict[str, Any]) -> bytes | None:
    """First inline image in the response, or None. Accepts either key spelling."""
    for cand in payload.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                try:
                    return base64.b64decode(blob["data"])
                except Exception:
                    return None
    return None


async def edit_image(
    png: bytes, instruction: str, settings: Settings, timeout_s: float = 90.0
) -> bytes:
    """Return edited image bytes, or raise EditError with a speakable message."""
    if not settings.gemini_api_key:
        raise EditError(_FAILED)

    model = getattr(settings, "gemini_image_model", "") or "gemini-2.5-flash-image"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={settings.gemini_api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _PROMPT.format(instruction=instruction.strip().rstrip("."))},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(png).decode(),
                        }
                    },
                ]
            }
        ]
    }

    client = get_http_client()
    try:
        resp = await client.post(
            url, headers={"Content-Type": "application/json"}, json=payload,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Gemini image edit failed: %s", exc)
        raise EditError(_FAILED) from exc

    raw = _image_part(data)
    if not raw:
        logger.info("Gemini image edit returned no image part")
        raise EditError(_NO_IMAGE)
    return raw
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m mesh.test_edit`
Expected: `TOTAL: 9/9 passed` (counts may differ slightly; zero failures is the gate)

- [x] **Step 6: Register the suite**

In `run_tests.sh`, add `mesh.test_edit` to the `for M in ...` list.

Run: `./run_tests.sh`
Expected: `mesh.test_edit: PASSED`, and no suite regressed.

- [x] **Step 7: Commit**

```bash
git add backend/mesh/edit.py backend/mesh/test_edit.py backend/app/config.py run_tests.sh
git commit -m "Add Gemini image-edit client for semantic sculpt edits"
```

---

### Task 2: Client view capture (pure 2D math)

**Files:**
- Create: `web-client/src/interaction/viewCapture.js`
- Test: `web-client/src/interaction/test_interaction.js` (append)

**Interfaces:**
- Consumes: nothing (no Three.js import — plain numbers, so Node can test it)
- Produces:
  ```js
  export function ndcToPixels(ndc, width, height)   // {x:-1..1,y:-1..1} -> {x,y} px
  export function circleRadiusPx(width, height, fraction = 0.22)
  export function clampCircle(cx, cy, r, width, height)  // keep it on-canvas
  ```

`main.js` gets the NDC from `vector.project(camera)`; everything after that is this module,
so it is testable without a GPU.

- [x] **Step 1: Write the failing tests**

Append to `web-client/src/interaction/test_interaction.js`, and add the import at the top
beside the existing interaction imports:

```js
import { ndcToPixels, circleRadiusPx, clampCircle } from "./viewCapture.js";
```

Then add this section. The file's harness is `test(name, fn)` with `assert` / `eq` / `near`
helpers and a `console.log` section header — match it exactly:

```js
console.log("\n=== viewCapture.js ===");

test("ndcToPixels maps the centre to the middle of the canvas", () => {
  const p = ndcToPixels({ x: 0, y: 0 }, 1024, 512);
  eq(p.x, 512);
  eq(p.y, 256);
});

test("ndcToPixels flips Y (NDC is up-positive, pixels are down-positive)", () => {
  eq(ndcToPixels({ x: 0, y: 1 }, 100, 100).y, 0);
  eq(ndcToPixels({ x: 0, y: -1 }, 100, 100).y, 100);
});

test("ndcToPixels maps the right edge", () => {
  eq(ndcToPixels({ x: 1, y: 0 }, 800, 600).x, 800);
});

test("circleRadiusPx scales with the smaller dimension", () => {
  eq(circleRadiusPx(1000, 500, 0.2), 100);
});

test("clampCircle keeps a circle near the edge fully on canvas", () => {
  const c = clampCircle(5, 5, 40, 400, 400);
  assert(c.cx >= c.r && c.cy >= c.r, `clamped to ${c.cx},${c.cy} r=${c.r}`);
});

test("clampCircle leaves a centred circle alone", () => {
  const c = clampCircle(200, 200, 40, 400, 400);
  eq(c.cx, 200);
  eq(c.cy, 200);
  eq(c.r, 40);
});

test("clampCircle shrinks a radius bigger than the canvas", () => {
  assert(clampCircle(200, 200, 500, 400, 400).r <= 200, "radius not clamped");
});
```

- [x] **Step 2: Run it to verify it fails**

Run: `node web-client/src/interaction/test_interaction.js`
Expected: FAIL — `Cannot find module './viewCapture.js'`

- [x] **Step 3: Write the implementation**

Create `web-client/src/interaction/viewCapture.js`:

```js
/**
 * Pure 2D helpers for the semantic-edit capture.
 *
 * main.js does the Three.js part (render to a canvas, and
 * `point.project(camera)` to get normalised device coordinates). Everything
 * after that is plain arithmetic and lives here so Node can test it without
 * a GPU or a WebXR session.
 */

/** NDC ({x,y} each -1..1, Y up) -> canvas pixels (Y down). */
export function ndcToPixels(ndc, width, height) {
  return {
    x: ((ndc.x + 1) / 2) * width,
    y: ((1 - ndc.y) / 2) * height,
  };
}

/**
 * The circle is a fixed share of the view rather than a projected 3D radius:
 * the tap is a point (radius 0), and "close enough" beats a size the user
 * cannot control anyway.
 */
export function circleRadiusPx(width, height, fraction = 0.22) {
  return Math.min(width, height) * fraction;
}

/** Keep the circle fully on-canvas so the edit region is never clipped. */
export function clampCircle(cx, cy, r, width, height) {
  const radius = Math.min(r, width / 2, height / 2);
  return {
    cx: Math.min(Math.max(cx, radius), width - radius),
    cy: Math.min(Math.max(cy, radius), height - radius),
    r: radius,
  };
}
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `node web-client/src/interaction/test_interaction.js`
Expected: all pass, total up by 7.

- [x] **Step 5: Commit**

```bash
git add web-client/src/interaction/viewCapture.js web-client/src/interaction/test_interaction.js
git commit -m "Add pure 2D view-capture helpers for semantic edits"
```

---

### Task 3: Pipeline orchestration

**Files:**
- Modify: `backend/app/pipeline.py`
- Test: `backend/app/test_pipeline.py`

**Interfaces:**
- Consumes: `mesh.edit.edit_image`, `mesh.factory.generate_mesh_glb_from_image`,
  `app.projects.append_version`
- Produces:
  ```python
  async def apply_semantic_edit(
      session: SessionState, settings: Settings, project_id: str,
      png: bytes, instruction: str,
  ) -> CommandResponse     # rebuilt=True on success; clarify on any failure
  ```

**Behaviour contract:** on success, append exactly one version with `op="semantic_edit"` and
`parent` set to the version that was edited. On **any** failure, append nothing and leave the
current version in place.

- [x] **Step 1: Write the failing tests**

Add to `backend/app/test_pipeline.py`, matching its existing style:

```python
def test_semantic_edit_appends_a_version():
    print("\n=== Test: semantic edit appends one version with a parent ===")
    import asyncio
    from unittest.mock import patch
    from app import pipeline

    t = (0, 0)
    s, root = _tmp_settings()          # reuse the file's existing helper
    session = _session_with_mesh_project(s)   # helper: a mesh project at v1
    before = projects.current_version(s, session.project_id)

    async def fake_edit(png, instruction, settings, timeout_s=90.0):
        return b"EDITED-PNG"

    async def fake_mesh(image_url, output_dir, **kw):
        dest = Path(output_dir) / "newmesh.glb"
        dest.write_bytes(b"glTF" + bytes(20))
        return {"ok": True, "model_id": "newmesh", "glb_path": str(dest),
                "textured": True, "provider": "fake"}

    with patch.object(pipeline, "edit_image", fake_edit), \
         patch.object(pipeline, "generate_mesh_glb_from_image", fake_mesh):
        resp = asyncio.run(pipeline.apply_semantic_edit(
            session, s, session.project_id, b"SRC", "give it wings"))

    after = projects.current_version(s, session.project_id)
    t = _add(t, _check("ok", resp.ok is True, resp.error))
    t = _add(t, _check("rebuilt", resp.rebuilt is True))
    t = _add(t, _check("version advanced by one", after.version == before.version + 1))
    t = _add(t, _check('op is "semantic_edit"', after.op == "semantic_edit", after.op))
    t = _add(t, _check("parent points at the edited version",
                       after.parent == before.version, after.parent))
    shutil.rmtree(root, ignore_errors=True)
    return t


def test_semantic_edit_failure_appends_nothing():
    print("\n=== Test: a failed semantic edit leaves the model untouched ===")
    import asyncio
    from unittest.mock import patch
    from app import pipeline
    from mesh.edit import EditError

    t = (0, 0)
    s, root = _tmp_settings()
    session = _session_with_mesh_project(s)
    before = projects.current_version(s, session.project_id)

    async def boom(png, instruction, settings, timeout_s=90.0):
        raise EditError("I couldn't picture that change")

    with patch.object(pipeline, "edit_image", boom):
        resp = asyncio.run(pipeline.apply_semantic_edit(
            session, s, session.project_id, b"SRC", "give it wings"))

    after = projects.current_version(s, session.project_id)
    t = _add(t, _check("ok is False", resp.ok is False))
    t = _add(t, _check('action is "clarify"', resp.action == "clarify", resp.action))
    t = _add(t, _check("no version appended", after.version == before.version))
    t = _add(t, _check("reply is speakable (no path, no exception name)",
                       "/" not in resp.reply and "Error" not in resp.reply, resp.reply))
    shutil.rmtree(root, ignore_errors=True)
    return t
```

Register both in that file's `TESTS` list. If `_session_with_mesh_project` does not already
exist in the file, add it next to `_tmp_settings`:

```python
def _session_with_mesh_project(s):
    """A SessionState pointing at a one-version mesh project."""
    from app.models import SessionState
    src = _glb(s.glb_dir, "seed.glb")
    info = projects.create_project(s, "mesh", src, op="generate",
                                   summary="a blob", mesh_prompt="a blob",
                                   base_size_m=0.2)
    return SessionState(session_id="t", project_id=info.project_id,
                        version=info.version, last_backend="mesh",
                        base_size_m=0.2, glb_url=info.glb_url)
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && ../.venv/bin/python -m app.test_pipeline`
Expected: FAIL — `module 'app.pipeline' has no attribute 'apply_semantic_edit'`

- [x] **Step 3: Write the implementation**

Add the imports near the other mesh imports at the top of `backend/app/pipeline.py`:

```python
from mesh.edit import EditError, edit_image
from mesh.factory import generate_mesh_glb_from_image
```

(Import them at module level, not inside the function — the tests patch
`pipeline.edit_image` and `pipeline.generate_mesh_glb_from_image` by name.)

Then add the function beside `apply_param_update`:

```python
_EDIT_NO_PROJECT = "I can't find that model's history."
_EDIT_NOT_MESH = "I can only do that to a sculpted model."
_EDIT_FAILED = "That change didn't work. The model is unchanged."


async def apply_semantic_edit(
    session: SessionState,
    settings: Settings,
    project_id: str,
    png: bytes,
    instruction: str,
) -> CommandResponse:
    """
    Edit a render of the current sculpt with Gemini, then rebuild a mesh from
    the edited picture. The headset supplies the PNG with the circle already
    drawn, so nothing here needs the camera or the selection.

    Appends exactly one version on success and nothing at all on failure.
    """
    current = projects.current_version(settings, project_id)
    if current is None:
        return CommandResponse(ok=False, reply=_EDIT_NO_PROJECT, action="clarify",
                               session=session, error=f"Unknown project {project_id}")
    if current.kind != "mesh":
        return CommandResponse(ok=False, reply=_EDIT_NOT_MESH, action="clarify",
                               session=session, error="Not a mesh project")

    latency: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        edited = await edit_image(png, instruction, settings)
        latency["image_edit_ms"] = (time.perf_counter() - t0) * 1000
    except EditError as err:
        return CommandResponse(ok=False, reply=str(err) + ".", action="clarify",
                               session=session, error=str(err))

    # Written to ref_dir because the three.ws fallback fetches by public URL;
    # the HF Space path (tried first) reads the local file directly.
    ref_name = f"edit_{uuid.uuid4().hex[:12]}.png"
    ref_path = Path(settings.ref_dir) / ref_name
    try:
        ref_path.write_bytes(edited)
    except Exception as exc:
        logger.exception("Could not stage the edited image: %s", exc)
        return CommandResponse(ok=False, reply=_EDIT_FAILED, action="clarify",
                               session=session, error=str(exc))

    public_url = f"{settings.public_base_url.rstrip('/')}/media/ref/{ref_name}"
    try:
        t0 = time.perf_counter()
        result = await generate_mesh_glb_from_image(
            public_url,
            Path(settings.glb_dir),
            prompt=instruction,
            image_path=ref_path,
            hf_token=settings.hf_token,
        )
        latency["mesh_ms"] = (time.perf_counter() - t0) * 1000
    except Exception as exc:
        logger.exception("Semantic edit mesh build failed: %s", exc)
        return CommandResponse(ok=False, reply=_EDIT_FAILED, action="clarify",
                               session=session, error=str(exc))

    if not result.get("ok") or not result.get("glb_path"):
        return CommandResponse(ok=False, reply=_EDIT_FAILED, action="clarify",
                               session=session, error="Mesh build returned no model")

    info = projects.append_version(
        settings, project_id, Path(result["glb_path"]),
        op="semantic_edit", summary=instruction, mesh_prompt=instruction,
    )
    restore_version(session, info)
    save_session(session)

    return CommandResponse(
        ok=True, reply="Done.", action="semantic_edit", rebuilt=True,
        glb_url=info.glb_url, model_id=session.model_id, session=session,
        latency_ms=latency, textured=True, backend="mesh",
        display_size_m=_display_size_m(session),
    )
```

Do **not** pass `parent=` — `append_version` sets it from the current version itself, and
`_RESERVED` in `app/projects.py:45` strips any `parent` a caller supplies. The test's
`after.parent == before.version` assertion holds because of that derivation, not because we
pass it.

- [x] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m app.test_pipeline`
Expected: all pass, including the two new ones.

- [x] **Step 5: Commit**

```bash
git add backend/app/pipeline.py backend/app/test_pipeline.py
git commit -m "Add semantic_edit orchestration: Gemini edit then image-to-3D"
```

---

### Task 4: Route + job

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/app/test_api.py`

**Interfaces:**
- Consumes: `pipeline.apply_semantic_edit`, `app.jobs.start`
- Produces: `POST /api/projects/{project_id}/semantic_edit`, multipart
  (`image` file, `text` form field, `session_id` form field) → `CommandResponse` with `job_id`

- [x] **Step 1: Write the failing test**

Add to `backend/app/test_api.py`, and add the route to `NEW_ROUTES` in that file:

```python
def test_semantic_edit_route_is_mounted_and_safe():
    print("\n=== Test: semantic_edit route ===")
    from fastapi.testclient import TestClient
    import app.main as main

    t = (0, 0)
    with TestClient(main.app) as client:
        r = client.post(
            "/api/projects/does-not-exist/semantic_edit",
            files={"image": ("v.png", b"notreallyapng", "image/png")},
            data={"text": "give it wings", "session_id": "test_api"},
        )
        t = _add(t, _check("unknown project does not 500",
                           r.status_code == 200 and r.json().get("ok") is False,
                           f"{r.status_code} {r.text[:120]}"))
        r = client.post(
            "/api/projects/p/semantic_edit",
            data={"text": "give it wings", "session_id": "test_api"},
        )
        t = _add(t, _check("a missing image is 422", r.status_code == 422, r.status_code))
    return t
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && ../.venv/bin/python -m app.test_api`
Expected: FAIL — the unknown-project call returns 404/405 because the route does not exist.

- [x] **Step 3: Write the implementation**

Add to `backend/app/main.py`, beside `project_update_params`:

```python
@app.post("/api/projects/{project_id}/semantic_edit", response_model=CommandResponse)
async def project_semantic_edit(
    project_id: str,
    image: UploadFile = File(...),
    text: str = Form(...),
    session_id: str = Form("default"),
):
    """
    Semantic sculpt edit. The headset sends a PNG of what it is looking at with
    a circle drawn where the user pinched; this edits that picture and rebuilds
    a mesh from it. Runs as a job: the whole chain is ~40-70 s.
    """
    session = get_session(session_id)
    try:
        png = await image.read()
    except Exception as exc:
        logger.exception("Could not read the uploaded render: %s", exc)
        return CommandResponse(ok=False, reply="That didn't upload. Try again.",
                               action="clarify", session=session, error=str(exc))

    async def work() -> CommandResponse:
        return await apply_semantic_edit(session, settings, project_id, png, text)

    job_id = jobs.start(work, progress={"stage": "editing"})
    return CommandResponse(
        ok=True, reply="Working on that.", action="semantic_edit",
        session=session, job_id=job_id,
    )
```

Add `apply_semantic_edit` to the `from app.pipeline import (...)` list at the top.

- [x] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m app.test_api`
Expected: all pass, including `no pre-existing route was removed`.

- [x] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/test_api.py
git commit -m "Add POST /api/projects/{id}/semantic_edit as a polled job"
```

---

### Task 5: Router rung

**Files:**
- Modify: `backend/ai/intent.py`
- Test: `backend/ai/test_intent.py`

**Interfaces:**
- Produces: `_check_semantic_edit(text, selection) -> Intent | None` with
  `action="semantic_edit"`, `params={"instruction": <text>}`

**Placement:** on a mesh session, **after** `_check_mesh_boolean` and `_check_absolute_size`
— hole / loop / flat base / resize are deterministic and must keep winning — and **before**
the `clarify_mesh` return, which it now replaces for selection-backed requests.

- [x] **Step 1: Write the failing test**

Add to `backend/ai/test_intent.py`:

```python
def test_semantic_edit_rung():
    print("\n=== Test: semantic edit rung ===")
    from ai.intent import _check_semantic_edit
    from app.models import Selection

    sel = Selection(center=[0.0, 0.0, 0.0], normal=[0.0, 1.0, 0.0])
    passed = failed = 0

    claims = ["give it wings", "add a hat", "make it look angrier", "put horns on it",
              "give it red wings"]   # a colour inside a real addition still counts
    for text in claims:
        got = _check_semantic_edit(text, sel)
        if got is not None and got.action == "semantic_edit":
            print(f"  [ok] claims {text!r}"); passed += 1
        else:
            print(f"  [FAIL] should claim {text!r}, got {got}"); failed += 1

    # A selection is required.
    if _check_semantic_edit("give it wings", None) is None:
        print("  [ok] no selection -> no semantic edit"); passed += 1
    else:
        print("  [FAIL] claimed without a selection"); failed += 1

    # Deterministic / fast paths keep their existing behaviour. The colour
    # cases matter most: "make" is a semantic verb, so without _RECOLOR_ONLY_RE
    # a recolour would cost a 40-70s regeneration.
    for text in ["drill a hole", "add a loop", "flatten the base",
                 "make it bigger", "make it 8 cm tall", "paint it red",
                 "make it red", "make it blue", "turn it green",
                 "colour it black", "make the ears red"]:
        if _check_semantic_edit(text, sel) is None:
            print(f"  [ok] leaves {text!r} alone"); passed += 1
        else:
            print(f"  [FAIL] hijacked {text!r}"); failed += 1

    return passed, failed
```

Register it in that file's `TESTS` list.

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && ../.venv/bin/python -m ai.test_intent`
Expected: FAIL — `cannot import name '_check_semantic_edit'`

- [x] **Step 3: Write the implementation**

Add to `backend/ai/intent.py`, below `_check_mesh_boolean`:

```python
# Anything a sculpt cannot do deterministically but an image edit can:
# "give it wings", "add a hat". Needs a selection, because the circle on the
# render is what tells the image model where to make the change.
_SEMANTIC_VERBS = re.compile(
    r"\b(?:give|add|put|make|turn|stick)\b", re.I
)
# Handled elsewhere, deterministically — never claim these.
_NOT_SEMANTIC = re.compile(
    r"\bholes?\b|\bloops?\b|\bhanger\b|\bflat(?:ten)?\s+(?:it\s+|the\s+)?(?:base|bottom)\b"
    r"|\b(?:bigger|smaller|larger|taller|wider|thicker|thinner)\b"
    r"|\bpaint\b|\bsmooth\b|\bpull\b|\bpush\b"
    r"|\b\d+(?:\.\d+)?\s*(?:mm|cm|m|inch(?:es)?|millimet|centimet|met)",
    re.I,
)
# A pure recolour is NOT a semantic edit. "make" is in _SEMANTIC_VERBS, so
# without this "make it red" would cost a 40-70s regeneration instead of the
# instant recolour. "give it red wings" is still a semantic edit, because the
# colour there is not the whole request.
_RECOLOR_ONLY_RE = re.compile(
    r"^(?:make|turn|paint|colou?r)\s+(?:it|this|that|the\s+\w+)\s+"
    r"(?:" + "|".join(re.escape(c) for c in COLOR_MAP) + r")\b\s*[.!?]*$",
    re.I,
)


def _check_semantic_edit(text: str, selection: Selection | None) -> Intent | None:
    """"Give it wings" on a sculpt, with a spot pointed at → an image edit."""
    t = text.lower().strip()
    if selection is None or not getattr(selection, "center", None):
        return None
    if _is_new_object_request(t) or _NOT_SEMANTIC.search(t):
        return None
    if _RECOLOR_ONLY_RE.match(t):
        return None
    if not _SEMANTIC_VERBS.search(t):
        return None
    return Intent(
        action="semantic_edit",
        backend="mesh",
        params={"instruction": text.strip()},
        reply="Working on that.",
    )
```

Then in `parse_intent`, inside the `session_backend == "mesh" and not is_new` block, add it
immediately **after** the `_check_absolute_size` block and **before** `_check_scale_only`:

```python
        semantic_intent = _check_semantic_edit(cleaned, selection)
        if semantic_intent:
            logger.info("Fast path: semantic edit")
            return semantic_intent, (time.perf_counter() - t0) * 1000
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m ai.test_intent`
Expected: all pass. **In particular the existing mesh-boolean and absolute-size tests must
still pass** — that is the regression this ordering protects.

- [x] **Step 5: Commit**

```bash
git add backend/ai/intent.py backend/ai/test_intent.py
git commit -m "Route selection-backed sculpt edits to semantic_edit"
```

---

### Task 6: Client wiring

**Files:**
- Modify: `web-client/src/main.js`, `web-client/src/voice/PercyAssistant.js`

**Interfaces:**
- Consumes: `viewCapture.js` (Task 2), `POST .../semantic_edit` (Task 4)

There is no unit test for this task — it is Three.js and WebXR wiring, which this repo tests
by hand. Keep the logic in `viewCapture.js` (already tested) and keep `main.js` thin.

- [x] **Step 1: Add the capture helper to `main.js`**

```js
/**
 * A PNG of what the user is looking at, with a circle drawn where they
 * pinched. The circle is what tells the image model where to make the change
 * (see docs/superpowers/specs/2026-09-19-semantic-mesh-edits-design.md).
 */
async function captureViewWithCircle(selection) {
  const W = 1024, H = 1024;
  const rt = new THREE.WebGLRenderTarget(W, H);
  const prevTarget = renderer.getRenderTarget();
  renderer.setRenderTarget(rt);
  renderer.render(scene, camera);
  const buf = new Uint8Array(W * H * 4);
  renderer.readRenderTargetPixels(rt, 0, 0, W, H, buf);
  renderer.setRenderTarget(prevTarget);
  rt.dispose();

  // readRenderTargetPixels is bottom-up; canvas is top-down.
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(W, H);
  for (let y = 0; y < H; y++) {
    const src = (H - 1 - y) * W * 4;
    img.data.set(buf.subarray(src, src + W * 4), y * W * 4);
  }
  ctx.putImageData(img, 0, 0);

  const world = currentModel.localToWorld(
    new THREE.Vector3(selection.center[0], selection.center[1], selection.center[2])
  );
  const ndc = world.project(camera);
  const px = ndcToPixels(ndc, W, H);
  const c = clampCircle(px.x, px.y, circleRadiusPx(W, H), W, H);

  ctx.strokeStyle = "#ff0000";
  ctx.lineWidth = Math.max(4, c.r * 0.06);
  ctx.beginPath();
  ctx.arc(c.cx, c.cy, c.r, 0, Math.PI * 2);
  ctx.stroke();

  return new Promise((res) => canvas.toBlob(res, "image/png"));
}
```

Add the import beside the other interaction imports:

```js
import { ndcToPixels, circleRadiusPx, clampCircle } from "./interaction/viewCapture.js";
```

- [x] **Step 2: Add the POST to `PercyAssistant.js`**

Beside `postParamUpdate`:

```js
  /**
   * Semantic sculpt edit: upload a render with the circle drawn on it and
   * poll the job. The whole chain is ~40-70 s.
   */
  async postSemanticEdit(projectId, blob, text) {
    const form = new FormData();
    form.append("image", blob, "view.png");
    form.append("text", text);
    form.append("session_id", SESSION_ID);
    const res = await fetch(`${API_BASE}/api/projects/${projectId}/semantic_edit`, {
      method: "POST", body: form,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const started = await res.json();
    if (started.job_id) return this._pollJob(started.job_id);
    return started;
  }
```

Reuse whatever the Composio pull path already uses to poll `job_id`; if that helper has a
different name than `_pollJob`, use that name here rather than adding a second poller.

- [x] **Step 3: Handle `action === "semantic_edit"` in the command response**

Where `main.js` handles `data.action === "ui_mode"` (around `main.js:630`), add a branch that
calls `captureViewWithCircle(currentSelection)` and then
`percy.postSemanticEdit(lastProjectId, blob, data.reply_instruction || transcript)`. The
returned response goes through the normal model-swap path, exactly like any other
`rebuilt=true` response.

- [x] **Step 4: Verify the client builds**

Run: `cd web-client && npm run build`
Expected: `✓ built in …`, no errors.

- [x] **Step 5: Commit**

```bash
git add web-client/src/main.js web-client/src/voice/PercyAssistant.js
git commit -m "Wire the headset capture and semantic-edit upload"
```

---

### Task 7: Full-suite gate and docs

- [x] **Step 1: Run everything**

Run: `./run_tests.sh`
Expected: `✓ ALL TEST SUITES PASSED`, with `mesh.test_edit` in the list.

- [x] **Step 2: Build the client**

Run: `cd web-client && npm run build`
Expected: no errors.

- [x] **Step 3: Update the invariants note**

`ai-docs/09-invariants.md` (git-excluded, main checkout only): `clarify_mesh` narrows again —
semantic edits on a sculpt with a selection are now supported, alongside the Phase 4
booleans.

- [x] **Step 4: Commit**

```bash
git add -A
git commit -m "Register the semantic-edit suite and narrow clarify_mesh"
```

---

## Manual verification (cannot be unit-tested)

Nothing above proves the feature works in a headset. Before demoing:

1. Build a sculpt from a photo.
2. Pinch a spot on it. Confirm the highlight lands where you pinched.
3. Say "give it wings."
4. Confirm the HUD appears, and the edited picture shows at ~7.5 s.
5. Confirm a new sculpt arrives at ~40–70 s with wings roughly where you pinched.
6. Say "undo." Confirm it returns to the **exact** pre-edit sculpt — this is the guard
   against identity drift being destructive.
7. Force a failure (turn off Wi-Fi mid-edit). Confirm the spoken reply is graceful and the
   model is unchanged.
