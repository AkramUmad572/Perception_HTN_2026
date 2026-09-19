#!/usr/bin/env python3
"""
Regression tests for pipeline: follow-up response contract.

Run with: python -m app.test_pipeline
Or:       python backend/app/test_pipeline.py

Tests that follow-ups (set_material, generate, etc.) ALWAYS return:
- rebuilt=True
- new model_id (fresh, different from session's old model_id)
- new glb_url

A "successful" modification with no new asset would be a client-invisible no-op.
Contract with Tabish: client uses model_id + glb_url to swap mesh.

=== SIZE/GEOMETRY EDITS ===
Scale ("make it bigger") and geometry modifications ("add a hole") use the `generate`
action path — they produce a new CadQuery script and execute it through the sandbox.
This is intentional: any structural change to the model requires full script execution
and MUST return rebuilt=True with fresh model_id/glb_url on success.

See apply_intent action="generate": size/geometry follow-ups produce a new CadQuery
script via Gemini (with last_script + last_summary) and execute it in the sandbox.
There is no regex that rewrites every number in the script (that used to break
n_teeth / range() on gears).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app import jobs
from app.models import Intent, SessionState, CommandResponse
from app.pipeline import apply_intent, build_from_image


def _mock_settings():
    """Create mock settings for testing."""
    settings = MagicMock()
    settings.glb_dir = Path("/tmp/test_glb")
    settings.audio_dir = Path("/tmp/test_audio")
    settings.elevenlabs_api_key = None  # Disable TTS
    settings.meshy_api_key = ""
    settings.nvidia_api_key = ""
    settings.three_ws_enabled = True
    settings.hf_space_enabled = True
    settings.hf_token = ""
    return settings


# ============================================================================
# Test 1: Color change on session with last_script MUST return new model
# ============================================================================

async def test_color_change_with_script_returns_new_model():
    """Test that set_material with last_script returns new model_id and glb_url."""
    print("\n=== Test: Color change with script returns new model ===")
    
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},  # yellow
        reply="Changed to yellow.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return success with new model
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "new_model_456", None)
        
        # Mock synthesize_speech to avoid actual TTS
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            
            # Mock save_session
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    # Assertions
    errors = []
    
    if not result.rebuilt:
        errors.append("rebuilt should be True")
    
    if result.model_id != "new_model_456":
        errors.append(f"model_id should be 'new_model_456', got {result.model_id!r}")
    
    if result.glb_url is None:
        errors.append("glb_url should not be None")
    
    if result.action != "set_material":
        errors.append(f"action should be 'set_material', got {result.action!r}")

    flatten = mock_exec.call_args.kwargs.get("flatten_color")
    if flatten is not True:
        errors.append(f"set_material must pass flatten_color=True, got {flatten!r}")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Color change correctly returns new model_id, glb_url")
        return 1, 0


# ============================================================================
# Test 2: Color change with NO script/template should fail gracefully
# ============================================================================

async def test_color_change_no_model_fails():
    """Test that set_material with no script or template returns an error."""
    print("\n=== Test: Color change with no model fails gracefully ===")
    
    session = SessionState(
        session_id="test",
        last_script=None,
        template=None,
        model_id=None,
        glb_url=None,
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to yellow.",
    )
    
    settings = _mock_settings()
    
    with patch("app.pipeline.synthesize_speech") as mock_tts:
        mock_tts.return_value = (None, 0.0)
        with patch("app.pipeline.save_session"):
            result = await apply_intent(intent, session, settings)
    
    errors = []
    
    if result.ok:
        errors.append("ok should be False when no model to apply color to")
    
    if result.rebuilt:
        errors.append("rebuilt should be False when no model exists")
    
    if result.error is None:
        errors.append("error should contain a message")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Color change with no model correctly returns error")
        return 1, 0


# ============================================================================
# Test 3: Color change rebuild failure should return error, not silent success
# ============================================================================

async def test_color_change_rebuild_failure_returns_error():
    """Test that set_material rebuild failure returns an error, not silent success."""
    print("\n=== Test: Color change rebuild failure returns error ===")
    
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to yellow.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return failure
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (False, None, "Sandbox timeout")
        
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    errors = []
    
    # The key assertion: a rebuild failure should NOT return ok=True
    if result.ok:
        errors.append("ok should be False when rebuild fails")
    
    if result.rebuilt:
        errors.append("rebuilt should be False when rebuild fails")
    
    if result.error is None:
        errors.append("error should contain the failure reason")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Color change rebuild failure correctly returns error")
        return 1, 0


# ============================================================================
# Test 4: Generate action with script returns new model (size/geometry edits)
# ============================================================================

async def test_generate_action_with_script_returns_new_model():
    """
    Test that generate action with script returns new model_id and glb_url.
    
    SIZE/GEOMETRY FOLLOW-UPS:
    Scale ("make it bigger", "make it smaller") and geometry modifications
    ("add a hole", "make it taller") use the `generate` action path.
    
    This test confirms that action=generate with a valid script:
    - Returns rebuilt=True
    - Returns new model_id
    - Returns glb_url set
    
    This is the SAME contract as color changes — any successful model modification
    MUST return fresh assets so the client can swap the mesh.
    """
    print("\n=== Test: Generate action with script returns new model ===")
    print("  (Used by scale/geometry edits: 'make it bigger', 'add a hole', etc.)")
    
    session = SessionState(
        session_id="test",
        last_script=None,  # Will be set after execution
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    # Intent with action=generate and a valid script (e.g., from scale modification)
    intent = Intent(
        action="generate",
        script='import cadquery as cq\nresult = cq.Workplane("XY").box(15, 15, 7.5)',  # scaled box
        reply="Made it bigger.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return success with new model
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "new_scaled_model_789", None)
        
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    # Verify _execute_with_retry was called with the script
    mock_exec.assert_called_once()
    call_kwargs = mock_exec.call_args
    assert call_kwargs[1]["script"] == intent.script or call_kwargs[0][0] == intent.script
    
    errors = []
    
    if not result.rebuilt:
        errors.append("rebuilt should be True for successful generate action")
    
    if result.model_id != "new_scaled_model_789":
        errors.append(f"model_id should be 'new_scaled_model_789', got {result.model_id!r}")
    
    if result.glb_url is None:
        errors.append("glb_url should not be None")
    
    if result.action != "generate":
        errors.append(f"action should be 'generate', got {result.action!r}")
    
    if not result.ok:
        errors.append(f"ok should be True, got {result.ok!r}")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Generate action correctly returns rebuilt=True, new model_id, glb_url")
        return 1, 0


# ============================================================================
# Test 5: Generate action failure returns error (not silent success)
# ============================================================================

async def test_generate_action_failure_returns_error():
    """
    Test that generate action sandbox failure returns an error, not silent success.
    
    When a geometry edit fails (e.g., invalid CadQuery), the response MUST:
    - Return ok=False
    - Return rebuilt=False
    - Return error message
    - NOT return a stale/old model_id
    """
    print("\n=== Test: Generate action failure returns error ===")
    
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="generate",
        script='import cadquery as cq\nresult = cq.Workplane("XY").invalid_op()',  # Bad script
        reply="Making changes.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return failure
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (False, None, "CadQuery error: invalid_op not found")
        
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    errors = []
    
    if result.ok:
        errors.append("ok should be False when generate fails")
    
    if result.rebuilt:
        errors.append("rebuilt should be False when generate fails")
    
    if result.error is None:
        errors.append("error should contain the failure reason")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Generate action failure correctly returns error, rebuilt=False")
        return 1, 0


# ============================================================================
# Test 6: Generate action with no script falls back to clarify
# ============================================================================

async def test_generate_action_no_script_returns_clarify():
    """
    Test that generate action with no script returns clarify action.
    
    Edge case: LLM returns action=generate but no script. Should gracefully
    fall back to clarify, not crash or return rebuilt=True with stale data.
    """
    print("\n=== Test: Generate action with no script returns clarify ===")
    
    session = SessionState(
        session_id="test",
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="generate",
        script=None,  # No script provided
        reply="I'll build that.",
    )
    
    settings = _mock_settings()
    
    with patch("app.pipeline.synthesize_speech") as mock_tts:
        mock_tts.return_value = (None, 0.0)
        with patch("app.pipeline.save_session"):
            result = await apply_intent(intent, session, settings)
    
    errors = []
    
    if result.rebuilt:
        errors.append("rebuilt should be False when no script")
    
    if result.action != "clarify":
        errors.append(f"action should be 'clarify', got {result.action!r}")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Generate with no script correctly returns clarify, rebuilt=False")
        return 1, 0


# ============================================================================
# COMPLEX FREE-REIN SCRIPTS FIXTURE
# These are sandbox-safe CadQuery scripts for complex shapes.
# Used in tests to verify the pipeline returns rebuilt=True + model_id + glb_url.
#
# Each script uses ONLY allowed operations:
# - cq.Workplane("XY"/"XZ"/"YZ")
# - .box(), .cylinder(), .sphere(), .circle(), .rect(), .polygon()
# - .extrude(), .loft(), .revolve()
# - .hole(), .cut(), .union(), .fillet(), .chamfer()
# - .text(), .faces(), .edges(), .workplane(), .center()
# - import math for trig/constants
# ============================================================================

COMPLEX_SCRIPTS = {
    "keychain_with_hole_and_text": '''import cadquery as cq
plate = cq.Workplane("XY").box(50, 25, 4).edges("|Z").fillet(3)
with_hole = plate.faces(">Z").workplane().center(20, 0).hole(5)
result = with_hole.faces(">Z").workplane().center(-5, 0).text("KEY", 8, 1)
''',

    "phone_stand": '''import cadquery as cq
base = cq.Workplane("XY").box(80, 50, 8)
back = cq.Workplane("XY").workplane(offset=8).center(0, -20).box(80, 10, 60)
lip = cq.Workplane("XY").workplane(offset=8).center(0, 10).box(80, 5, 15)
result = base.union(back).union(lip).edges().fillet(2)
''',

    "mug_with_handle": '''import cadquery as cq
body = cq.Workplane("XY").circle(25).extrude(60).faces(">Z").shell(-3)
handle = cq.Workplane("XZ").center(25, 30).ellipse(8, 15).extrude(5)
result = body.union(handle)
''',

    "gear_12_teeth": '''import cadquery as cq
import math
n_teeth = 12
outer_r, inner_r = 25, 20
pts = []
for i in range(n_teeth * 2):
    angle = i * math.pi / n_teeth
    r = outer_r if i % 2 == 0 else inner_r
    pts.append((r * math.cos(angle), r * math.sin(angle)))
result = cq.Workplane("XY").polyline(pts).close().extrude(8).faces(">Z").workplane().hole(10)
''',

    "nameplate": '''import cadquery as cq
plate = cq.Workplane("XY").box(100, 30, 5).edges("|Z").fillet(5)
result = plate.faces(">Z").workplane().text("HELLO", 12, 2)
''',

    "desk_organizer": '''import cadquery as cq
base = cq.Workplane("XY").box(120, 80, 10)
pen_holder = cq.Workplane("XY").workplane(offset=10).center(-40, 0).box(30, 30, 60)
pen_with_hole = pen_holder.faces(">Z").workplane().hole(20)
card_slot = cq.Workplane("XY").workplane(offset=10).center(20, 0).box(60, 10, 40)
result = base.union(pen_with_hole).union(card_slot)
''',

    "vase": '''import cadquery as cq
pts = [(0, 0), (20, 0), (15, 30), (18, 50), (10, 60), (10, 65), (18, 65), (20, 50), (17, 30), (22, 0)]
result = cq.Workplane("XZ").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0))
''',

    "hinge": '''import cadquery as cq
plate1 = cq.Workplane("XY").box(40, 30, 3)
plate2 = cq.Workplane("XY").workplane(offset=3).center(0, 15).box(40, 30, 3)
pin_cyl = cq.Workplane("XZ").center(0, 4.5).circle(3).extrude(40)
result = plate1.union(plate2).union(pin_cyl).faces(">Z").workplane().center(0, 15).hole(2)
''',
}


# ============================================================================
# Test 7: Complex free-rein scripts return rebuilt=True + model_id + glb_url
# ============================================================================

async def test_complex_freerein_scripts_return_rebuilt():
    """
    Test that complex free-rein CadQuery scripts return rebuilt=True + model_id + glb_url.
    
    These test cases cover:
    - keychain with hole and text (box + fillet + hole + text)
    - phone stand (multiple boxes + union + fillet)
    - mug with handle (cylinder + shell + ellipse + union)
    - 12-tooth gear (polyline + math + extrude + hole)
    - nameplate (box + fillet + text)
    - desk organizer (boxes + union + hole)
    - vase (polyline + revolve)
    - hinge (boxes + cylinder + union + hole)
    
    All use sandbox-safe CadQuery operations:
    Workplane, box, hole, fillet, cut, text, extrude, union, shell, polyline, revolve
    """
    print("\n" + "=" * 60)
    print("=== Test: Complex free-rein scripts return rebuilt ===")
    print("=" * 60)
    
    settings = _mock_settings()
    total_pass = 0
    total_fail = 0
    
    for name, script in COMPLEX_SCRIPTS.items():
        print(f"\n  Testing: {name}")
        
        session = SessionState(
            session_id="test",
            model_id="old_model",
            glb_url="/media/glb/old_model.glb",
            color="#C0C0C0",
        )
        
        intent = Intent(
            action="generate",
            script=script,
            reply=f"Built {name}.",
        )
        
        # Mock sandbox to return success with unique model_id
        new_model_id = f"complex_{name}_model"
        
        with patch("app.pipeline._execute_with_retry") as mock_exec:
            mock_exec.return_value = (True, new_model_id, None)
            
            with patch("app.pipeline.synthesize_speech") as mock_tts:
                mock_tts.return_value = (None, 0.0)
                
                with patch("app.pipeline.save_session"):
                    result = await apply_intent(intent, session, settings)
        
        errors = []
        
        if not result.rebuilt:
            errors.append("rebuilt should be True")
        
        if result.model_id != new_model_id:
            errors.append(f"model_id should be '{new_model_id}', got {result.model_id!r}")
        
        if result.glb_url is None:
            errors.append("glb_url should not be None")
        
        if not result.ok:
            errors.append(f"ok should be True, got error: {result.error}")
        
        # Verify mock was called with the script
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        called_script = call_args[1].get("script") or call_args[0][0]
        if called_script != script:
            errors.append("_execute_with_retry not called with correct script")
        
        if errors:
            print(f"    ✗ FAILED:")
            for e in errors:
                print(f"      - {e}")
            total_fail += 1
        else:
            print(f"    ✓ {name}: rebuilt=True, model_id={new_model_id}")
            total_pass += 1
    
    return total_pass, total_fail


# ============================================================================
# Test 8: Verify scripts use sandbox-safe CadQuery patterns
# ============================================================================

def test_complex_scripts_are_sandbox_safe():
    """
    Verify that COMPLEX_SCRIPTS only use sandbox-allowed patterns.
    
    ALLOWED:
    - import cadquery as cq
    - import math
    - cq.Workplane("XY"/"XZ"/"YZ")
    - .box(), .circle(), .rect(), .polygon(), .polyline()
    - .extrude(), .loft(), .revolve()
    - .hole(), .cut(), .union(), .intersect()
    - .fillet(), .chamfer()
    - .text(), .shell()
    - .faces(), .edges(), .workplane(), .center()
    - Python loops/variables for geometry
    
    FORBIDDEN:
    - import os/sys/subprocess/socket/etc
    - open(), exec(), eval(), getattr()
    - cq.occ_impl, cq.exporters, cq.__file__
    """
    print("\n=== Test: Complex scripts use sandbox-safe patterns ===")
    
    import ast
    
    ALLOWED_IMPORTS = {"cadquery", "cq", "math"}
    FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "getattr", "setattr", "delattr"}
    
    total_pass = 0
    total_fail = 0
    
    for name, script in COMPLEX_SCRIPTS.items():
        errors = []
        
        try:
            tree = ast.parse(script)
        except SyntaxError as e:
            errors.append(f"Syntax error: {e}")
            print(f"  ✗ {name}: {errors}")
            total_fail += 1
            continue
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".")[0]
                    if base not in ALLOWED_IMPORTS:
                        errors.append(f"Forbidden import: {alias.name}")
            
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base = node.module.split(".")[0]
                    if base not in ALLOWED_IMPORTS:
                        errors.append(f"Forbidden import from: {node.module}")
            
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in FORBIDDEN_CALLS:
                        errors.append(f"Forbidden call: {node.func.id}()")
        
        if errors:
            print(f"  ✗ {name}:")
            for e in errors:
                print(f"    - {e}")
            total_fail += 1
        else:
            print(f"  ✓ {name}: sandbox-safe")
            total_pass += 1
    
    return total_pass, total_fail


# ============================================================================
# Test 9: Follow-up on complex session.last_script
# ============================================================================

async def test_followups_on_complex_script():
    """
    Test follow-up operations on a complex session.last_script.
    
    Scenario: User built a complex gear, then:
    1. set_material yellow → rebuilt=True + new model_id
    2. generate "make it bigger" (scaled script) → rebuilt=True + new model_id
    3. generate "add a hole" (modified script) → rebuilt=True + new model_id
    
    All MUST return rebuilt=True with fresh model_id/glb_url.
    """
    print("\n=== Test: Follow-ups on complex session.last_script ===")
    
    settings = _mock_settings()
    total_pass = 0
    total_fail = 0
    
    # Original complex script (gear)
    original_script = COMPLEX_SCRIPTS["gear_12_teeth"]
    
    # Test 1: set_material yellow
    print("\n  9a. set_material yellow on gear")
    session = SessionState(
        session_id="test",
        last_script=original_script,
        model_id="gear_original",
        glb_url="/media/glb/gear_original.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Made it yellow.",
    )
    
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "gear_yellow", None)
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    if result.rebuilt and result.model_id == "gear_yellow" and result.ok:
        print(f"    ✓ set_material: rebuilt=True, model_id=gear_yellow")
        total_pass += 1
    else:
        print(f"    ✗ set_material: rebuilt={result.rebuilt}, model_id={result.model_id}, ok={result.ok}")
        total_fail += 1
    
    # Test 2: generate "make it bigger" (scaled script)
    print("\n  9b. generate 'make it bigger' (scaled script)")
    
    # Simulated scaled script (1.25x dimensions)
    scaled_script = '''import cadquery as cq
import math
n_teeth = 12
outer_r, inner_r = 31.25, 25.0  # scaled 1.25x
pts = []
for i in range(n_teeth * 2):
    angle = i * math.pi / n_teeth
    r = outer_r if i % 2 == 0 else inner_r
    pts.append((r * math.cos(angle), r * math.sin(angle)))
result = cq.Workplane("XY").polyline(pts).close().extrude(10).faces(">Z").workplane().hole(12.5)
'''
    
    session.last_script = original_script  # Reset
    intent = Intent(
        action="generate",
        script=scaled_script,
        reply="Made it bigger.",
    )
    
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "gear_bigger", None)
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    if result.rebuilt and result.model_id == "gear_bigger" and result.ok:
        print(f"    ✓ scale 'bigger': rebuilt=True, model_id=gear_bigger")
        total_pass += 1
    else:
        print(f"    ✗ scale 'bigger': rebuilt={result.rebuilt}, model_id={result.model_id}, ok={result.ok}")
        total_fail += 1
    
    # Test 3: generate "add a hole" (modified script)
    print("\n  9c. generate 'add a hole' (modified script)")
    
    # Script with additional hole
    hole_script = '''import cadquery as cq
import math
n_teeth = 12
outer_r, inner_r = 25, 20
pts = []
for i in range(n_teeth * 2):
    angle = i * math.pi / n_teeth
    r = outer_r if i % 2 == 0 else inner_r
    pts.append((r * math.cos(angle), r * math.sin(angle)))
gear = cq.Workplane("XY").polyline(pts).close().extrude(8).faces(">Z").workplane().hole(10)
result = gear.faces(">Z").workplane().center(15, 0).hole(5)  # additional hole
'''
    
    intent = Intent(
        action="generate",
        script=hole_script,
        reply="Added a hole.",
    )
    
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "gear_with_hole", None)
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    if result.rebuilt and result.model_id == "gear_with_hole" and result.ok:
        print(f"    ✓ add hole: rebuilt=True, model_id=gear_with_hole")
        total_pass += 1
    else:
        print(f"    ✗ add hole: rebuilt={result.rebuilt}, model_id={result.model_id}, ok={result.ok}")
        total_fail += 1
    
    return total_pass, total_fail


# ============================================================================
# Test 10: FAIL if complex shapes collapse to ring/box/cylinder templates
# ============================================================================

async def test_no_collapse_to_templates():
    """
    Assert that complex free-rein shapes do NOT collapse to ring/box/cylinder templates.
    
    The pipeline should use action="generate" with script execution,
    NOT action="create" with template="ring"/"box"/"cylinder".
    
    If apply_intent were to use the template path for complex shapes,
    it would be a regression (loss of fidelity).
    """
    print("\n=== Test: Complex shapes do NOT collapse to templates ===")
    
    from cad.builder import DEFAULTS
    TEMPLATE_NAMES = set(DEFAULTS.keys())  # {"ring", "box", "cylinder"}
    
    settings = _mock_settings()
    total_pass = 0
    total_fail = 0
    
    for name, script in COMPLEX_SCRIPTS.items():
        print(f"\n  Testing: {name}")
        
        session = SessionState(
            session_id="test",
            template=None,  # No template
            model_id="old_model",
            color="#C0C0C0",
        )
        
        intent = Intent(
            action="generate",  # MUST be generate, not create
            script=script,
            template=None,  # MUST be None, not ring/box/cylinder
            reply=f"Built {name}.",
        )
        
        errors = []
        
        # Check: action must be "generate", not "create"
        if intent.action == "create":
            errors.append("action should be 'generate', not 'create'")
        
        # Check: template must be None, not a basic template
        if intent.template in TEMPLATE_NAMES:
            errors.append(f"template should be None, not '{intent.template}'")
        
        # Check: script must be present
        if not intent.script:
            errors.append("script should be present for free-rein shapes")
        
        # Verify the pipeline would execute via generate path
        with patch("app.pipeline._execute_with_retry") as mock_exec:
            mock_exec.return_value = (True, f"freerein_{name}", None)
            
            with patch("app.pipeline.synthesize_speech") as mock_tts:
                mock_tts.return_value = (None, 0.0)
                
                with patch("app.pipeline.save_session"):
                    result = await apply_intent(intent, session, settings)
        
        # Check result action is still "generate", not collapsed to "create"
        if result.action == "create":
            errors.append(f"result.action should be 'generate', got 'create'")
        
        # Check template was not set on session
        if session.template in TEMPLATE_NAMES:
            errors.append(f"session.template should be None, got '{session.template}'")
        
        if errors:
            print(f"    ✗ COLLAPSED TO TEMPLATE:")
            for e in errors:
                print(f"      - {e}")
            total_fail += 1
        else:
            print(f"    ✓ {name}: uses generate path, no template collapse")
            total_pass += 1
    
    return total_pass, total_fail


# ============================================================================
# Test 11: Generate bakes named color from transcript and stores last_summary
# ============================================================================

async def test_generate_bakes_named_color_and_summary():
    """Named color in the utterance is applied before sandbox exec; summary is stored."""
    print("\n=== Test: Generate bakes named color + last_summary ===")

    session = SessionState(
        session_id="test",
        model_id="old_model",
        glb_url="/media/glb/old_model.glb",
        color="#C0C0C0",
        last_summary=None,
    )
    intent = Intent(
        action="generate",
        script='import cadquery as cq\nresult = cq.Workplane("XY").box(20, 20, 10)',
        reply="Built a yellow mug.",
    )
    settings = _mock_settings()

    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "mug_yellow", None)
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(
                    intent,
                    session,
                    settings,
                    transcript="build me a yellow mug",
                )

    errors = []
    if session.color != "#FFD700":
        errors.append(f"session.color should be #FFD700, got {session.color!r}")
    if session.last_summary != "Built a yellow mug.":
        errors.append(f"last_summary should be stored, got {session.last_summary!r}")
    if result.color != "#FFD700":
        errors.append(f"response color should be #FFD700, got {result.color!r}")
    if not result.rebuilt:
        errors.append("rebuilt should be True")
    flatten = mock_exec.call_args.kwargs.get("flatten_color")
    if flatten is not False:
        errors.append(f"generate must pass flatten_color=False, got {flatten!r}")

    if errors:
        print("  [FAIL]")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print("  [ok] named color baked and last_summary stored")
    return 1, 0


async def test_mesh_generate_skips_sandbox_and_sets_session():
    """backend=mesh must call Meshy, not CadQuery, and keep last_script unset."""
    print("\n=== Test: Mesh generate skips sandbox ===")
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10,10,5)',
        last_backend="cad",
        model_id="old",
        glb_url="/media/glb/old.glb",
    )
    intent = Intent(
        action="generate",
        backend="mesh",
        mesh_prompt="a yellow electric mouse, full body 3d model",
        script=None,
        reply="Here's that sculpt.",
    )
    settings = _mock_settings()
    settings.meshy_api_key = "test-key"

    with patch("app.pipeline._execute_with_retry") as mock_cad:
        with patch("app.pipeline.generate_mesh_glb") as mock_mesh:
            mock_mesh.return_value = {
                "ok": True,
                "model_id": "mesh123abc",
                "glb_path": "/tmp/test_glb/mesh123abc.glb",
                "exec_ms": 1200,
                "textured": True,
            }
            with patch("app.pipeline.synthesize_speech") as mock_tts:
                mock_tts.return_value = (None, 0.0)
                with patch("app.pipeline.save_session"):
                    # Mesh builds run detached now (see _start_mesh_build): the
                    # request gets an instant ack, the sculpt itself is a job.
                    ack = await apply_intent(intent, session, settings)
                    job = jobs.get(ack.job_id)
                    await job.task
                    result = job.result

    errors = []
    if ack.action != "building" or not ack.job_id:
        errors.append("apply_intent should ack immediately with a job_id")
    if mock_cad.called:
        errors.append("CadQuery sandbox must not run for mesh generate")
    if not mock_mesh.called:
        errors.append("generate_mesh_glb was not called")
    if session.last_script is not None:
        errors.append("last_script must be cleared on mesh sessions")
    if session.last_backend != "mesh":
        errors.append(f"last_backend should be mesh, got {session.last_backend}")
    if session.last_mesh_prompt != intent.mesh_prompt:
        errors.append("last_mesh_prompt should be stored")
    if not result.textured or result.backend != "mesh":
        errors.append(f"response flags textured/backend wrong: {result.textured} {result.backend}")
    if not result.rebuilt or result.model_id != "mesh123abc":
        errors.append("mesh generate should rebuild with new model_id")

    if errors:
        print("  [FAIL]")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print("  [ok] mesh generate skipped sandbox")
    return 1, 0


async def test_mesh_without_meshy_still_runs_factory():
    """No Meshy key still sculpts via three.ws; never falls back to CadQuery."""
    print("\n=== Test: Mesh without Meshy still uses factory ===")
    session = SessionState(session_id="test")
    intent = Intent(
        action="generate",
        backend="mesh",
        mesh_prompt="a dragon",
        script='import cadquery as cq\nresult = cq.Workplane("XY").box(10,10,5)',
        reply="Here's that sculpt.",
    )
    settings = _mock_settings()
    with patch("app.pipeline._execute_with_retry") as mock_cad:
        with patch("app.pipeline.generate_mesh_glb") as mock_mesh:
            mock_mesh.return_value = {
                "ok": True,
                "model_id": "free123",
                "glb_path": "/tmp/test_glb/free123.glb",
                "exec_ms": 800,
                "textured": True,
                "provider": "three_ws",
            }
            with patch("app.pipeline.synthesize_speech") as mock_tts:
                mock_tts.return_value = (None, 0.0)
                with patch("app.pipeline.save_session"):
                    ack = await apply_intent(intent, session, settings)
                    job = jobs.get(ack.job_id)
                    await job.task
                    result = job.result
    errors = []
    if mock_cad.called:
        errors.append("must not call CadQuery")
    if not mock_mesh.called:
        errors.append("factory should run without a Meshy key")
    if not result.rebuilt or result.backend != "mesh":
        errors.append(f"expected mesh rebuild, got {result.action} rebuilt={result.rebuilt}")
    if errors:
        print("  [FAIL]")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print("  [ok] three.ws factory runs without Meshy")
    return 1, 0


async def test_photo_falls_back_to_text_sculpt_before_cad():
    """When Forge photo engines are down, sculpt from a description — not CAD."""
    print("\n=== Test: Photo busy → text sculpt, not CAD ===")
    session = SessionState(session_id="test")
    settings = _mock_settings()
    settings.gemini_api_key = "test-key"
    photo = Path("/tmp/test_ref_pikachu.png")
    photo.write_bytes(b"\x89PNG\r\n" + b"x" * 64)

    with patch(
        "app.pipeline.generate_mesh_glb_from_image",
        new=AsyncMock(return_value={"ok": False, "error": "queued", "error_type": "busy"}),
    ):
        with patch(
            "ai.intent.mesh_prompt_from_photo",
            new=AsyncMock(return_value="a yellow Pikachu standing upright"),
        ):
            with patch("app.pipeline._execute_mesh") as mock_mesh:
                mock_mesh.return_value = (True, "sculpt99", None, True)
                with patch("app.pipeline._cad_from_photo") as mock_cad:
                    with patch("app.pipeline.synthesize_speech") as mock_tts:
                        mock_tts.return_value = (None, 0.0)
                        with patch("app.pipeline.save_session"):
                            result = await build_from_image(
                                "https://example.com/p.png",
                                session,
                                settings,
                                prompt="pikachu",
                                speak=False,
                                image_path=photo,
                            )
    errors = []
    if mock_cad.called:
        errors.append("must not fall through to CAD when text sculpt works")
    if not mock_mesh.called:
        errors.append("text sculpt should run")
    if not result.ok or result.backend != "mesh" or result.model_id != "sculpt99":
        errors.append(f"expected mesh sculpt, got ok={result.ok} backend={result.backend} id={result.model_id}")
    if errors:
        print("  [FAIL]")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print("  [ok] photo lane fails over to three.ws text sculpt")
    return 1, 0


async def test_cad_generate_still_uses_sandbox():
    """CAD generate must not call Meshy."""
    print("\n=== Test: CAD generate still uses sandbox ===")
    session = SessionState(session_id="test")
    intent = Intent(
        action="generate",
        backend="cad",
        script='import cadquery as cq\nresult = cq.Workplane("XY").box(10,10,5)',
        reply="Built a box.",
    )
    settings = _mock_settings()
    with patch("app.pipeline._execute_with_retry") as mock_cad:
        mock_cad.return_value = (True, "cadbox1", None)
        with patch("app.pipeline.generate_mesh_glb") as mock_mesh:
            with patch("app.pipeline.synthesize_speech") as mock_tts:
                mock_tts.return_value = (None, 0.0)
                with patch("app.pipeline.save_session"):
                    result = await apply_intent(intent, session, settings)
    errors = []
    if mock_mesh.called:
        errors.append("Meshy must not run for CAD generate")
    if not mock_cad.called:
        errors.append("sandbox should run")
    if result.textured or result.backend != "cad":
        errors.append(f"CAD response flags wrong: textured={result.textured} backend={result.backend}")
    if errors:
        print("  [FAIL]")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print("  [ok] CAD generate uses sandbox only")
    return 1, 0

# ============================================================================
# WS-A: project versions
# ============================================================================

def _version_settings():
    """Mock settings with real temp dirs so GLBs can actually move into projects."""
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="ws_a_pipe_"))
    settings = _mock_settings()
    settings.glb_dir = root / "glb"
    settings.projects_dir = root / "projects"
    settings.ref_dir = root / "ref"
    for d in (settings.glb_dir, settings.projects_dir, settings.ref_dir):
        d.mkdir(parents=True, exist_ok=True)
    return settings, root


def _fake_executor(cfg):
    """Stand-in for _execute_with_retry that writes a GLB like the sandbox does."""
    counter = {"n": 0}

    async def run(script, original_text, session, settings, latency, flatten_color=True):
        counter["n"] += 1
        model_id = f"built{counter['n']}"
        (cfg.glb_dir / f"{model_id}.glb").write_bytes(b"glTF" + bytes(20))
        session.template = None
        session.params = {}
        session.model_id = model_id
        session.glb_url = f"/media/glb/{model_id}.glb"
        session.last_script = script
        session.last_backend = "cad"
        session.last_mesh_prompt = None
        return True, model_id, None

    return run


async def _run_cad(intent, session, settings, transcript):
    with patch("app.pipeline._execute_with_retry", side_effect=_fake_executor_for(settings)), \
         patch("app.pipeline.synthesize_speech", AsyncMock(return_value=(None, 0.0))), \
         patch("app.pipeline.save_session"):
        return await apply_intent(intent, session, settings, transcript=transcript)


_EXECUTORS: dict = {}


def _fake_executor_for(settings):
    key = id(settings)
    if key not in _EXECUTORS:
        _EXECUTORS[key] = _fake_executor(settings)
    return _EXECUTORS[key]


def _report(name, errors):
    if errors:
        print(f"  [FAIL] {name}")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print(f"  [ok] {name}")
    return 1, 0


async def test_builds_record_project_versions():
    """Every rebuild lands in a project; follow-ups append, new objects start a project."""
    import shutil
    from app import projects

    print("\n=== Test: builds record project versions ===")
    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_a")
        r1 = await _run_cad(
            Intent(action="generate", script="S1", reply="A box."), session, settings, "build me a box"
        )
        pid = session.project_id
        if not pid:
            errors.append("session.project_id not set")
        else:
            if r1.model_id != f"{pid}-v1":
                errors.append(f"model_id {r1.model_id!r} != {pid}-v1")
            if r1.glb_url != f"/media/projects/{pid}/v1.glb":
                errors.append(f"glb_url {r1.glb_url!r}")
            if not (settings.projects_dir / pid / "v1.glb").is_file():
                errors.append("v1.glb not on disk")
            if session.version != 1 or not r1.rebuilt:
                errors.append(f"version={session.version} rebuilt={r1.rebuilt}")
            v1 = projects.get_version(settings, pid, 1)
            if not v1 or v1.script != "S1" or v1.summary != "A box." or v1.op != "generate":
                errors.append(f"v1 meta wrong: {v1}")

        r2 = await _run_cad(
            Intent(action="generate", script="S2", reply="Taller box."), session, settings, "make it taller"
        )
        if session.project_id != pid or r2.model_id != f"{pid}-v2":
            errors.append(f"follow-up did not append: {session.project_id} {r2.model_id}")
        v2 = projects.get_version(settings, pid, 2) if pid else None
        if not v2 or v2.parent != 1 or v2.script != "S2":
            errors.append(f"v2 meta wrong: {v2}")

        r3 = await _run_cad(
            Intent(action="set_material", params={"color": "#FF0000"}, reply="Red."),
            session, settings, "make it red",
        )
        v3 = projects.get_version(settings, pid, 3) if pid else None
        if r3.model_id != f"{pid}-v3" or not v3 or v3.op != "set_material" or v3.color != "#FF0000":
            errors.append(f"set_material not versioned: {r3.model_id} {v3}")

        await _run_cad(
            Intent(action="generate", script="R1", reply="A ring."), session, settings, "build me a ring"
        )
        if session.project_id == pid or session.version != 1:
            errors.append("new object should start a new project")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("builds record project versions", errors)


async def test_missing_glb_keeps_legacy_url():
    """No GLB on disk (mocked build) → nothing recorded, legacy ids untouched."""
    print("\n=== Test: missing GLB keeps legacy url ===")
    import shutil

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_a")
        with patch("app.pipeline._execute_with_retry", AsyncMock(return_value=(True, "ghost", None))), \
             patch("app.pipeline.synthesize_speech", AsyncMock(return_value=(None, 0.0))), \
             patch("app.pipeline.save_session"):
            r = await apply_intent(Intent(action="generate", script="x"), session, settings, transcript="build me a box")
        if r.model_id != "ghost" or session.project_id is not None:
            errors.append(f"model_id={r.model_id} project_id={session.project_id}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("missing GLB keeps legacy url", errors)


async def test_photo_build_records_project():
    print("\n=== Test: photo build records a mesh project ===")
    import shutil
    from app import projects

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_a")

        async def fake_image(image_url, output_dir, **kwargs):
            (Path(output_dir) / "photo1.glb").write_bytes(b"glTF" + bytes(20))
            return {"ok": True, "model_id": "photo1", "textured": True, "exec_ms": 1}

        with patch("app.pipeline.generate_mesh_glb_from_image", side_effect=fake_image), \
             patch("app.pipeline.synthesize_speech", AsyncMock(return_value=(None, 0.0))), \
             patch("app.pipeline.save_session"):
            r = await build_from_image("http://x/ref.png", session, settings, prompt="a mug")
        pid = session.project_id
        cur = projects.current_version(settings, pid) if pid else None
        if not cur or cur.kind != "mesh" or cur.op != "photo" or cur.mesh_prompt != "a mug":
            errors.append(f"photo version wrong: {cur}")
        if r.model_id != f"{pid}-v1" or r.glb_url != f"/media/projects/{pid}/v1.glb":
            errors.append(f"response ids {r.model_id} {r.glb_url}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("photo build records a mesh project", errors)


# ============================================================================
# Absolute size ("make it 8 cm tall") on sculpts
# ============================================================================

async def _apply_scale(intent, session, settings):
    with patch("app.pipeline.synthesize_speech") as mock_tts:
        mock_tts.return_value = (None, 0.0)
        with patch("app.pipeline.save_session"):
            return await apply_intent(intent, session, settings)


def _mesh_session(**kw):
    base = dict(
        session_id="test", last_backend="mesh", model_id="m1",
        glb_url="/media/glb/m1.glb", base_size_m=0.2, scale=1.0,
    )
    base.update(kw)
    return SessionState(**base)


async def test_absolute_size_sets_longest_edge():
    print("\n=== Test: absolute size sets the longest edge ===")
    session = _mesh_session()
    intent = Intent(action="set_scale", params={"target_m": 0.08, "axis": None},
                    reply="Made it 8 centimetres.", backend="mesh")
    result = await _apply_scale(intent, session, _mock_settings())
    errors = []
    if result.action != "set_scale":
        errors.append(f"action {result.action!r}")
    if result.rebuilt:
        errors.append("must not rebuild")
    if abs(session.scale - 0.4) > 1e-6:
        errors.append(f"scale {session.scale}")
    if abs((result.display_size_m or 0) - 0.08) > 1e-6:
        errors.append(f"display_size_m {result.display_size_m}")
    if errors:
        print("  ✗ FAILED: " + "; ".join(errors))
        return 0, 1
    print("  ✓ 8 cm target → scale 0.4, display 0.08 m")
    return 1, 0


async def test_absolute_size_uses_axis_from_glb():
    print("\n=== Test: absolute height reads the GLB bounds ===")
    import tempfile
    import trimesh

    with tempfile.TemporaryDirectory() as tmp:
        settings = _mock_settings()
        settings.glb_dir = Path(tmp)
        # Width 1, height 2, depth 4: height is half the longest edge.
        trimesh.creation.box(extents=(1.0, 2.0, 4.0)).export(str(Path(tmp) / "m1.glb"))
        session = _mesh_session()
        intent = Intent(action="set_scale", params={"target_m": 0.08, "axis": "height"},
                        reply="Made it 8 centimetres tall.", backend="mesh")
        result = await _apply_scale(intent, session, settings)
    errors = []
    if abs(session.scale - 0.8) > 1e-6:
        errors.append(f"scale {session.scale}")
    if abs((result.display_size_m or 0) - 0.16) > 1e-6:
        errors.append(f"display_size_m {result.display_size_m}")
    if errors:
        print("  ✗ FAILED: " + "; ".join(errors))
        return 0, 1
    print("  ✓ 8 cm tall on a 1×2×4 box → longest edge 16 cm")
    return 1, 0


async def test_absolute_size_never_scales_cad():
    print("\n=== Test: absolute size never display-scales CAD ===")
    session = _mesh_session(last_backend="cad", last_script="result = None")
    intent = Intent(action="set_scale", params={"target_m": 0.08, "axis": "height"},
                    reply="Made it 8 centimetres tall.")
    result = await _apply_scale(intent, session, _mock_settings())
    if result.action == "clarify" and session.scale == 1.0 and not result.rebuilt:
        print("  ✓ CAD refused with a spoken reason:", result.reply)
        return 1, 0
    print(f"  ✗ FAILED: action={result.action} scale={session.scale}")
    return 0, 1


async def test_absolute_size_clamps_and_says_so():
    print("\n=== Test: absolute size beyond the display range ===")
    session = _mesh_session()
    intent = Intent(action="set_scale", params={"target_m": 5.0, "axis": None},
                    reply="Made it 5 metres.", backend="mesh")
    result = await _apply_scale(intent, session, _mock_settings())
    ok = (
        abs((result.display_size_m or 0) - 2.0) < 1e-6
        and "as far as I can" in (result.reply or "")
    )
    if ok:
        print("  ✓ clamped to 2 m and said so")
        return 1, 0
    print(f"  ✗ FAILED: display={result.display_size_m} reply={result.reply!r}")
    return 0, 1


def run_all_tests():
    """Run all pipeline regression tests."""
    print("=" * 60)
    print("PIPELINE REGRESSION TESTS")
    print("(Color + Size/Geometry + Complex Free-rein)")
    print("=" * 60)
    
    total_pass = 0
    total_fail = 0
    
    # Run async tests
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # === BASIC TESTS ===
        print("\n--- Basic Follow-up Tests ---")
        
        # Color change tests
        p, f = loop.run_until_complete(test_color_change_with_script_returns_new_model())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_color_change_no_model_fails())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_color_change_rebuild_failure_returns_error())
        total_pass += p
        total_fail += f
        
        # Generate action tests
        p, f = loop.run_until_complete(test_generate_action_with_script_returns_new_model())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_generate_action_failure_returns_error())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_generate_action_no_script_returns_clarify())
        total_pass += p
        total_fail += f
        
        # === COMPLEX FREE-REIN TESTS ===
        print("\n--- Complex Free-rein Tests ---")
        
        # Sync test: verify scripts are sandbox-safe
        p, f = test_complex_scripts_are_sandbox_safe()
        total_pass += p
        total_fail += f
        
        # Async test: complex scripts return rebuilt
        p, f = loop.run_until_complete(test_complex_freerein_scripts_return_rebuilt())
        total_pass += p
        total_fail += f
        
        # Async test: follow-ups on complex script
        p, f = loop.run_until_complete(test_followups_on_complex_script())
        total_pass += p
        total_fail += f
        
        # Async test: no collapse to templates
        p, f = loop.run_until_complete(test_no_collapse_to_templates())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_generate_bakes_named_color_and_summary())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_mesh_generate_skips_sandbox_and_sets_session())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_mesh_without_meshy_still_runs_factory())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_photo_falls_back_to_text_sculpt_before_cad())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_cad_generate_still_uses_sandbox())
        total_pass += p
        total_fail += f

        # === WS-A: project versions ===
        print("\n--- Project Version Tests ---")
        for test in WS_A_TESTS:
            p, f = loop.run_until_complete(test())
            total_pass += p
            total_fail += f

        # === WS-E: CAD params wiring ===
        print("\n--- CAD Params Tests ---")
        for test in WS_E_TESTS:
            p, f = loop.run_until_complete(test())
            total_pass += p
            total_fail += f

        # === WS-FG: mesh boolean wiring ===
        print("\n--- Mesh Boolean Wiring Tests ---")
        for test in WS_FG_TESTS:
            p, f = loop.run_until_complete(test())
            total_pass += p
            total_fail += f

        p, f = loop.run_until_complete(test_absolute_size_sets_longest_edge())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_absolute_size_uses_axis_from_glb())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_absolute_size_never_scales_cad())
        total_pass += p
        total_fail += f

        p, f = loop.run_until_complete(test_absolute_size_clamps_and_says_so())
        total_pass += p
        total_fail += f
        
    finally:
        loop.close()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"TOTAL: {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


async def test_undo_redo_restores_session():
    print("\n=== Test: undo/redo restore follow-up context ===")
    import shutil
    from app.pipeline import history_step

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_a")
        await _run_cad(Intent(action="generate", script="S1", reply="A box."), session, settings, "build me a box")
        await _run_cad(Intent(action="generate", script="S2", reply="Taller."), session, settings, "make it taller")
        pid = session.project_id

        r = await _run_cad(Intent(action="undo", params={"steps": 1}, reply="Undone."), session, settings, "undo")
        if not (r.rebuilt and r.model_id == f"{pid}-v1" and r.glb_url == f"/media/projects/{pid}/v1.glb"):
            errors.append(f"undo response {r.rebuilt} {r.model_id} {r.glb_url}")
        if session.last_script != "S1" or session.last_summary != "A box." or session.version != 1:
            errors.append(f"undo did not restore: {session.last_script} {session.last_summary} {session.version}")
        if r.action != "undo" or not r.ok:
            errors.append(f"undo action {r.action} ok={r.ok}")

        again = await _run_cad(Intent(action="undo", params={"steps": 1}), session, settings, "undo")
        if again.rebuilt or again.action != "noop" or again.reply != "Nothing to undo." or not again.ok:
            errors.append(f"undo at v1: {again.action} {again.reply} rebuilt={again.rebuilt}")

        r = await _run_cad(Intent(action="redo", params={"steps": 1}, reply="Redone."), session, settings, "redo")
        if not r.rebuilt or r.model_id != f"{pid}-v2" or session.last_script != "S2":
            errors.append(f"redo {r.model_id} {session.last_script}")

        empty = SessionState(session_id="ws_a_empty")
        r = await _run_cad(Intent(action="undo", params={"steps": 1}), empty, settings, "undo")
        if r.rebuilt or r.action != "noop" or r.reply != "There's nothing to undo yet.":
            errors.append(f"no project: {r.action} {r.reply}")

        with patch("app.pipeline.synthesize_speech", AsyncMock(return_value=(None, 0.0))), \
             patch("app.pipeline.save_session"):
            r = await history_step(session, settings, "nope", "undo", 1)
            if r.ok or r.action != "clarify":
                errors.append(f"unknown project: ok={r.ok} action={r.action}")
            r = await history_step(session, settings, pid, "undo", 1)
            if not r.rebuilt or r.model_id != f"{pid}-v1":
                errors.append(f"history_step undo {r.model_id}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("undo/redo restore follow-up context", errors)


async def test_save_client_version():
    print("\n=== Test: client version save ===")
    import shutil
    from app import projects
    from app.pipeline import save_client_version

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_a")
        await _run_cad(Intent(action="generate", script="S1", reply="A box."), session, settings, "build me a box")
        await _run_cad(Intent(action="generate", script="S2", reply="Taller."), session, settings, "make it taller")
        pid = session.project_id
        with patch("app.pipeline.save_session"):
            r = await save_client_version(session, settings, pid, b"glTF" + bytes(40), "hand_edit", "Moved it.")
            if r.action != "version_saved" or r.rebuilt is not False or not r.ok:
                errors.append(f"response {r.action} rebuilt={r.rebuilt} ok={r.ok}")
            if r.model_id != f"{pid}-v3" or r.glb_url != f"/media/projects/{pid}/v3.glb":
                errors.append(f"ids {r.model_id} {r.glb_url}")
            if not (settings.projects_dir / pid / "v3.glb").is_file() or session.version != 3:
                errors.append("v3 not stored / session not moved")
            v3 = projects.get_version(settings, pid, 3)
            if not v3 or v3.op != "hand_edit" or v3.script != "S2" or v3.summary != "Moved it.":
                errors.append(f"v3 meta {v3}")

            bad = await save_client_version(session, settings, pid, b"not a glb at all", "hand_edit")
            if bad.ok or bad.action != "clarify" or projects.current_version(settings, pid).version != 3:
                errors.append(f"bad upload accepted: {bad.action}")

            odd = await save_client_version(session, settings, pid, b"glTF" + bytes(40), "rm -rf")
            if projects.get_version(settings, pid, 4).op != "hand_edit":
                errors.append("unknown op not normalised to hand_edit")
            del odd

            missing = await save_client_version(session, settings, "nope", b"glTF" + bytes(40), "hand_edit")
            if missing.ok or missing.action != "clarify":
                errors.append("unknown project accepted")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("client version save", errors)


WS_A_TESTS = [
    test_builds_record_project_versions,
    test_missing_glb_keeps_legacy_url,
    test_photo_build_records_project,
    test_undo_redo_restores_session,
    test_save_client_version,
]


# ============================================================================
# WS-E: CAD params wiring
# ============================================================================

_PARAM_SCRIPT = (
    'import cadquery as cq\n'
    'PARAMS = {"width_mm": 10, "height_mm": 5}\n'
    'result = cq.Workplane("XY").box(PARAMS["width_mm"], PARAMS["height_mm"], 2)\n'
)


def _fake_sandbox_exec(model_id="param_built_1"):
    """Stand-in for cad.sandbox.execute_cadquery_script: writes a fake GLB."""

    def run(script, output_dir, timeout=45.0, color="#C0C0C0", flatten_color=True):
        (Path(output_dir) / f"{model_id}.glb").write_bytes(b"glTF" + bytes(20))
        return {"ok": True, "model_id": model_id, "exec_ms": 1.0}

    return run


async def test_generate_records_and_returns_cad_params():
    """A CAD build with PARAMS records them on the version and echoes them back."""
    print("\n=== Test: generate records and returns cad_params ===")
    import shutil
    from app import projects

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_e")
        r = await _run_cad(
            Intent(action="generate", script=_PARAM_SCRIPT, reply="A box."),
            session, settings, "build me a box",
        )
        want = {"width_mm": 10.0, "height_mm": 5.0}
        if r.cad_params != want:
            errors.append(f"response cad_params {r.cad_params}")
        v1 = projects.get_version(settings, session.project_id, 1)
        if not v1 or v1.params != want:
            errors.append(f"v1.params {v1.params if v1 else None}")

        no_params = await _run_cad(
            Intent(action="generate", script="import cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)\n", reply="Plain."),
            session, settings, "build a plain box",
        )
        if no_params.cad_params != {}:
            errors.append(f"script without PARAMS should give {{}}: {no_params.cad_params}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("generate records and returns cad_params", errors)


async def test_param_update_rebuilds_and_versions():
    """A drag-release param update reruns the sandbox and appends a param_edit version."""
    print("\n=== Test: param update rebuilds and records a version ===")
    import shutil
    from app import projects
    from app.pipeline import apply_param_update

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_e")
        await _run_cad(
            Intent(action="generate", script=_PARAM_SCRIPT, reply="A box."),
            session, settings, "build me a box",
        )
        pid = session.project_id

        with patch("app.pipeline.execute_cadquery_script", side_effect=_fake_sandbox_exec()), \
             patch("app.pipeline.save_session"):
            r = await apply_param_update(session, settings, pid, {"width_mm": 20})

        if not r.ok or not r.rebuilt or r.action != "param_edit":
            errors.append(f"response ok={r.ok} rebuilt={r.rebuilt} action={r.action}")
        if r.model_id != f"{pid}-v2" or r.glb_url != f"/media/projects/{pid}/v2.glb":
            errors.append(f"ids {r.model_id} {r.glb_url}")
        want = {"width_mm": 20.0, "height_mm": 5.0}
        if r.cad_params != want:
            errors.append(f"cad_params {r.cad_params}")
        v2 = projects.get_version(settings, pid, 2)
        if not v2 or v2.op != "param_edit" or v2.params != want:
            errors.append(f"v2 meta {v2}")
        if not v2 or '"width_mm": 20' not in (v2.script or ""):
            errors.append(f"v2.script not rewritten: {v2.script if v2 else None}")
        if session.version != 2 or session.last_script != (v2.script if v2 else None):
            errors.append("session not moved to the new version")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("param update rebuilds and records a version", errors)


async def test_param_update_bad_value_clarifies_and_leaves_model():
    """An unknown dimension or a non-positive value comes back as a clarify, unchanged."""
    print("\n=== Test: param update bad value clarifies ===")
    import shutil
    from app import projects
    from app.pipeline import apply_param_update

    settings, root = _version_settings()
    errors = []
    try:
        session = SessionState(session_id="ws_e")
        await _run_cad(
            Intent(action="generate", script=_PARAM_SCRIPT, reply="A box."),
            session, settings, "build me a box",
        )
        pid = session.project_id

        with patch("app.pipeline.save_session"):
            unknown = await apply_param_update(session, settings, pid, {"depth_mm": 4})
            if unknown.ok or unknown.action != "clarify" or "no dimension called" not in unknown.reply:
                errors.append(f"unknown dimension: ok={unknown.ok} action={unknown.action} reply={unknown.reply!r}")

            negative = await apply_param_update(session, settings, pid, {"width_mm": -5})
            if negative.ok or negative.action != "clarify" or "positive number" not in negative.reply:
                errors.append(f"negative value: ok={negative.ok} reply={negative.reply!r}")

            if projects.current_version(settings, pid).version != 1:
                errors.append("a rejected update must not create a new version")

            missing = await apply_param_update(session, settings, "nope", {"width_mm": 5})
            if missing.ok or missing.action != "clarify":
                errors.append(f"unknown project: ok={missing.ok} action={missing.action}")

            no_script_session = SessionState(session_id="ws_e_mesh")
            mesh_r = await _execute_mesh_stub(no_script_session, settings)
            mesh_pid = no_script_session.project_id
            if mesh_pid:
                mesh_reply = await apply_param_update(no_script_session, settings, mesh_pid, {"a_mm": 1})
                if mesh_reply.ok or mesh_reply.action != "clarify":
                    errors.append(f"mesh project should clarify: {mesh_reply.action}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("param update bad value clarifies", errors)


async def _execute_mesh_stub(session, settings):
    """Records a mesh version directly (skips the real mesh generator)."""
    from app.pipeline import _record_version

    model_id = "mesh_built_1"
    (settings.glb_dir / f"{model_id}.glb").write_bytes(b"glTF" + bytes(20))
    session.last_backend = "mesh"
    session.last_script = None
    session.last_mesh_prompt = "a duck"
    session.model_id = model_id
    session.glb_url = f"/media/glb/{model_id}.glb"
    _record_version(session, settings, model_id, "generate", new_object=True)
    return session


WS_E_TESTS = [
    test_generate_records_and_returns_cad_params,
    test_param_update_rebuilds_and_versions,
    test_param_update_bad_value_clarifies_and_leaves_model,
]


# ============================================================================
# WS-FG: mesh_boolean wiring (hole / loop / flat base)
# ============================================================================


def _boolean_mesh_session(settings, **kw):
    """A mesh session pointing at a real box GLB in settings.glb_dir/m1.glb."""
    import trimesh

    trimesh.creation.box(extents=(1.0, 1.0, 1.0)).export(str(Path(settings.glb_dir) / "m1.glb"))
    base = dict(
        session_id="ws_fg", last_backend="mesh", model_id="m1",
        glb_url="/media/glb/m1.glb", base_size_m=0.2, scale=1.0,
    )
    base.update(kw)
    return SessionState(**base)


async def _run_boolean(intent, session, settings):
    with patch("app.pipeline.synthesize_speech", AsyncMock(return_value=(None, 0.0))), \
         patch("app.pipeline.save_session"):
        return await apply_intent(intent, session, settings, transcript="mesh boolean")


async def test_mesh_boolean_hole_drills_through():
    print("\n=== Test: mesh_boolean drills a hole ===")
    import shutil

    settings, root = _version_settings()
    try:
        session = _boolean_mesh_session(settings)
        intent = Intent(
            action="mesh_boolean", backend="mesh",
            params={"op": "hole", "center": [0.5, 0.0, 0.0], "normal": [1.0, 0.0, 0.0]},
            reply="Drilling that hole.",
        )
        result = await _run_boolean(intent, session, settings)
        errors = []
        if not result.rebuilt or not result.ok:
            errors.append(f"rebuilt={result.rebuilt} ok={result.ok} error={result.error}")
        if result.backend != "mesh":
            errors.append(f"backend {result.backend}")
        if result.model_id is None or result.model_id == "m1":
            errors.append(f"model_id did not change: {result.model_id}")
        if result.reply != "Drilled it.":
            errors.append(f"reply {result.reply!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("mesh_boolean drills a hole", errors)


async def test_mesh_boolean_flat_base_needs_no_selection():
    print("\n=== Test: mesh_boolean flattens the base with no selection ===")
    import shutil

    settings, root = _version_settings()
    try:
        session = _boolean_mesh_session(settings)
        intent = Intent(action="mesh_boolean", backend="mesh", params={"op": "flat_base"}, reply="Flattening the base.")
        result = await _run_boolean(intent, session, settings)
        errors = []
        if not result.rebuilt or not result.ok:
            errors.append(f"rebuilt={result.rebuilt} ok={result.ok} error={result.error}")
        if result.reply != "Flattened the base.":
            errors.append(f"reply {result.reply!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("mesh_boolean flattens the base", errors)


async def test_mesh_boolean_error_leaves_model_unchanged():
    print("\n=== Test: a BooleanError leaves the model unchanged ===")
    import shutil

    settings, root = _version_settings()
    try:
        session = _boolean_mesh_session(settings)
        # diameter_mm=0 is invalid (mesh/boolean.py: MSG_HOLE_SIZE) and never reaches manifold.
        intent = Intent(
            action="mesh_boolean", backend="mesh",
            params={"op": "hole", "center": [0.5, 0.0, 0.0], "normal": [1.0, 0.0, 0.0], "diameter_mm": 0},
            reply="Drilling that hole.",
        )
        result = await _run_boolean(intent, session, settings)
        errors = []
        if result.rebuilt or result.ok or result.action != "clarify":
            errors.append(f"rebuilt={result.rebuilt} ok={result.ok} action={result.action}")
        if "bigger than zero" not in (result.reply or ""):
            errors.append(f"reply not the boolean error: {result.reply!r}")
        if session.model_id != "m1" or session.glb_url != "/media/glb/m1.glb":
            errors.append(f"session moved on failure: {session.model_id} {session.glb_url}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("BooleanError leaves the model unchanged", errors)


async def test_mesh_boolean_requires_mesh_session():
    print("\n=== Test: mesh_boolean refuses a CAD session ===")
    import shutil

    settings, root = _version_settings()
    try:
        session = _boolean_mesh_session(settings, last_backend="cad", last_script="result = 1")
        intent = Intent(action="mesh_boolean", backend="mesh", params={"op": "flat_base"}, reply="Flattening the base.")
        result = await _run_boolean(intent, session, settings)
        errors = []
        if result.rebuilt or result.action != "clarify" or "no sculpt" not in (result.reply or "").lower():
            errors.append(f"rebuilt={result.rebuilt} action={result.action} reply={result.reply!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("mesh_boolean refuses a CAD session", errors)


async def test_mesh_boolean_missing_glb_clarifies():
    print("\n=== Test: mesh_boolean with no GLB on disk clarifies ===")
    import shutil

    settings, root = _version_settings()
    try:
        session = SessionState(
            session_id="ws_fg", last_backend="mesh", model_id="ghost",
            glb_url="/media/glb/ghost.glb", base_size_m=0.2, scale=1.0,
        )
        intent = Intent(action="mesh_boolean", backend="mesh", params={"op": "flat_base"}, reply="Flattening the base.")
        result = await _run_boolean(intent, session, settings)
        errors = []
        if result.rebuilt or result.action != "clarify":
            errors.append(f"rebuilt={result.rebuilt} action={result.action}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("mesh_boolean with missing GLB clarifies", errors)


async def test_mesh_boolean_records_project_version():
    print("\n=== Test: mesh_boolean appends a project version op=boolean ===")
    import shutil
    from app import projects

    settings, root = _version_settings()
    errors = []
    try:
        import trimesh

        from app.pipeline import restore_version

        v1_src = settings.ref_dir / "seed.glb"
        trimesh.creation.box(extents=(1.0, 1.0, 1.0)).export(str(v1_src))
        info = projects.create_project(
            settings, "mesh", v1_src, op="generate", summary="A sculpt.",
            mesh_prompt="a cube", color="#FFFFFF", base_size_m=0.2,
        )
        session = SessionState(session_id="ws_fg")
        with patch("app.pipeline.save_session"):
            restore_version(session, info)

        intent = Intent(action="mesh_boolean", backend="mesh", params={"op": "flat_base"}, reply="Flattening the base.")
        result = await _run_boolean(intent, session, settings)
        if not result.rebuilt or not result.ok:
            errors.append(f"rebuilt={result.rebuilt} ok={result.ok} error={result.error}")
        pid = session.project_id
        v2 = projects.get_version(settings, pid, 2) if pid else None
        if not v2 or v2.op != "boolean":
            errors.append(f"v2 {v2}")
        if result.model_id != f"{pid}-v2":
            errors.append(f"model_id {result.model_id} != {pid}-v2")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _report("mesh_boolean appends a project version", errors)


WS_FG_TESTS = [
    test_mesh_boolean_hole_drills_through,
    test_mesh_boolean_flat_base_needs_no_selection,
    test_mesh_boolean_error_leaves_model_unchanged,
    test_mesh_boolean_requires_mesh_session,
    test_mesh_boolean_missing_glb_clarifies,
    test_mesh_boolean_records_project_version,
]


if __name__ == "__main__":
    sys.exit(run_all_tests())
