#!/usr/bin/env python3
"""
Tests for per-object projects: versions, undo/redo, pruning, cleanup, routes.

Run with: python -m app.test_projects
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import shutil
import tempfile
from types import SimpleNamespace


def _check(name, cond, detail=""):
    if cond:
        print(f"  [ok] {name}")
        return 1, 0
    print(f"  [FAIL] {name} {detail}")
    return 0, 1


def _add(total, result):
    return total[0] + result[0], total[1] + result[1]


def _tmp_settings():
    root = Path(tempfile.mkdtemp(prefix="ws_a_"))
    s = SimpleNamespace(
        projects_dir=root / "projects",
        audio_dir=root / "audio",
        ref_dir=root / "ref",
        glb_dir=root / "glb",
    )
    for d in (s.projects_dir, s.audio_dir, s.ref_dir, s.glb_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s, root


def _glb(directory: Path, name: str) -> Path:
    path = Path(directory) / name
    path.write_bytes(b"glTF" + bytes(20))
    return path


# ============================================================================
# Task 1: models and settings
# ============================================================================

def test_models():
    print("\n=== Test: models and settings ===")
    from app.config import get_settings
    from app.models import HistoryRequest, SessionState, VersionInfo

    t = (0, 0)
    v = VersionInfo(
        project_id="p", version=1, kind="cad", op="generate", glb_url="/x", created_at=1.0
    )
    t = _add(t, _check("VersionInfo params default {}", v.params == {}))
    s = SessionState()
    t = _add(t, _check("SessionState project pointer defaults", s.project_id is None and s.version is None))
    t = _add(t, _check("HistoryRequest steps default 1", HistoryRequest().steps == 1))
    t = _add(t, _check("projects_dir exists", get_settings().projects_dir.is_dir()))
    return t


# ============================================================================
# Task 2: create / append / get / current / prune
# ============================================================================

def test_create_and_append():
    print("\n=== Test: create_project / append_version ===")
    import json
    from app import projects

    s, root = _tmp_settings()
    t = (0, 0)
    try:
        src = _glb(s.glb_dir, "a.glb")
        v1 = projects.create_project(s, "cad", src, op="generate", script="S1", summary="a box")
        pid = v1.project_id
        pdir = s.projects_dir / pid
        t = _add(t, _check("v1 numbered 1", v1.version == 1 and v1.parent is None))
        t = _add(t, _check("glb moved", not src.exists() and (pdir / "v1.glb").is_file()))
        t = _add(t, _check("glb_url", v1.glb_url == f"/media/projects/{pid}/v1.glb", v1.glb_url))
        t = _add(t, _check("kind/op stored", v1.kind == "cad" and v1.op == "generate"))
        cur = projects.current_version(s, pid)
        t = _add(t, _check("current is v1", cur is not None and cur.version == 1))
        info = json.loads((pdir / "info.json").read_text())
        t = _add(t, _check(
            "info.json shape",
            info["current"] == 1 and info["saved_to_drive"] is False
            and "touched_at" in info and len(info["versions"]) == 1,
            info,
        ))

        v2 = projects.append_version(s, pid, _glb(s.glb_dir, "b.glb"), op="set_material", color="#FF0000")
        t = _add(t, _check("v2 parent 1", v2.version == 2 and v2.parent == 1))
        t = _add(t, _check(
            "v2 inherits unspecified meta",
            v2.script == "S1" and v2.kind == "cad" and v2.summary == "a box",
            v2,
        ))
        t = _add(t, _check("v2 keeps explicit meta", v2.color == "#FF0000" and v2.op == "set_material"))
        t = _add(t, _check("current is v2", projects.current_version(s, pid).version == 2))
        got = projects.get_version(s, pid, 1)
        t = _add(t, _check("get_version v1", got is not None and got.script == "S1"))
        t = _add(t, _check("get_version missing", projects.get_version(s, pid, 9) is None))

        v3 = projects.append_version(s, pid, _glb(s.glb_dir, "c.glb"), op="hand_edit", script=None)
        t = _add(t, _check("explicit None respected", v3.script is None, v3.script))

        try:
            projects.append_version(s, "nope", _glb(s.glb_dir, "d.glb"), op="hand_edit")
            t = _add(t, _check("unknown project raises", False))
        except ValueError:
            t = _add(t, _check("unknown project raises", True))
        t = _add(t, _check("unknown current is None", projects.current_version(s, "nope") is None))
        t = _add(t, _check("path-like pid rejected", projects.current_version(s, "../x") is None))
        t = _add(t, _check("get_project json", projects.get_project(s, pid)["current"] == 3))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return t


def test_prune():
    print("\n=== Test: prune keeps v1 + newest ===")
    from app import projects

    s, root = _tmp_settings()
    t = (0, 0)
    try:
        pid = projects.create_project(s, "mesh", _glb(s.glb_dir, "a.glb")).project_id
        for i in range(24):
            projects.append_version(s, pid, _glb(s.glb_dir, f"x{i}.glb"), op="hand_edit")
        info = projects.load_info(s, pid)
        nums = [v.version for v in info["versions"]]
        pdir = s.projects_dir / pid
        t = _add(t, _check("capped at MAX_VERSIONS", len(nums) == projects.MAX_VERSIONS, nums))
        t = _add(t, _check("v1 kept, newest last", nums[0] == 1 and nums[-1] == 25, nums))
        t = _add(t, _check("pruned file deleted", not (pdir / "v2.glb").exists()))
        t = _add(t, _check("kept files present", (pdir / "v1.glb").exists() and (pdir / "v25.glb").exists()))
        t = _add(t, _check(
            "one glb per kept version",
            len(list(pdir.glob("v*.glb"))) == projects.MAX_VERSIONS,
        ))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return t


# ============================================================================
# Task 3: undo / redo / saved_to_drive
# ============================================================================

def test_undo_redo():
    print("\n=== Test: undo / redo ===")
    from app import projects

    s, root = _tmp_settings()
    t = (0, 0)
    try:
        pid = projects.create_project(s, "cad", _glb(s.glb_dir, "a.glb"), script="S1").project_id
        projects.append_version(s, pid, _glb(s.glb_dir, "b.glb"), script="S2")
        projects.append_version(s, pid, _glb(s.glb_dir, "c.glb"), script="S3")

        v = projects.undo(s, pid)
        t = _add(t, _check("undo -> v2", v is not None and v.version == 2 and v.script == "S2"))
        t = _add(t, _check("current follows undo", projects.current_version(s, pid).version == 2))
        v = projects.undo(s, pid, steps=5)
        t = _add(t, _check("undo clamps to v1", v is not None and v.version == 1))
        t = _add(t, _check("undo at v1 -> None", projects.undo(s, pid) is None))
        v = projects.redo(s, pid, steps=2)
        t = _add(t, _check("redo 2 -> v3", v is not None and v.version == 3))
        t = _add(t, _check("redo at end -> None", projects.redo(s, pid) is None))

        projects.undo(s, pid)
        v4 = projects.append_version(s, pid, _glb(s.glb_dir, "d.glb"), op="hand_edit")
        nums = [x.version for x in projects.load_info(s, pid)["versions"]]
        t = _add(t, _check("number never reused", v4.version == 4 and v4.parent == 2, v4.version))
        t = _add(t, _check("redo tail dropped", nums == [1, 2, 4], nums))
        t = _add(t, _check("dropped file deleted", not (s.projects_dir / pid / "v3.glb").exists()))
        t = _add(t, _check("no redo after new edit", projects.redo(s, pid) is None))

        t = _add(t, _check("unknown undo -> None", projects.undo(s, "nope") is None))
        projects.mark_saved_to_drive(s, pid)
        t = _add(t, _check("saved_to_drive set", projects.load_info(s, pid)["saved_to_drive"] is True))
        projects.mark_saved_to_drive(s, "nope")  # must not raise
        t = _add(t, _check("mark unknown is a no-op", True))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return t


# ============================================================================
# Task 4: cleanup
# ============================================================================

def test_cleanup():
    print("\n=== Test: cleanup TTLs ===")
    import os
    import time
    from app import projects

    s, root = _tmp_settings()
    t = (0, 0)
    try:
        now = time.time()

        def aged(path, age_s):
            path.write_bytes(b"x")
            os.utime(path, (now - age_s, now - age_s))
            return path

        old_audio = aged(s.audio_dir / "old.mp3", 2 * 3600)
        new_audio = aged(s.audio_dir / "new.mp3", 600)
        keep_dot = aged(s.audio_dir / ".gitkeep", 30 * 86400)
        old_ref = aged(s.ref_dir / "old.png", 25 * 3600)
        new_ref = aged(s.ref_dir / "new.png", 3600)

        a = projects.create_project(s, "mesh", _glb(s.glb_dir, "a.glb")).project_id
        b = projects.create_project(s, "mesh", _glb(s.glb_dir, "b.glb")).project_id
        projects.mark_saved_to_drive(s, b)

        fresh = projects.cleanup(s, now=time.time())
        t = _add(t, _check("fresh projects kept", fresh["projects"] == 0, fresh))

        counts = projects.cleanup(s, now=time.time() + 8 * 86400)
        # Everything in audio/ref is "old" relative to +8 days, so re-check
        # the per-TTL behaviour with the real clock instead.
        t = _add(t, _check("old project removed", not (s.projects_dir / a).exists()))
        t = _add(t, _check("saved_to_drive project kept", (s.projects_dir / b).exists()))
        t = _add(t, _check("project count", counts["projects"] == 1, counts))
        t = _add(t, _check("dot-files kept", keep_dot.exists()))

        # TTLs for audio / ref at the real clock.
        s2, root2 = _tmp_settings()
        try:
            o_a = aged(s2.audio_dir / "old.mp3", 2 * 3600)
            n_a = aged(s2.audio_dir / "new.mp3", 600)
            o_r = aged(s2.ref_dir / "old.png", 25 * 3600)
            n_r = aged(s2.ref_dir / "new.png", 3600)
            c2 = projects.cleanup(s2, now=now)
            t = _add(t, _check("audio > 1 h deleted", not o_a.exists() and n_a.exists()))
            t = _add(t, _check("ref > 24 h deleted", not o_r.exists() and n_r.exists()))
            t = _add(t, _check(
                "counts per kind",
                c2 == {"audio": 1, "ref": 1, "projects": 0},
                c2,
            ))
        finally:
            shutil.rmtree(root2, ignore_errors=True)

        missing = SimpleNamespace(
            projects_dir=root / "nope1", audio_dir=root / "nope2", ref_dir=root / "nope3"
        )
        t = _add(t, _check("missing dirs do not raise", projects.cleanup(missing)["audio"] == 0))
        del old_audio, new_audio, old_ref, new_ref
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return t


# ============================================================================
# Task 8: routes, media mount, cleanup task
# ============================================================================

def test_routes():
    print("\n=== Test: project routes ===")
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    import app.main as main
    import app.session as session_mod
    from app import projects

    s, root = _tmp_settings()
    t = (0, 0)
    real_projects_dir = main.settings.projects_dir
    try:
        main.settings.projects_dir = s.projects_dir
        with patch.object(session_mod, "SESSION_FILE", root / "sessions.json"), \
             patch("app.pipeline.synthesize_speech", AsyncMock(return_value=(None, 0.0))):
            client = TestClient(main.app)  # no `with`: lifespan (cleanup) not started
            pid = projects.create_project(s, "mesh", _glb(s.glb_dir, "a.glb"), mesh_prompt="a corgi").project_id

            r = client.get(f"/api/projects/{pid}").json()
            t = _add(t, _check("GET project", r.get("ok") is True and len(r.get("versions", [])) == 1, r))

            r = client.post(
                f"/api/projects/{pid}/versions",
                files={"glb": ("edit.glb", b"glTF" + bytes(40), "model/gltf-binary")},
                data={"op": "hand_edit", "summary": "Pulled the ear.", "session_id": "ws_a_route"},
            ).json()
            t = _add(t, _check(
                "POST versions -> version_saved",
                r.get("action") == "version_saved" and r.get("rebuilt") is False
                and r.get("model_id") == f"{pid}-v2",
                r,
            ))

            r = client.post(f"/api/projects/{pid}/undo", json={"steps": 1, "session_id": "ws_a_route"}).json()
            t = _add(t, _check(
                "POST undo swaps to v1",
                r.get("rebuilt") is True and r.get("glb_url") == f"/media/projects/{pid}/v1.glb"
                and r.get("model_id") == f"{pid}-v1",
                r,
            ))
            r = client.post(f"/api/projects/{pid}/redo", json={"session_id": "ws_a_route"}).json()
            t = _add(t, _check("POST redo -> v2", r.get("model_id") == f"{pid}-v2", r))

            r = client.get("/api/projects/nope").json()
            t = _add(t, _check("GET unknown", r.get("ok") is False, r))
            r = client.post("/api/projects/nope/undo", json={"steps": 1}).json()
            t = _add(t, _check("undo unknown -> clarify", r.get("ok") is False and r.get("action") == "clarify", r))
            r = client.post(
                f"/api/projects/{pid}/versions",
                files={"glb": ("x.glb", b"nope", "model/gltf-binary")},
                data={"op": "hand_edit"},
            )
            t = _add(t, _check(
                "bad upload -> clarify, not 500",
                r.status_code == 200 and r.json().get("action") == "clarify",
                r.status_code,
            ))

        mount = next((m for m in main.app.routes if getattr(m, "path", "") == "/media/projects"), None)
        t = _add(t, _check(
            "/media/projects mounted on projects_dir",
            mount is not None and Path(mount.app.directory) == Path(real_projects_dir),
        ))
    finally:
        main.settings.projects_dir = real_projects_dir
        shutil.rmtree(root, ignore_errors=True)
    return t


def test_cleanup_task_runs_on_startup():
    print("\n=== Test: cleanup runs on startup ===")
    import time
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import app.main as main

    fake = MagicMock(return_value={"audio": 0, "ref": 0, "projects": 0})
    with patch.object(main.projects, "cleanup", fake):
        with TestClient(main.app):
            deadline = time.time() + 2.0
            while not fake.called and time.time() < deadline:
                time.sleep(0.02)
    t = _check("cleanup called at startup", fake.called)
    t = _add(t, _check("hourly interval", main.CLEANUP_INTERVAL_S == 3600))
    return t


def run_all_tests():
    print("=" * 60)
    print("PROJECT / VERSION TESTS")
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
    test_models,
    test_create_and_append,
    test_prune,
    test_undo_redo,
    test_cleanup,
    test_routes,
    test_cleanup_task_runs_on_startup,
]


if __name__ == "__main__":
    sys.exit(run_all_tests())
