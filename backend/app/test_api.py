#!/usr/bin/env python3
"""
HTTP-surface regression tests.

The in-headset-editing branch added a lifespan handler, four project routes, a
`selection` field on /api/command and three new CommandResponse fields. Nothing
exercised app.main end to end, so this suite pins the things that are easy to
break from the outside:

  - the app starts and stops cleanly, and shutdown still closes the shared
    httpx client (passing `lifespan=` to FastAPI makes Starlette ignore
    @app.on_event, which silently killed that close once already)
  - every route that existed before the branch is still mounted, same methods
  - the new project routes refuse ids that could escape projects_dir
  - an old client that sends no `selection` and reads only the old response
    fields still works

Run with: python -m app.test_api
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import warnings
from unittest.mock import patch

warnings.filterwarnings("ignore")


def _check(name, cond, detail=""):
    if cond:
        print(f"  [ok] {name}")
        return 1, 0
    print(f"  [FAIL] {name} {detail}")
    return 0, 1


def _add(total, result):
    return total[0] + result[0], total[1] + result[1]


# ============================================================================
# Lifespan: startup/shutdown
# ============================================================================

def test_app_starts_and_health_responds():
    print("\n=== Test: app starts, /api/health responds ===")
    from fastapi.testclient import TestClient

    import app.main as main

    t = (0, 0)
    with TestClient(main.app) as client:
        r = client.get("/api/health")
        t = _add(t, _check("health 200", r.status_code == 200, r.status_code))
        t = _add(t, _check("health body is json", isinstance(r.json(), dict)))
    return t


def test_shutdown_closes_http_client():
    """
    Regression: the shared httpx client used to be closed from
    @app.on_event("shutdown"). Adding `lifespan=` to the FastAPI constructor
    makes Starlette drop on_event handlers entirely, so the close has to live
    in the lifespan's finally block. Without it the client leaks every restart.
    """
    print("\n=== Test: shutdown closes the shared http client ===")
    from fastapi.testclient import TestClient

    import app.httpclient as httpclient
    import app.main as main

    t = (0, 0)
    closed = {"n": 0}

    async def fake_close():
        closed["n"] += 1

    # Patch the name main.py actually calls, so this fails if the call is dropped.
    with patch.object(main, "aclose_http_client", fake_close):
        with TestClient(main.app):
            t = _add(t, _check("not closed while running", closed["n"] == 0, closed["n"]))
    t = _add(t, _check("closed exactly once on shutdown", closed["n"] == 1, closed["n"]))

    # And the real implementation must actually drop the cached client.
    async def roundtrip():
        c = httpclient.get_http_client()
        await httpclient.aclose_http_client()
        return c

    client = asyncio.run(roundtrip())
    t = _add(t, _check("real close clears the cached client", httpclient._client is None))
    t = _add(t, _check("real close closed the httpx client", client.is_closed))
    return t


def test_cleanup_task_is_cancelled_on_shutdown():
    print("\n=== Test: the hourly cleanup task stops on shutdown ===")
    import time

    from fastapi.testclient import TestClient

    import app.main as main

    t = (0, 0)
    seen = {"calls": 0}

    def fake_cleanup(_settings):
        seen["calls"] += 1
        return {"audio": 0, "ref": 0, "projects": 0}

    with patch.object(main.projects, "cleanup", fake_cleanup):
        with TestClient(main.app):
            deadline = time.time() + 2.0
            while seen["calls"] == 0 and time.time() < deadline:
                time.sleep(0.02)
    t = _add(t, _check("cleanup ran at startup", seen["calls"] >= 1, seen["calls"]))
    # Reaching this line at all proves shutdown did not block on the loop's
    # hour-long sleep: TestClient.__exit__ waits for the lifespan to finish.
    t = _add(t, _check("shutdown did not hang on the sleeping loop", True))
    t = _add(t, _check("hourly interval unchanged", main.CLEANUP_INTERVAL_S == 3600))
    return t


# ============================================================================
# Route surface: nothing that existed before the branch may disappear
# ============================================================================

# Captured from origin/main before this branch merged. A route leaving this
# list is a breaking change for the deployed headset client.
PRE_EXISTING_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/session/{session_id}"),
    ("POST", "/api/session/{session_id}/reset"),
    ("POST", "/api/command"),
    ("POST", "/api/script"),
    ("POST", "/api/image"),
    ("GET", "/api/photos/{file_id}/preview"),
    ("POST", "/api/photos/confirm"),
    ("POST", "/api/photos/choose"),
    ("GET", "/api/jobs/{job_id}"),
    ("POST", "/api/voice"),
}

NEW_ROUTES = {
    ("GET", "/api/projects/{project_id}"),
    ("POST", "/api/projects/{project_id}/undo"),
    ("POST", "/api/projects/{project_id}/redo"),
    ("POST", "/api/projects/{project_id}/versions"),
    ("POST", "/api/projects/{project_id}/params"),
    ("POST", "/api/projects/{project_id}/resize"),
    ("POST", "/api/projects/{project_id}/semantic_edit"),
}


def _route_pairs(app):
    pairs = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            pairs.add((m, path))
    return pairs


def test_pre_existing_routes_survive():
    print("\n=== Test: pre-branch routes are all still mounted ===")
    import app.main as main

    live = _route_pairs(main.app)
    t = (0, 0)
    missing = sorted(PRE_EXISTING_ROUTES - live)
    t = _add(t, _check("no pre-existing route was removed", not missing, missing))
    absent_new = sorted(NEW_ROUTES - live)
    t = _add(t, _check("the new project routes are mounted", not absent_new, absent_new))
    return t


def test_media_mounts():
    print("\n=== Test: /media mounts ===")
    import app.main as main

    mounts = {getattr(r, "path", "") for r in main.app.routes}
    t = (0, 0)
    for m in ("/media/glb", "/media/audio", "/media/ref", "/media/projects"):
        t = _add(t, _check(f"{m} mounted", m in mounts))
    from app.config import get_settings

    s = get_settings()
    t = _add(t, _check("projects_dir exists on disk", Path(s.projects_dir).is_dir()))
    return t


# ============================================================================
# New project routes: bad input must not 500 or escape projects_dir
# ============================================================================

BAD_IDS = [
    "..",
    "does-not-exist",
    "a" * 100,          # over the 64-char id limit
    "has space",
    "semi;colon",
]


def test_project_routes_reject_bad_ids():
    print("\n=== Test: project routes refuse unknown / unsafe ids ===")
    from fastapi.testclient import TestClient

    import app.main as main

    t = (0, 0)
    with TestClient(main.app) as client:
        for pid in BAD_IDS:
            r = client.get(f"/api/projects/{pid}")
            ok = r.status_code in (200, 404) and (
                r.status_code == 404 or r.json().get("ok") is False
            )
            t = _add(t, _check(f"GET info {pid!r} refused cleanly", ok, r.status_code))

            for route, body in (
                ("undo", {"session_id": "test_api", "steps": 1}),
                ("redo", {"session_id": "test_api", "steps": 1}),
                ("resize", {"session_id": "test_api", "factor": 2.0}),
                ("params", {"session_id": "test_api", "updates": {"a_mm": 5.0}}),
            ):
                r = client.post(f"/api/projects/{pid}/{route}", json=body)
                # 404/405 mean the URL never resolved to a handler at all
                # (".." is normalized away by the client before routing) —
                # equally safe. What must never happen is a 500.
                if r.status_code in (404, 405):
                    t = _add(t, _check(f"POST {route} {pid!r} never routed ({r.status_code})", True))
                    continue
                body_json = r.json()
                ok = r.status_code == 200 and body_json.get("ok") is False
                t = _add(
                    t,
                    _check(
                        f"POST {route} {pid!r} → ok=false, no 500",
                        ok,
                        f"{r.status_code} {body_json}",
                    ),
                )
    return t


def test_project_id_validation_is_the_gate():
    print("\n=== Test: _project_dir is the traversal gate ===")
    from types import SimpleNamespace

    from app import projects

    s = SimpleNamespace(projects_dir=Path("/tmp/does-not-matter"))
    t = (0, 0)
    for bad in ("..", "../..", "a/b", "a\\b", "", "x" * 65, "has space", None, 5):
        t = _add(t, _check(f"rejects {bad!r}", projects._project_dir(s, bad) is None))
    for good in ("abc123", "a-b_c", "x" * 64):
        t = _add(t, _check(f"accepts {good!r}", projects._project_dir(s, good) is not None))
    return t


def test_resize_and_params_validate_bodies():
    print("\n=== Test: malformed bodies are 422, not 500 ===")
    from fastapi.testclient import TestClient

    import app.main as main

    t = (0, 0)
    with TestClient(main.app) as client:
        cases = [
            ("/api/projects/p/resize", {"session_id": "t"}),               # factor missing
            ("/api/projects/p/resize", {"factor": "huge"}),                 # factor not a number
            ("/api/projects/p/params", {"session_id": "t"}),                # updates missing
            ("/api/projects/p/params", {"updates": {"a_mm": "wide"}}),      # value not a number
        ]
        for url, body in cases:
            r = client.post(url, json=body)
            t = _add(
                t,
                _check(f"{url} {body} → 422", r.status_code == 422, r.status_code),
            )
        # steps is optional and defaults to 1
        r = client.post("/api/projects/p/undo", json={})
        t = _add(t, _check("undo with an empty body is accepted", r.status_code == 200, r.status_code))
    return t


# ============================================================================
# Back-compat for the deployed client
# ============================================================================

def test_command_selection_is_optional_and_forgiving():
    print("\n=== Test: /api/command back-compat around `selection` ===")
    from app.models import CommandRequest, Selection

    t = (0, 0)
    # An old client sends no selection at all.
    req = CommandRequest(text="make a cube", session_id="t")
    t = _add(t, _check("selection defaults to None", req.selection is None))

    # A new client sends one.
    req = CommandRequest(
        text="drill a hole",
        session_id="t",
        selection={"center": [0, 0, 0], "normal": [0, 1, 0]},
    )
    t = _add(t, _check("selection parses", isinstance(req.selection, Selection)))
    t = _add(t, _check("parts defaults to []", req.selection.parts == []))
    t = _add(t, _check("radius defaults to 0", req.selection.radius == 0.0))

    # The voice route parses selection from a form field; it must never raise.
    import app.main as main

    for raw in (None, "", "not json", "{}", '{"center": "nope"}', "[]", '{"center":[0,0,0],"normal":[0,1,0]}'):
        try:
            got = main._parse_selection(raw)
            ok = True
        except Exception as exc:  # noqa: BLE001
            got, ok = None, False
        t = _add(t, _check(f"_parse_selection({raw!r}) does not raise", ok))
    good = main._parse_selection('{"center":[0,0,0],"normal":[0,1,0]}')
    t = _add(t, _check("_parse_selection returns a Selection for valid json", good is not None))
    t = _add(t, _check("_parse_selection returns None for junk", main._parse_selection("not json") is None))
    return t


def test_command_response_stays_backward_compatible():
    print("\n=== Test: CommandResponse new fields are optional ===")
    from app.models import CommandResponse

    from app.models import SessionState

    t = (0, 0)
    # Every field the pre-branch client read must still be present, and the
    # new fields must have safe defaults so an old client parsing this JSON
    # sees exactly what it saw before.
    r = CommandResponse(reply="hi", action="clarify", session=SessionState())
    for field in ("ok", "reply", "action", "rebuilt", "glb_url", "model_id", "latency_ms"):
        t = _add(t, _check(f"{field} still present", hasattr(r, field)))
    t = _add(t, _check("cad_params defaults to {}", r.cad_params == {}))
    t = _add(t, _check("ui_mode defaults to None", r.ui_mode is None))

    data = r.model_dump()
    t = _add(t, _check("serializes without the optional fields set", "ui_mode" in data))

    s = SessionState()
    t = _add(t, _check("session.project_id defaults to None", s.project_id is None))
    t = _add(t, _check("session.version defaults to None", s.version is None))
    return t


# ============================================================================
# Router precedence: the new fast paths must not swallow real build requests
# ============================================================================

def test_new_fast_paths_do_not_hijack_build_requests():
    """
    _check_history and _check_ui_mode run above every other rung in
    parse_intent, before the network is ever touched. If their regexes are
    loose they silently eat ordinary utterances, which is the worst kind of
    regression: no error, just the wrong thing happening.
    """
    print("\n=== Test: history / ui_mode fast paths are whole-utterance only ===")
    from ai.intent import _check_history, _check_ui_mode

    t = (0, 0)

    # These must be claimed by the fast paths.
    for text, action in [
        ("undo", "undo"),
        ("Undo.", "undo"),
        ("hey percy, undo that", "undo"),
        ("redo", "redo"),
        ("go back two steps", "undo"),
        ("go forward", "redo"),
    ]:
        got = _check_history(text)
        t = _add(t, _check(f"history claims {text!r}", got is not None and got.action == action, got))

    # These must fall through to the real router.
    for text in [
        "undo the ears and make them longer",
        "go back to the round one",
        "make a model of an undo button",
        "redo the wheels in black",
        "step back the bumper by 5 mm",
    ]:
        t = _add(t, _check(f"history ignores {text!r}", _check_history(text) is None, _check_history(text)))

    for text, mode in [
        ("select mode", "lasso"),
        ("tape measure", "tape"),
        ("done", "none"),
        ("cancel", "none"),
        ("hey percy, clear selection", "none"),
    ]:
        got = _check_ui_mode(text)
        t = _add(
            t,
            _check(f"ui_mode claims {text!r}", got is not None and got.params["mode"] == mode, got),
        )

    for text in [
        "make me a tape measure",
        "are you done",
        "cancel the hole and make it bigger",
        "build a select mode button",
        "measure mode is what I want on the handle",
    ]:
        t = _add(t, _check(f"ui_mode ignores {text!r}", _check_ui_mode(text) is None, _check_ui_mode(text)))
    return t


def test_history_step_count_is_bounded():
    print("\n=== Test: undo step counts are clamped ===")
    from ai.intent import _check_history

    t = (0, 0)
    got = _check_history("undo 500 steps")
    t = _add(t, _check("500 clamps to 20", got is not None and got.params["steps"] == 20, got))
    got = _check_history("undo three times")
    t = _add(t, _check("word numbers parse", got is not None and got.params["steps"] == 3, got))
    got = _check_history("undo")
    t = _add(t, _check("bare undo is 1 step", got is not None and got.params["steps"] == 1, got))
    return t


def test_semantic_edit_route():
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
        # It starts a job, so the immediate answer is ok with a job_id; the
        # unknown project surfaces in the job result, not here.
        body = r.json() if r.status_code == 200 else {}
        t = _add(t, _check("accepted and started a job",
                           r.status_code == 200 and bool(body.get("job_id")),
                           f"{r.status_code} {str(body)[:120]}"))
        t = _add(t, _check("action is semantic_edit",
                           body.get("action") == "semantic_edit", body.get("action")))

        r = client.post(
            "/api/projects/p/semantic_edit",
            data={"text": "give it wings", "session_id": "test_api"},
        )
        t = _add(t, _check("a missing image is 422", r.status_code == 422, r.status_code))

        r = client.post(
            "/api/projects/p/semantic_edit",
            files={"image": ("v.png", b"x", "image/png")},
            data={"session_id": "test_api"},
        )
        t = _add(t, _check("missing text is 422", r.status_code == 422, r.status_code))
    return t


def run_all_tests():
    print("=" * 60)
    print("HTTP API REGRESSION TESTS")
    print("=" * 60)
    total = (0, 0)
    for fn in TESTS:
        try:
            total = _add(total, fn())
        except Exception as exc:  # a crash is a failure, not an abort
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
    test_app_starts_and_health_responds,
    test_shutdown_closes_http_client,
    test_cleanup_task_is_cancelled_on_shutdown,
    test_pre_existing_routes_survive,
    test_media_mounts,
    test_project_routes_reject_bad_ids,
    test_project_id_validation_is_the_gate,
    test_resize_and_params_validate_bodies,
    test_command_selection_is_optional_and_forgiving,
    test_command_response_stays_backward_compatible,
    test_new_fast_paths_do_not_hijack_build_requests,
    test_history_step_count_is_bounded,
    test_semantic_edit_route,
]


if __name__ == "__main__":
    sys.exit(run_all_tests())
