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


# ---------------------------------------------------------------- repair


def _sphere() -> trimesh.Trimesh:
    s = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    s.visual.vertex_colors = [180, 120, 60, 255]
    return s


def _holed(keep_mask) -> trimesh.Trimesh:
    s = _sphere()
    s.update_faces(keep_mask(s))
    s.remove_unreferenced_vertices()
    return s


def _small_hole() -> trimesh.Trimesh:
    return _holed(lambda s: np.arange(len(s.faces)) > 2)


def _big_hole() -> trimesh.Trimesh:
    return _holed(lambda s: s.triangles_center[:, 1] <= 0.7)


def _scattered_holes() -> trimesh.Trimesh:
    rng = np.random.default_rng(0)
    return _holed(lambda s: rng.random(len(s.faces)) > 0.03)


def _solid_check(name: str, mesh, ref_volume: float, tol: float) -> tuple[int, int]:
    ok = (
        isinstance(mesh, trimesh.Trimesh)
        and mesh.is_watertight
        and mesh.is_winding_consistent
        and abs(mesh.volume - ref_volume) <= tol
        and mesh.visual.kind == "vertex"
    )
    detail = ""
    if isinstance(mesh, trimesh.Trimesh):
        detail = (
            f"watertight={mesh.is_watertight} volume={mesh.volume:.4f} "
            f"ref={ref_volume:.4f} kind={mesh.visual.kind}"
        )
    return _check(name, ok, detail)


def test_repair_watertight_passthrough(tmp: Path) -> tuple[int, int]:
    print("\nrepair keeps a watertight mesh as is")
    s = _sphere()
    return _solid_check("sphere unchanged", boolean.repair(s), s.volume, 1e-9)


def test_repair_small_hole(tmp: Path) -> tuple[int, int]:
    print("\nrepair closes a small hole")
    return _solid_check("3-face gap closed", boolean.repair(_small_hole()), _sphere().volume, 0.01)


def test_repair_big_hole(tmp: Path) -> tuple[int, int]:
    print("\nrepair closes a big hole")
    # The cap comes back flat, so a little volume is lost; it must still be most of it.
    return _solid_check("cap hole closed", boolean.repair(_big_hole()), _sphere().volume, 0.4)


def test_repair_scattered_holes(tmp: Path) -> tuple[int, int]:
    print("\nrepair closes many holes, including pinched ones")
    return _solid_check(
        "3% random faces missing", boolean.repair(_scattered_holes()), _sphere().volume, 0.05
    )


def test_repair_split_seam(tmp: Path) -> tuple[int, int]:
    print("\nrepair welds a mesh whose faces share no vertices (UV seams)")
    box = trimesh.creation.box(extents=(1, 1, 1))
    box.unmerge_vertices()
    box.vertices = box.vertices + np.random.default_rng(1).normal(0, 1e-9, box.vertices.shape)
    box.visual.vertex_colors = [10, 200, 10, 255]
    return _solid_check("seams welded", boolean.repair(box), 1.0, 1e-6)


def test_repair_drops_debris(tmp: Path) -> tuple[int, int]:
    print("\nrepair drops a stray open sliver far from the body")
    s = _sphere()
    sliver = trimesh.Trimesh(
        vertices=[[5, 5, 5], [5.1, 5, 5], [5, 5.1, 5]], faces=[[0, 1, 2]], process=False
    )
    sliver.visual.vertex_colors = [0, 0, 0, 255]
    merged = trimesh.util.concatenate([s, sliver])
    out = boolean.repair(merged)
    p, f = _solid_check("sphere survives", out, s.volume, 0.01)
    p2, f2 = _check("sliver gone", out.bounds[1].max() < 1.5, str(out.bounds))
    return p + p2, f + f2


def test_repair_keeps_colors(tmp: Path) -> tuple[int, int]:
    print("\nrepair keeps baked texture colours")
    mesh = boolean.load_mesh(_textured_box_glb(tmp / "tex.glb"))
    ok, why = _colors_split_by_x(boolean.repair(mesh))
    return _check("red/blue survive", ok, why)


def test_repair_hopeless_raises_speakable(tmp: Path) -> tuple[int, int]:
    print("\nrepair gives up on a flat open sheet")
    sheet = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )
    sheet.visual.vertex_colors = [255, 255, 255, 255]
    p, f = _expect_error("flat sheet", lambda: boolean.repair(sheet))
    empty = trimesh.Trimesh()
    p2, f2 = _expect_error("empty mesh", lambda: boolean.repair(empty))
    return p + p2, f + f2


# ---------------------------------------------------------------- runner

TESTS = [
    test_load_bakes_texture,
    test_load_flattens_scene,
    test_export_roundtrip_keeps_colors,
    test_load_missing_file_speakable,
    test_repair_watertight_passthrough,
    test_repair_small_hole,
    test_repair_big_hole,
    test_repair_scattered_holes,
    test_repair_split_seam,
    test_repair_drops_debris,
    test_repair_keeps_colors,
    test_repair_hopeless_raises_speakable,
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
