"""Export the on-screen model to STL (default) or STEP.

Publish must execute generated scripts the same way the headset viewer does
(hex cq.Color, Assembly, allowlisted cq). If CAD re-export still fails, STL
falls back to the GLB already in the room so print/email cannot hard-fail.
"""

from __future__ import annotations

import logging
import multiprocessing
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    _MP_CTX = multiprocessing.get_context("fork")
except ValueError:
    _MP_CTX = multiprocessing.get_context()

EXPORT_TIMEOUT_SEC = 45


def safe_stem(name: str | None, default: str = "model") -> str:
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    stem = "_".join(words[:6]).strip("._")
    return stem.lower() or default


def glb_path_for_session(session: Any, settings: Any) -> Path | None:
    url = getattr(session, "glb_url", None) or ""
    path = str(url).split("?", 1)[0]
    if path.startswith("/media/glb/"):
        return Path(settings.glb_dir) / path[len("/media/glb/"):]
    projects_dir = getattr(settings, "projects_dir", None)
    if path.startswith("/media/projects/") and isinstance(projects_dir, Path):
        return Path(projects_dir) / path[len("/media/projects/"):]
    return None


def default_stem(session: Any) -> str:
    raw = (
        getattr(session, "last_summary", None)
        or getattr(session, "last_mesh_prompt", None)
        or getattr(session, "template", None)
        or "model"
    )
    return safe_stem(str(raw), "model")


def _is_assembly(obj: Any) -> bool:
    if obj is None:
        return False
    if type(obj).__name__ == "Assembly":
        return True
    return hasattr(obj, "children") and hasattr(obj, "objects") and hasattr(obj, "add")


def _export_worker(script: str, dest: str, q: Any) -> None:
    try:
        from cadquery import exporters
        from cad.sandbox import exec_cad_script

        result = exec_cad_script(script)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if _is_assembly(result):
            try:
                exporters.export(result, str(dest_path))
            except Exception:
                exporters.export(result.toCompound(), str(dest_path))
        else:
            exporters.export(result, str(dest_path))
        if not dest_path.exists() or dest_path.stat().st_size < 32:
            q.put({"ok": False, "error": "Export wrote an empty file"})
            return
        q.put({"ok": True, "path": str(dest_path)})
    except Exception as exc:
        q.put({"ok": False, "error": str(exc)})


def export_from_script(script: str, dest: Path, timeout: float = EXPORT_TIMEOUT_SEC) -> dict[str, Any]:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    q: Any = _MP_CTX.Queue()
    proc = _MP_CTX.Process(target=_export_worker, args=(script, str(dest), q))
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        return {"ok": False, "error": "Export timed out"}
    try:
        return q.get_nowait()
    except Exception:
        return {"ok": False, "error": "Export failed"}


def export_mesh_stl(glb_path: Path, dest: Path, size_mm: float | None = None) -> dict[str, Any]:
    import trimesh

    loaded = trimesh.load(str(glb_path), force=None)
    if isinstance(loaded, trimesh.Scene):
        geoms = [g for g in loaded.geometry.values() if g is not None]
        if not geoms:
            return {"ok": False, "error": "Empty mesh"}
        mesh = trimesh.util.concatenate(geoms)
    else:
        mesh = loaded
    if size_mm and getattr(mesh, "extents", None) is not None:
        longest = float(max(mesh.extents))
        if longest > 0:
            mesh.apply_scale(size_mm / longest)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(dest), file_type="stl")
    return {"ok": True, "path": str(dest), "fallback": "mesh"}


def _mesh_size_mm(session: Any) -> float:
    size_mm = float(getattr(session, "base_size_m", 0.2) or 0.2)
    size_mm *= float(getattr(session, "scale", 1.0) or 1.0)
    return size_mm * 1000.0


def export_session_model(
    session: Any,
    settings: Any,
    dest: Path,
    fmt: str = "stl",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    fmt = "step" if fmt in ("step", "stp") else "stl"
    dest = Path(dest)
    script = getattr(session, "last_script", None)
    if script:
        out = export_from_script(script, dest)
        out["ms"] = (time.perf_counter() - t0) * 1000
        if out.get("ok"):
            return out
        logger.warning("CAD script export failed (%s); trying on-screen mesh if STL", out.get("error"))
        if fmt == "step":
            return out
    elif fmt == "step":
        return {"ok": False, "error": "step_needs_cad", "ms": 0}

    glb = glb_path_for_session(session, settings)
    if glb is None or not glb.exists():
        return {"ok": False, "error": (script and "export failed") or "no_model", "ms": (time.perf_counter() - t0) * 1000}
    if dest.suffix.lower() != ".stl":
        dest = dest.with_suffix(".stl")
    out = export_mesh_stl(glb, dest, _mesh_size_mm(session))
    out["ms"] = (time.perf_counter() - t0) * 1000
    return out
