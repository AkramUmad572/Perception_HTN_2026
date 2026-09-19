#!/usr/bin/env python3
"""Mesh boolean tests: synthetic meshes only, no network."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import trimesh
from PIL import Image

from mesh import boolean
from mesh.boolean import BooleanError

RED = (255, 0, 0)
BLUE = (0, 0, 255)


# ---------------------------------------------------------------- helpers


def _textured_box_glb(dest: Path) -> Path:
    """A unit cube whose texture is red where x<0 and blue where x>0."""
    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    box.unmerge_vertices()
    uv = np.zeros((len(box.vertices), 2))
    uv[:, 0] = np.where(box.vertices[:, 0] < 0, 0.25, 0.75)
    uv[:, 1] = 0.5
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, :2] = RED
    img[:, 2:] = BLUE
    box.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(img))
    box.export(str(dest))
    return dest


def _is_speakable(msg: str) -> bool:
    if not msg or len(msg) > 200:
        return False
    if re.search(r"[/\\_`*#<>{}\[\]]", msg):
        return False
    if re.search(r"(Error|Exception|Traceback|\.py|\.glb)", msg):
        return False
    return msg.rstrip()[-1] in ".!?"


def _colors_split_by_x(mesh: trimesh.Trimesh, margin: float = 0.1) -> tuple[bool, str]:
    """Every vertex left of -margin is red, right of +margin is blue."""
    if mesh.visual.kind != "vertex":
        return False, f"visual kind is {mesh.visual.kind}"
    rgb = np.asarray(mesh.visual.vertex_colors)[:, :3]
    x = mesh.vertices[:, 0]
    left = rgb[x < -margin]
    right = rgb[x > margin]
    if len(left) == 0 or len(right) == 0:
        return False, "no vertices on one side"
    if not (left == RED).all():
        return False, f"left side not all red: {np.unique(left, axis=0)}"
    if not (right == BLUE).all():
        return False, f"right side not all blue: {np.unique(right, axis=0)}"
    return True, ""


def _check(name: str, cond: bool, detail: str = "") -> tuple[int, int]:
    if cond:
        print(f"  [ok] {name}")
        return 1, 0
    print(f"  [FAIL] {name} {detail}")
    return 0, 1


def _expect_error(name: str, fn) -> tuple[int, int]:
    try:
        fn()
    except BooleanError as exc:
        return _check(name, _is_speakable(str(exc)), f"not speakable: {exc!r}")
    except Exception as exc:  # noqa: BLE001
        return _check(name, False, f"raised {type(exc).__name__}: {exc}")
    return _check(name, False, "did not raise")


# ---------------------------------------------------------------- load / export


def test_load_bakes_texture(tmp: Path) -> tuple[int, int]:
    print("\nload_mesh bakes texture to vertex colours")
    mesh = boolean.load_mesh(_textured_box_glb(tmp / "tex.glb"))
    ok, why = _colors_split_by_x(mesh)
    p, f = _check("colours split red/blue", ok, why)
    p2, f2 = _check("volume ~1", abs(abs(mesh.volume) - 1.0) < 1e-6, str(mesh.volume))
    return p + p2, f + f2


def test_load_flattens_scene(tmp: Path) -> tuple[int, int]:
    print("\nload_mesh concatenates a multi-node scene with transforms")
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=(1, 1, 1)), node_name="a")
    scene.add_geometry(
        trimesh.creation.box(extents=(1, 1, 1)),
        node_name="b",
        transform=trimesh.transformations.translation_matrix((5, 0, 0)),
    )
    dest = tmp / "two.glb"
    scene.export(str(dest))
    mesh = boolean.load_mesh(dest)
    ok = isinstance(mesh, trimesh.Trimesh) and np.allclose(
        mesh.bounds, [[-0.5, -0.5, -0.5], [5.5, 0.5, 0.5]]
    )
    return _check("one mesh spanning both nodes", ok, str(getattr(mesh, "bounds", None)))


def test_export_roundtrip_keeps_colors(tmp: Path) -> tuple[int, int]:
    print("\nexport_glb round-trip keeps vertex colours")
    mesh = boolean.load_mesh(_textured_box_glb(tmp / "tex.glb"))
    out = tmp / "out.glb"
    boolean.export_glb(mesh, out)
    again = boolean.load_mesh(out)
    ok, why = _colors_split_by_x(again)
    return _check("colours after round-trip", ok, why)


def test_load_missing_file_speakable(tmp: Path) -> tuple[int, int]:
    print("\nload_mesh on a missing file")
    return _expect_error("speakable error", lambda: boolean.load_mesh(tmp / "nope.glb"))


# ---------------------------------------------------------------- runner

TESTS = [
    test_load_bakes_texture,
    test_load_flattens_scene,
    test_export_roundtrip_keeps_colors,
    test_load_missing_file_speakable,
]


def run_all_tests() -> int:
    import tempfile

    print("=" * 60)
    print("MESH BOOLEAN TESTS")
    print("=" * 60)
    passed = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for test in TESTS:
            try:
                p, f = test(Path(tmp))
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] {test.__name__} crashed: {type(exc).__name__}: {exc}")
                p, f = 0, 1
            passed += p
            failed += f
    print(f"\nTOTAL: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
