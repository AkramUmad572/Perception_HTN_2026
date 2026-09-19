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
]


if __name__ == "__main__":
    sys.exit(run_all_tests())
