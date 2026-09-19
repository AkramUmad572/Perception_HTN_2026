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
]


if __name__ == "__main__":
    sys.exit(run_all_tests())
