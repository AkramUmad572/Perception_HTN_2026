#!/usr/bin/env python3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from cad.export import default_stem, export_from_script, export_mesh_stl, export_session_model, safe_stem

BOX = """
import cadquery as cq
result = cq.Workplane("XY").box(20, 10, 4)
"""

HEX_CUBE = """
import cadquery as cq
body = cq.Workplane("XY").box(40, 40, 40)
assy = cq.Assembly()
assy.add(body, name="body", color=cq.Color("#1E88E5"))
result = assy
"""

REAL_CUBE = """import cadquery as cq

PARAMS = {
    "body_size_mm": 40.0,
    "body_fillet_mm": 2.0,
    "panel_size_mm": 28.0,
    "panel_thickness_mm": 1.5,
    "stand_width_mm": 50.0,
    "stand_height_mm": 8.0,
}

S = PARAMS["body_size_mm"]
R = PARAMS["body_fillet_mm"]
PS = PARAMS["panel_size_mm"]
PT = PARAMS["panel_thickness_mm"]
SW = PARAMS["stand_width_mm"]
SH = PARAMS["stand_height_mm"]

body = cq.Workplane("XY").box(S, S, S).edges().fillet(R)

stand = (
    cq.Workplane("XY")
    .transformed(offset=(0, 0, -S / 2 - SH / 2))
    .box(SW, SW, SH)
    .edges("|Z")
    .fillet(3.0)
)

panel_top = cq.Workplane("XY").transformed(offset=(0, 0, S / 2)).box(PS, PS, PT)
panel_front = cq.Workplane("XZ").transformed(offset=(0, -S / 2, 0)).box(PS, PS, PT)
panel_right = cq.Workplane("YZ").transformed(offset=(S / 2, 0, 0)).box(PS, PS, PT)

assy = cq.Assembly()
assy.add(body, name="body", color=cq.Color("#1E88E5"))
assy.add(stand, name="stand", color=cq.Color("#212121"))
assy.add(panel_top, name="panel_top", color=cq.Color("#FFD700"))
assy.add(panel_front, name="panel_front", color=cq.Color("#FFD700"))
assy.add(panel_right, name="panel_right", color=cq.Color("#FFD700"))

result = assy
"""


def test_safe_stem() -> None:
    assert safe_stem("Pikachu Keychain!!") == "pikachu_keychain"
    assert default_stem(SimpleNamespace(last_summary="yellow pikachu keychain")) == "yellow_pikachu_keychain"
    print("ok safe_stem")


def test_script_stl_and_step() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / "box.stl"
        step = Path(tmp) / "box.step"
        stl_out = export_from_script(BOX, stl)
        assert stl_out["ok"], stl_out
        assert stl.exists() and stl.stat().st_size > 80
        step_out = export_from_script(BOX, step)
        assert step_out["ok"], step_out
        assert step.exists() and step.stat().st_size > 80
    print("ok script_stl_and_step")


def test_mesh_fallback() -> None:
    from cad.sandbox import execute_cadquery_script

    with tempfile.TemporaryDirectory() as tmp:
        built = execute_cadquery_script(BOX, output_dir=tmp)
        assert built["ok"], built
        dest = Path(tmp) / "from_glb.stl"
        out = export_mesh_stl(Path(built["glb_path"]), dest, size_mm=40)
        assert out["ok"], out
        assert dest.exists() and dest.stat().st_size > 80
    print("ok mesh_fallback")


def test_real_headset_cube_exports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / "cube.stl"
        step = Path(tmp) / "cube.step"
        stl_out = export_from_script(REAL_CUBE, stl)
        assert stl_out["ok"], stl_out
        assert "Unknown color" not in str(stl_out)
        assert stl.exists() and stl.stat().st_size > 200
        step_out = export_from_script(REAL_CUBE, step)
        assert step_out["ok"], step_out
        assert step.exists() and step.stat().st_size > 200
    print("ok real_headset_cube_exports")


def test_broken_script_falls_back_to_glb() -> None:
    from cad.sandbox import execute_cadquery_script

    with tempfile.TemporaryDirectory() as tmp:
        built = execute_cadquery_script(BOX, output_dir=tmp)
        assert built["ok"], built
        glb_dir = Path(tmp) / "glb"
        glb_dir.mkdir()
        dest_glb = glb_dir / "live.glb"
        dest_glb.write_bytes(Path(built["glb_path"]).read_bytes())
        session = SimpleNamespace(
            last_script="import cadquery as cq\nraise RuntimeError('boom')\nresult = None",
            glb_url="/media/glb/live.glb",
            base_size_m=0.04,
            scale=1.0,
        )
        settings = SimpleNamespace(glb_dir=glb_dir, projects_dir=Path(tmp))
        out = export_session_model(session, settings, Path(tmp) / "fb.stl", "stl")
        assert out["ok"], out
        assert out.get("fallback") == "mesh"
        assert Path(out["path"]).exists()
    print("ok broken_script_falls_back_to_glb")


def test_hex_color_assembly_exports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / "hex.stl"
        step = Path(tmp) / "hex.step"
        stl_out = export_from_script(HEX_CUBE, stl)
        assert stl_out["ok"], stl_out
        assert "Unknown color" not in str(stl_out)
        assert stl.exists() and stl.stat().st_size > 80
        step_out = export_from_script(HEX_CUBE, step)
        assert step_out["ok"], step_out
        assert step.exists() and step.stat().st_size > 80
    print("ok hex_color_assembly_exports")


def test_session_step_needs_cad() -> None:
    session = SimpleNamespace(last_script=None, glb_url=None, base_size_m=0.2, scale=1.0)
    settings = SimpleNamespace(glb_dir=Path("/tmp"), projects_dir=Path("/tmp"))
    out = export_session_model(session, settings, Path("/tmp/nope.step"), "step")
    assert out["ok"] is False
    assert out["error"] == "step_needs_cad"
    print("ok session_step_needs_cad")


if __name__ == "__main__":
    test_safe_stem()
    test_script_stl_and_step()
    test_mesh_fallback()
    test_real_headset_cube_exports()
    test_broken_script_falls_back_to_glb()
    test_hex_color_assembly_exports()
    test_session_step_needs_cad()
    print("all export tests passed")
