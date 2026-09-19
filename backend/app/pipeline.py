"""Apply intents: codegen → sandbox execution → retry on failure."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ai.intent import _is_new_object_request, extract_named_color
from app import projects
from app.config import Settings
from app.models import CommandResponse, Intent, SessionState, VersionInfo
from app.session import save_session
from cad import DEFAULTS
from cad.params import ParamError
from cad.params import extract_params as extract_cad_params
from cad.params import set_params as set_cad_params
from cad.sandbox import execute_cadquery_script
from cad.builder import merge_params, build_model
from mesh.factory import (
    generate_mesh_glb,
    generate_mesh_glb_from_image,
    mesh_ready,
)
from photos.drive import download_file, list_images
from photos.search import find_photos
from photos.stage import stage_photo
from voice.speech import synthesize_speech

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

# CadQuery models carry real millimetre dimensions, so they are shown life size.
# The clamps only stop a stray script from producing something invisible or
# room-filling in AR.
CAD_MIN_M = 0.04
CAD_MAX_M = 1.00
# Mesh output is unit-normalised, so a sculpt has no real size of its own.
MESH_DEFAULT_M = 0.20
SIZE_MIN_M = 0.03
SIZE_MAX_M = 2.00


def _glb_url(settings: Settings, model_id: str) -> str:
    return f"/media/glb/{model_id}.glb"


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _cad_size_m(settings: Settings, model_id: str) -> float:
    """
    Longest real dimension of a CadQuery GLB, in metres.

    The sandbox already converts mm to metres on export, so this reads straight
    off the bounds — the script's real dimensions survive into the headset.
    """
    try:
        import trimesh

        scene = trimesh.load(str(settings.glb_dir / f"{model_id}.glb"))
        lo, hi = scene.bounds
        longest_m = float(max(hi - lo))
    except Exception as exc:
        logger.info("Could not measure %s: %s", model_id, exc)
        return MESH_DEFAULT_M
    if longest_m <= 0:
        return MESH_DEFAULT_M
    return _clamp(longest_m, CAD_MIN_M, CAD_MAX_M)


def _mesh_size_m(size_mm: float | None) -> float:
    if not size_mm or size_mm <= 0:
        return MESH_DEFAULT_M
    return _clamp(float(size_mm) / 1000.0, SIZE_MIN_M, SIZE_MAX_M)


def _display_size_m(session: SessionState) -> float:
    return _clamp(session.base_size_m * session.scale, SIZE_MIN_M, SIZE_MAX_M)


# Which VersionInfo.op a rebuilding action records.
_VERSION_OPS = {
    "generate": "generate",
    "set_material": "set_material",
    "execute_script": "script",
    "create": "generate",
    "modify": "generate",
}


def _versioned_model_id(info: VersionInfo) -> str:
    return f"{info.project_id}-v{info.version}"


def _cad_params(session: SessionState) -> dict[str, float]:
    """The session's current CAD script's named dimensions, for CommandResponse.cad_params."""
    if session.last_backend != "cad" or not session.last_script:
        return {}
    return extract_cad_params(session.last_script)


def _record_version(
    session: SessionState,
    settings: Settings,
    model_id: str | None,
    op: str,
    new_object: bool = False,
) -> str | None:
    """
    Move a freshly built GLB into the session's project as a new version.

    A new object (or a lane change, or no live project) starts a project;
    anything else appends. On success the session points at the version file
    and the versioned model_id is returned — always new, so the client swaps.
    Best effort: if the GLB is not on disk or anything fails, returns None and
    the legacy /media/glb URL stays in place.
    """
    if not model_id:
        return None
    try:
        src = Path(settings.glb_dir) / f"{model_id}.glb"
        if not src.is_file():
            return None
        kind = session.last_backend if session.last_backend in ("cad", "mesh") else "cad"
        meta = {
            "op": op,
            "summary": session.last_summary,
            "script": session.last_script if kind == "cad" else None,
            "params": extract_cad_params(session.last_script) if kind == "cad" and session.last_script else {},
            "mesh_prompt": session.last_mesh_prompt if kind == "mesh" else None,
            "color": session.color,
            "base_size_m": session.base_size_m,
        }
        info = None
        if not new_object and session.project_id:
            current = projects.current_version(settings, session.project_id)
            if current is not None and current.kind == kind:
                info = projects.append_version(settings, session.project_id, src, **meta)
        if info is None:
            info = projects.create_project(settings, kind, src, **meta)
    except Exception as exc:
        logger.warning("Could not record a version for %s: %s", model_id, exc)
        return None
    session.project_id = info.project_id
    session.version = info.version
    session.model_id = _versioned_model_id(info)
    session.glb_url = info.glb_url
    save_session(session)
    return session.model_id


def restore_version(session: SessionState, info: VersionInfo) -> None:
    """Point the session at a stored version and restore its follow-up context."""
    session.project_id = info.project_id
    session.version = info.version
    session.model_id = _versioned_model_id(info)
    session.glb_url = info.glb_url
    session.template = None
    session.params = {}
    session.last_backend = info.kind
    session.last_script = info.script
    session.last_summary = info.summary
    session.last_mesh_prompt = info.mesh_prompt
    if info.color:
        session.color = info.color
    if info.base_size_m:
        session.base_size_m = info.base_size_m
    save_session(session)


_AXIS_INDEX = {"width": 0, "height": 1, "depth": 2}  # GLB is Y-up


def _glb_path_from_url(settings: Settings, glb_url: str | None) -> Path | None:
    """Local file behind a served GLB URL, or None when it cannot be resolved."""
    if not glb_url:
        return None
    path = glb_url.split("?", 1)[0]
    if path.startswith("/media/glb/"):
        return Path(settings.glb_dir) / path[len("/media/glb/"):]
    projects_dir = getattr(settings, "projects_dir", None)
    if path.startswith("/media/projects/") and isinstance(projects_dir, Path):
        return projects_dir / path[len("/media/projects/"):]
    return None


def _axis_fraction(glb_path: Path | None, axis: str | None) -> float:
    """
    Extent along `axis` as a fraction of the longest extent (1.0 if unknown).

    base_size_m is the longest edge, so "8 cm tall" on a wide model needs this
    ratio to land the height, not the width, on 8 cm.
    """
    idx = _AXIS_INDEX.get(axis or "")
    if idx is None or glb_path is None:
        return 1.0
    try:
        import trimesh

        lo, hi = trimesh.load(str(glb_path)).bounds
        extents = hi - lo
        longest = float(max(extents))
        part = float(extents[idx])
    except Exception as exc:
        logger.info("Could not measure %s for axis %s: %s", glb_path, axis, exc)
        return 1.0
    if longest <= 0 or part <= 0:
        return 1.0
    return part / longest


_CAD_ABSOLUTE_SIZE_REPLY = (
    "I keep CAD parts at their real size. Ask me to change the dimension "
    "and I'll rebuild it."
)
_SIZE_CLAMPED_REPLY = "That's outside what I can show, so I went as far as I can."


async def _execute_with_retry(
    script: str,
    original_text: str,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
    flatten_color: bool = True,
) -> tuple[bool, str | None, str | None]:
    """
    Execute CadQuery script in sandbox with retry loop on failure.
    
    Uses Tabish's canonical entrypoint: execute_cadquery_script()
    Returns dict: {ok, glb_path, model_id, exec_ms} or {ok=False, error, error_type}
    
    Returns (success, model_id_or_none, error_or_none).
    """
    from ai.intent import repair_and_retry

    color = session.color or "#C0C0C0"
    current_script = script
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        result = execute_cadquery_script(
            script=current_script,
            output_dir=settings.glb_dir,
            timeout=45.0,
            color=color,
            flatten_color=flatten_color,
        )
        latency[f"cad_ms_attempt_{attempt}"] = result.get("exec_ms", 0)

        if result["ok"]:
            latency["cad_ms"] = result.get("exec_ms", 0)
            session.template = None
            session.params = {}
            session.model_id = result["model_id"]
            session.glb_url = _glb_url(settings, result["model_id"])
            session.last_script = current_script
            session.last_backend = "cad"
            session.last_mesh_prompt = None
            session.base_size_m = _cad_size_m(settings, result["model_id"])
            save_session(session)
            return True, result["model_id"], None

        last_error = result.get("error", "Unknown error")
        error_type = result.get("error_type", "execution")
        logger.warning("Sandbox exec failed (attempt %d): [%s] %s", attempt + 1, error_type, last_error)

        if error_type == "security":
            return False, None, f"Security violation: {last_error}"

        if attempt < MAX_RETRIES:
            logger.info("Attempting repair via LLM...")
            repair_intent = await repair_and_retry(
                original_text=original_text,
                failed_script=current_script,
                error=last_error,
                settings=settings,
                max_retries=1,
            )
            if repair_intent.action == "generate" and repair_intent.script:
                current_script = repair_intent.script
                logger.info("Retrying with repaired script")
            else:
                break

    return False, None, last_error


async def _execute_mesh(
    prompt: str,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
    size_mm: float | None = None,
    use_nvidia: bool = True,
) -> tuple[bool, str | None, str | None, bool]:
    """Text-to-3D via three.ws / NVIDIA / Meshy. Never falls back to CadQuery."""
    nvidia_key = getattr(settings, "nvidia_api_key", "") or ""
    result = await generate_mesh_glb(
        prompt=prompt,
        output_dir=settings.glb_dir,
        meshy_api_key=settings.meshy_api_key or "",
        nvidia_api_key=nvidia_key if use_nvidia else "",
        three_ws=bool(getattr(settings, "three_ws_enabled", True)),
        timeout_s=150.0,
    )
    latency["mesh_ms"] = result.get("exec_ms", 0)
    if not result.get("ok"):
        return False, None, result.get("error", "Mesh generation failed"), False

    session.template = None
    session.params = {}
    session.model_id = result["model_id"]
    session.glb_url = _glb_url(settings, result["model_id"])
    session.last_script = None
    session.last_backend = "mesh"
    session.last_mesh_prompt = prompt
    session.base_size_m = _mesh_size_m(size_mm)
    save_session(session)
    return True, result["model_id"], None, bool(result.get("textured", True))


async def apply_intent(
    intent: Intent,
    session: SessionState,
    settings: Settings,
    transcript: str | None = None,
    extra_latency: dict[str, float] | None = None,
) -> CommandResponse:
    """
    Apply an Intent to session state.
    
    Handles:
    - generate + backend=mesh: Meshy text-to-3D (no CadQuery)
    - generate + backend=cad: CadQuery script via sandbox (with retry loop)
    - set_material: CAD rebuild, or mesh re-gen with color in the prompt
    - create/modify: Legacy template path (kept for backward compat)
    - clarify/noop: No CAD action
    
    IMPORTANT: For any action that changes the model (generate, set_material, modify),
    we MUST return a new model_id and glb_url so the client can swap the mesh.
    A "successful" color change with no new asset would be a client-invisible no-op.
    """
    latency: dict[str, float] = dict(extra_latency or {})
    rebuilt = False
    action = intent.action
    error_msg = None
    result_model_id: str | None = None
    textured = False
    response_backend = intent.backend or session.last_backend or "cad"
    candidates: list[dict] = []

    if action == "find_photos":
        response_backend = "mesh"
        try:
            files = await list_images(settings)
        except Exception as exc:
            logger.warning("Drive list failed: %s", exc)
            error_msg = str(exc)
            intent.reply = "I couldn't reach your photos."
            action = "clarify"
            files = []
        if files:
            query = (intent.photo_query or "").strip() or (transcript or "").strip()
            staged: list[dict] = []
            thumbs: dict[str, bytes] = {}
            for item in files:
                try:
                    raw, mime = await download_file(item["id"], settings)
                except Exception as exc:
                    logger.warning("Drive download %s failed: %s", item.get("id"), exc)
                    continue
                thumbs[item["id"]] = raw[: min(len(raw), 400_000)]
                info = stage_photo(
                    raw,
                    settings,
                    mime=mime,
                    name=item.get("name") or "",
                    file_id=item["id"],
                )
                staged.append(
                    {
                        "id": item["id"],
                        "name": item.get("name") or "photo",
                        "preview_url": info["preview_url"],
                        "image_url": info["preview_url"],
                        "build_url": info["build_url"],
                    }
                )
            matches = await find_photos(query, files, settings, thumbs)
            match_ids = {m["id"] for m in matches}
            candidates = [s for s in staged if s["id"] in match_ids] or staged
            session.last_photos = candidates
            save_session(session)
            n = len(candidates)
            if n == 0:
                intent.reply = "I didn't find that in your photos."
                action = "clarify"
            elif n == 1:
                intent.reply = "Found it. Pinch to confirm."
            else:
                intent.reply = f"Found {n}. Pinch to pick one."

    elif action == "generate" and (intent.backend or "cad") == "mesh":
        prompt = (intent.mesh_prompt or "").strip()
        if not prompt:
            intent.reply = "No mesh prompt was generated."
            action = "clarify"
            response_backend = "mesh"
        elif not mesh_ready(settings):
            intent.reply = (
                "Mesh generation isn't configured. "
                "three.ws should work with no key; or set NVIDIA_API_KEY / MESHY_API_KEY."
            )
            action = "clarify"
            response_backend = "mesh"
        else:
            session.scale = 1.0
            success, model_id, error, _mesh_textured = await _execute_mesh(
                prompt, session, settings, latency, size_mm=intent.size_mm
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                textured = True
                response_backend = "mesh"
                session.last_summary = intent.reply
                save_session(session)
            else:
                error_msg = error
                intent.reply = f"Sculpt failed: {error}"
                action = "clarify"
                response_backend = "mesh"

    elif action == "set_scale":
        # Resizing a sculpt is a display change, not a reason to spend 90s
        # rebuilding a model that would come back looking different anyway.
        if not session.model_id:
            error_msg = "No model to resize. Build something first."
            intent.reply = error_msg
            action = "clarify"
        elif intent.params.get("target_m") is not None and session.last_backend != "mesh":
            # CAD is shown life size; a display scale would make its mm lie.
            intent.reply = _CAD_ABSOLUTE_SIZE_REPLY
            action = "clarify"
        else:
            low = SIZE_MIN_M / session.base_size_m
            high = SIZE_MAX_M / session.base_size_m
            target_m = intent.params.get("target_m")
            if target_m is not None:
                fraction = _axis_fraction(
                    _glb_path_from_url(settings, session.glb_url),
                    intent.params.get("axis"),
                )
                wanted = float(target_m) / (session.base_size_m * fraction)
            else:
                wanted = session.scale * float(intent.params.get("factor") or 1.0)
            session.scale = _clamp(wanted, low, high)
            if target_m is not None and abs(session.scale - wanted) > 1e-9:
                intent.reply = _SIZE_CLAMPED_REPLY
            response_backend = session.last_backend
            save_session(session)

    elif action in ("undo", "redo"):
        # History steps reload a stored version through the normal swap path.
        project_id = intent.params.get("project_id") or session.project_id
        try:
            steps = max(1, int(intent.params.get("steps") or 1))
        except (TypeError, ValueError):
            steps = 1
        mover = projects.undo if action == "undo" else projects.redo
        info = mover(settings, project_id, steps) if project_id else None
        if info is None:
            if not project_id or projects.current_version(settings, project_id) is None:
                intent.reply = f"There's nothing to {action} yet."
            else:
                intent.reply = f"Nothing to {action}."
            action = "noop"
            response_backend = session.last_backend
        else:
            restore_version(session, info)
            rebuilt = True
            result_model_id = session.model_id
            response_backend = info.kind
            textured = info.kind == "mesh"
            intent.reply = "Undone." if action == "undo" else "Redone."

    elif action == "generate":
        if not intent.script:
            intent.reply = "No code was generated."
            action = "clarify"
        else:
            session.scale = 1.0
            named = extract_named_color(transcript or "")
            if named:
                session.color = named
                session.params["color"] = named
            success, model_id, error = await _execute_with_retry(
                script=intent.script,
                original_text=transcript or "",
                session=session,
                settings=settings,
                latency=latency,
                flatten_color=False,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                response_backend = "cad"
                session.last_summary = intent.reply
                save_session(session)
            else:
                error_msg = error
                intent.reply = f"Build failed: {error}"
                action = "clarify"

    elif action == "execute_script":
        if not intent.script:
            intent.reply = "No script provided."
            action = "clarify"
        else:
            success, model_id, error = await _execute_with_retry(
                script=intent.script,
                original_text=transcript or "",
                session=session,
                settings=settings,
                latency=latency,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                session.last_summary = intent.reply
                save_session(session)
            else:
                error_msg = error
                intent.reply = f"Script failed: {error}"
                action = "clarify"

    elif action == "create":
        template = intent.template
        if template and template in DEFAULTS:
            params = merge_params(template, intent.params)
            color = str(params.get("color", session.color or "#C0C0C0"))
            try:
                model_id, _, build_ms = build_model(template, params, settings)
                latency["cad_ms"] = build_ms
                session.template = template
                session.params = params
                session.color = color
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                session.last_script = None
                session.last_backend = "cad"
                session.last_mesh_prompt = None
                rebuilt = True
                result_model_id = model_id
                save_session(session)
            except Exception as e:
                logger.exception("Legacy build failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Build failed: {e}"
                action = "clarify"
        else:
            intent.reply = f"Unknown template '{template}'."
            action = "clarify"

    elif action == "modify":
        if not session.template or session.template not in DEFAULTS:
            intent.reply = "No model to modify. Say what you'd like to build."
            action = "clarify"
        else:
            params = merge_params(session.template, {**session.params, **intent.params})
            params["color"] = session.color
            try:
                model_id, _, build_ms = build_model(session.template, params, settings)
                latency["cad_ms"] = build_ms
                session.params = params
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                rebuilt = True
                result_model_id = model_id
                save_session(session)
            except Exception as e:
                logger.exception("Modify failed: %s", e)
                error_msg = str(e)
                intent.reply = f"Modification failed: {e}"
                action = "clarify"

    elif action == "set_material":
        color = str(intent.params.get("color", session.color))
        session.color = color
        session.params["color"] = color

        rebuild_attempted = False

        if session.last_backend == "mesh" and session.last_mesh_prompt:
            rebuild_attempted = True
            if not mesh_ready(settings):
                error_msg = "Mesh generation isn't configured."
                intent.reply = (
                    "Mesh generation isn't configured. "
                    "three.ws should work with no key; or set NVIDIA_API_KEY / MESHY_API_KEY."
                )
                action = "clarify"
                response_backend = "mesh"
            else:
                prompt = f"{session.last_mesh_prompt}, overall color {color}"
                success, model_id, error, _tex = await _execute_mesh(
                    prompt, session, settings, latency
                )
                if success:
                    rebuilt = True
                    result_model_id = model_id
                    textured = True
                    response_backend = "mesh"
                    session.color = color
                    session.params["color"] = color
                else:
                    error_msg = f"Color change failed: {error}"
                    intent.reply = f"Couldn't apply color: {error}"
                    response_backend = "mesh"
        elif session.last_script:
            rebuild_attempted = True
            success, model_id, error = await _execute_with_retry(
                script=session.last_script,
                original_text="rebuild with new color",
                session=session,
                settings=settings,
                latency=latency,
                flatten_color=True,
            )
            if success:
                rebuilt = True
                result_model_id = model_id
                response_backend = "cad"
            else:
                error_msg = f"Color change failed: {error}"
                intent.reply = f"Couldn't apply color: {error}"
        elif session.template and session.template in DEFAULTS:
            rebuild_attempted = True
            try:
                params = merge_params(session.template, session.params)
                model_id, _, build_ms = build_model(session.template, params, settings)
                latency["cad_ms"] = build_ms
                session.model_id = model_id
                session.glb_url = _glb_url(settings, model_id)
                session.last_backend = "cad"
                rebuilt = True
                result_model_id = model_id
                response_backend = "cad"
            except Exception as e:
                logger.warning("Rebuild for color failed: %s", e)
                error_msg = f"Color change failed: {e}"
                intent.reply = f"Couldn't apply color: {e}"

        if not rebuild_attempted:
            error_msg = "No model to apply color to. Build something first."
            intent.reply = error_msg

        save_session(session)

    if rebuilt and result_model_id and action in _VERSION_OPS:
        new_object = action == "create" or (
            action == "generate" and _is_new_object_request(transcript or "")
        )
        versioned = _record_version(
            session, settings, result_model_id, _VERSION_OPS[action], new_object=new_object
        )
        if versioned:
            result_model_id = versioned

    t0 = time.perf_counter()
    audio_url, tts_ms = await synthesize_speech(intent.reply, settings)
    latency["tts_ms"] = tts_ms if tts_ms else (time.perf_counter() - t0) * 1000

    # Always include model_id when rebuilt is True
    # Client uses fresh model_id + glb_url to know it needs to swap the mesh
    response_model_id = result_model_id if rebuilt else session.model_id

    return CommandResponse(
        ok=error_msg is None,
        transcript=transcript,
        reply=intent.reply,
        action=action,
        rebuilt=rebuilt,
        color=session.color,
        glb_url=session.glb_url,
        model_id=response_model_id,
        reply_audio_url=audio_url,
        session=session,
        latency_ms=latency,
        error=error_msg,
        textured=textured,
        backend=response_backend,
        display_size_m=_display_size_m(session),
        candidates=candidates if action == "find_photos" else [],
        cad_params=_cad_params(session),
    )


_CLIENT_OPS = {
    "generate", "set_material", "hand_edit", "param_edit",
    "boolean", "semantic_edit", "photo", "script",
}
_NO_HISTORY_REPLY = "I can't find that model's history."


async def history_step(
    session: SessionState,
    settings: Settings,
    project_id: str,
    direction: str,
    steps: int = 1,
) -> CommandResponse:
    """Undo/redo for a named project (the HTTP route). Voice goes via apply_intent."""
    if projects.current_version(settings, project_id) is None:
        return CommandResponse(
            ok=False,
            reply=_NO_HISTORY_REPLY,
            action="clarify",
            session=session,
            error=f"Unknown project {project_id}",
        )
    intent = Intent(
        action="redo" if direction == "redo" else "undo",
        params={"steps": steps, "project_id": project_id},
    )
    return await apply_intent(intent, session, settings)


async def save_client_version(
    session: SessionState,
    settings: Settings,
    project_id: str,
    glb_bytes: bytes,
    op: str = "hand_edit",
    summary: str | None = None,
) -> CommandResponse:
    """
    Store a GLB the client already shows (a hand edit) as the next version.

    Returns rebuilt=False with action="version_saved": the client has the
    geometry already, so it must only move its version pointer, not reload.
    Silent on purpose — this fires on every drag release.
    """
    import tempfile

    if projects.current_version(settings, project_id) is None:
        return CommandResponse(
            ok=False,
            reply=_NO_HISTORY_REPLY,
            action="clarify",
            session=session,
            error=f"Unknown project {project_id}",
        )
    if len(glb_bytes) < 12 or glb_bytes[:4] != b"glTF":
        return CommandResponse(
            ok=False,
            reply="That edit didn't save. Try again.",
            action="clarify",
            session=session,
            error="Upload is not a binary glTF (.glb) file",
        )
    fd, tmp = tempfile.mkstemp(dir=str(settings.projects_dir), suffix=".glb")
    try:
        with open(fd, "wb") as fh:
            fh.write(glb_bytes)
        meta: dict = {"op": op if op in _CLIENT_OPS else "hand_edit"}
        if summary:
            meta["summary"] = summary
        info = projects.append_version(settings, project_id, Path(tmp), **meta)
    finally:
        Path(tmp).unlink(missing_ok=True)
    restore_version(session, info)
    return CommandResponse(
        ok=True,
        reply="Saved.",
        action="version_saved",
        rebuilt=False,
        color=session.color,
        glb_url=session.glb_url,
        model_id=session.model_id,
        session=session,
        backend=info.kind,
        textured=info.kind == "mesh",
        display_size_m=_display_size_m(session),
        cad_params=_cad_params(session),
    )


_NO_PARAMS_REPLY = "This model has no named dimensions I can change."


async def apply_param_update(
    session: SessionState,
    settings: Settings,
    project_id: str,
    updates: dict[str, float],
) -> CommandResponse:
    """
    Drag-release from the client's dimension panel: rewrite named PARAMS in the
    project's current CAD script and rerun the sandbox. No LLM involved.

    Returns rebuilt=True with a fresh model_id/glb_url on success, same
    contract as any other rebuild. A ParamError (unknown dimension, or a value
    that isn't a positive number) comes back as a clarify with its message —
    the model is left untouched.
    """
    current = projects.current_version(settings, project_id)
    if current is None:
        return CommandResponse(
            ok=False,
            reply=_NO_HISTORY_REPLY,
            action="clarify",
            session=session,
            error=f"Unknown project {project_id}",
        )
    if current.kind != "cad" or not current.script:
        return CommandResponse(
            ok=False,
            reply=_NO_PARAMS_REPLY,
            action="clarify",
            session=session,
            error="Not a CAD project, or it has no stored script",
        )

    try:
        new_script = set_cad_params(current.script, updates)
    except ParamError as err:
        return CommandResponse(
            ok=False,
            reply=str(err),
            action="clarify",
            session=session,
            error=str(err),
        )

    latency: dict[str, float] = {}
    color = current.color or session.color or "#C0C0C0"
    t0 = time.perf_counter()
    result = execute_cadquery_script(
        script=new_script,
        output_dir=settings.glb_dir,
        timeout=45.0,
        color=color,
        flatten_color=True,
    )
    latency["cad_ms"] = result.get("exec_ms", (time.perf_counter() - t0) * 1000)
    if not result.get("ok"):
        return CommandResponse(
            ok=False,
            reply="That change didn't build. Try a different value.",
            action="clarify",
            session=session,
            error=result.get("error", "sandbox execution failed"),
            latency_ms=latency,
        )

    model_id = result["model_id"]
    src = Path(settings.glb_dir) / f"{model_id}.glb"
    new_params = extract_cad_params(new_script)
    info = projects.append_version(
        settings,
        project_id,
        src,
        op="param_edit",
        script=new_script,
        params=new_params,
        color=color,
        base_size_m=_cad_size_m(settings, model_id),
    )
    restore_version(session, info)
    return CommandResponse(
        ok=True,
        reply="Updated.",
        action="param_edit",
        rebuilt=True,
        color=session.color,
        glb_url=session.glb_url,
        model_id=session.model_id,
        session=session,
        latency_ms=latency,
        backend="cad",
        textured=False,
        display_size_m=_display_size_m(session),
        cad_params=new_params,
    )


async def _cad_from_photo(
    image_path: Path,
    session: SessionState,
    settings: Settings,
    latency: dict[str, float],
    hint: str = "",
) -> tuple[bool, str | None, str | None]:
    """Rebuild the photo's subject as CadQuery when the sculptors are down."""
    from ai.intent import codegen_from_photo

    t0 = time.perf_counter()
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    try:
        intent = await codegen_from_photo(
            image_path.read_bytes(), mime, settings, hint=hint
        )
    except Exception as exc:
        logger.warning("Photo codegen failed: %s", exc)
        return False, None, str(exc)
    latency["photo_codegen_ms"] = (time.perf_counter() - t0) * 1000

    if not intent.script:
        return False, None, "Gemini returned no script for the photo."

    session.scale = 1.0
    success, model_id, error = await _execute_with_retry(
        script=intent.script,
        original_text=f"the object in the photo{f' ({hint})' if hint else ''}",
        session=session,
        settings=settings,
        latency=latency,
        flatten_color=False,
    )
    if success:
        session.last_summary = intent.reply
        save_session(session)
    return success, model_id, error


async def build_from_image(
    image_url: str,
    session: SessionState,
    settings: Settings,
    prompt: str = "",
    speak: bool = True,
    quality: str = "draft",
    image_path: Path | None = None,
) -> CommandResponse:
    """Image-to-3D: a reference photo beats describing the object in words."""
    latency: dict[str, float] = {}
    t0 = time.perf_counter()

    result = await generate_mesh_glb_from_image(
        image_url,
        settings.glb_dir,
        prompt=prompt,
        quality=quality,
        image_path=image_path,
        hf_token=getattr(settings, "hf_token", "") or "",
        hf_space=bool(getattr(settings, "hf_space_enabled", True)),
        three_ws=bool(getattr(settings, "three_ws_enabled", True)),
    )
    latency["mesh_ms"] = result.get("exec_ms", (time.perf_counter() - t0) * 1000)

    if not result.get("ok"):
        error = str(result.get("error") or "Image-to-3D failed.")
        logger.info("Photo lane unavailable (%s); trying text sculpt", error)

        text_prompt = (prompt or "").strip()
        if image_path and image_path.exists() and settings.gemini_api_key:
            try:
                from ai.intent import mesh_prompt_from_photo

                mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
                text_prompt = await mesh_prompt_from_photo(
                    image_path.read_bytes(), mime, settings, hint=prompt
                )
                latency["photo_describe_ms"] = (time.perf_counter() - t0) * 1000
            except Exception as exc:
                logger.warning("Photo describe failed: %s", exc)

        if text_prompt:
            ok, model_id, mesh_error, textured = await _execute_mesh(
                text_prompt, session, settings, latency, use_nvidia=False
            )
            if ok:
                model_id = _record_version(
                    session, settings, model_id, "photo", new_object=True
                ) or model_id
                reply = (
                    "The photo engine was busy, so I sculpted it from "
                    "what I saw in the picture."
                )
                audio_url, tts_ms = (
                    await synthesize_speech(reply, settings) if speak else (None, 0)
                )
                latency["tts_ms"] = tts_ms
                return CommandResponse(
                    ok=True,
                    reply=reply,
                    action="generate",
                    rebuilt=True,
                    color=session.color,
                    glb_url=session.glb_url,
                    model_id=model_id,
                    reply_audio_url=audio_url,
                    session=session,
                    latency_ms=latency,
                    textured=textured,
                    backend="mesh",
                    display_size_m=_display_size_m(session),
                )
            error = f"{error} | text-sculpt: {mesh_error}"

        logger.info("Text sculpt unavailable; trying CAD from the photo")

        if image_path and image_path.exists():
            ok, model_id, cad_error = await _cad_from_photo(
                image_path, session, settings, latency, hint=prompt
            )
            if ok:
                model_id = _record_version(
                    session, settings, model_id, "photo", new_object=True
                ) or model_id
                reply = "The sculptor was down, so I modelled it in CAD instead."
                audio_url, tts_ms = (
                    await synthesize_speech(reply, settings) if speak else (None, 0)
                )
                latency["tts_ms"] = tts_ms
                return CommandResponse(
                    ok=True,
                    reply=reply,
                    action="generate",
                    rebuilt=True,
                    color=session.color,
                    glb_url=session.glb_url,
                    model_id=model_id,
                    reply_audio_url=audio_url,
                    session=session,
                    latency_ms=latency,
                    textured=False,
                    backend="cad",
                    display_size_m=_display_size_m(session),
                )
            error = f"{error} | cad-from-photo: {cad_error}"

        if result.get("error_type") == "busy":
            reply = "The free sculpting service is down. Try again in a few minutes."
        else:
            reply = "I couldn't build that from the photo."
        audio_url, _ = await synthesize_speech(reply, settings) if speak else (None, 0)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            latency_ms=latency,
            error=error,
            backend="mesh",
            reply_audio_url=audio_url,
        )

    session.template = None
    session.params = {}
    session.model_id = result["model_id"]
    session.glb_url = _glb_url(settings, result["model_id"])
    session.last_script = None
    session.last_backend = "mesh"
    session.last_mesh_prompt = prompt or session.last_mesh_prompt
    session.base_size_m = MESH_DEFAULT_M
    session.scale = 1.0
    save_session(session)
    model_id = _record_version(
        session, settings, result["model_id"], "photo", new_object=True
    ) or result["model_id"]

    reply = "Built that from your photo."
    audio_url, tts_ms = await synthesize_speech(reply, settings) if speak else (None, 0)
    latency["tts_ms"] = tts_ms

    return CommandResponse(
        ok=True,
        reply=reply,
        action="generate",
        rebuilt=True,
        color=session.color,
        glb_url=session.glb_url,
        model_id=model_id,
        reply_audio_url=audio_url,
        session=session,
        latency_ms=latency,
        textured=bool(result.get("textured", True)),
        backend="mesh",
        display_size_m=_display_size_m(session),
    )


async def build_chosen_photo(
    file_id: str,
    session: SessionState,
    settings: Settings,
) -> CommandResponse:
    """Sculpt the Drive photo the user pinched in the picker."""
    chosen = next((p for p in session.last_photos if p.get("id") == file_id), None)
    if not chosen:
        reply = "I don't have that photo anymore. Ask me to find it again."
        audio_url, _ = await synthesize_speech(reply, settings)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            reply_audio_url=audio_url,
            backend="mesh",
        )

    prompt = chosen.get("name") or ""
    # The staged cut-out on disk, for the CAD fallback when sculpting is down.
    local = settings.ref_dir / chosen["build_url"].rsplit("/", 1)[-1]
    return await build_from_image(
        chosen["build_url"],
        session,
        settings,
        prompt=prompt,
        quality="draft",
        image_path=local,
    )


def _spoken_photo_name(chosen: dict) -> str:
    raw = (chosen.get("name") or "this").strip()
    stem = raw.rsplit(".", 1)[0] if "." in raw else raw
    label = stem.replace("_", " ").replace("-", " ").strip()
    return label or "this"


async def confirm_chosen_photo(
    file_id: str,
    session: SessionState,
    settings: Settings,
) -> CommandResponse:
    """Spoken confirmation the moment the user pinches — before the long sculpt."""
    chosen = next((p for p in session.last_photos if p.get("id") == file_id), None)
    if not chosen:
        reply = "I don't have that photo anymore. Ask me to find it again."
        audio_url, _ = await synthesize_speech(reply, settings)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            reply_audio_url=audio_url,
            backend="mesh",
        )
    label = _spoken_photo_name(chosen)
    reply = f"Sounds good. Building this image of {label}."
    audio_url, tts_ms = await synthesize_speech(reply, settings)
    return CommandResponse(
        ok=True,
        reply=reply,
        action="confirm_photo",
        session=session,
        reply_audio_url=audio_url,
        backend="mesh",
        latency_ms={"tts_ms": tts_ms},
    )


async def execute_script_direct(
    script: str,
    session: SessionState,
    settings: Settings,
    color: str = "#C0C0C0",
) -> CommandResponse:
    """
    Direct script execution endpoint - bypasses intent parsing.
    Used when external codegen provides CadQuery scripts directly.
    """
    latency: dict[str, float] = {}
    session.color = color

    success, model_id, error = await _execute_with_retry(
        script=script,
        original_text="direct script execution",
        session=session,
        settings=settings,
        latency=latency,
    )

    if success:
        model_id = _record_version(session, settings, model_id, "script") or model_id
        return CommandResponse(
            ok=True,
            transcript=None,
            reply="Model built from script.",
            action="execute_script",
            rebuilt=True,
            color=color,
            glb_url=session.glb_url,
            model_id=model_id,
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
            textured=False,
            backend="cad",
            cad_params=_cad_params(session),
        )
    else:
        return CommandResponse(
            ok=False,
            transcript=None,
            reply=f"Script execution failed: {error}",
            action="clarify",
            rebuilt=False,
            color=session.color,
            glb_url=session.glb_url,
            model_id=None,
            reply_audio_url=None,
            session=session,
            latency_ms=latency,
            error=error,
        )
