"""Per-object project history: versioned GLBs, undo/redo, TTL cleanup.

Layout::

    <projects_dir>/<project_id>/v1.glb, v2.glb, ...
    <projects_dir>/<project_id>/info.json

info.json = {"current": int, "last_version": int, "versions": [VersionInfo...],
             "saved_to_drive": bool, "touched_at": float}

`last_version` is what keeps version numbers from ever being reused after a
redo tail is dropped — a reused number would reuse a URL the client (and the
browser cache) has already seen with different geometry.

Plain filesystem, single process, like app/session.py. Every write goes
through a temp file plus os.replace so a crash cannot corrupt info.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from app.models import VersionInfo

logger = logging.getLogger(__name__)

MAX_VERSIONS = 20
AUDIO_TTL_S = 3600
REF_TTL_S = 86400
PROJECT_TTL_S = 7 * 86400

_PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Filled from the parent version when append_version's caller leaves them out.
_INHERITED = ("kind", "summary", "script", "params", "mesh_prompt", "color", "base_size_m")
# Set by this module, never by callers.
_RESERVED = ("project_id", "version", "glb_url", "parent", "created_at")


def _project_dir(settings: Any, project_id: str) -> Path | None:
    """The project's folder, or None for an id that could escape projects_dir."""
    if not isinstance(project_id, str) or not _PID_RE.match(project_id):
        return None
    return Path(settings.projects_dir) / project_id


def version_url(project_id: str, version: int) -> str:
    return f"/media/projects/{project_id}/v{version}.glb"


def load_info(settings: Any, project_id: str) -> dict | None:
    """Parsed info.json with `versions` as VersionInfo objects; None if absent or unreadable."""
    pdir = _project_dir(settings, project_id)
    if pdir is None:
        return None
    path = pdir / "info.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["versions"] = [VersionInfo.model_validate(v) for v in raw.get("versions", [])]
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Unreadable project info %s: %s", project_id, exc)
        return None
    if not raw["versions"]:
        return None
    raw.setdefault("saved_to_drive", False)
    raw.setdefault("last_version", max(v.version for v in raw["versions"]))
    return raw


def _write_info(settings: Any, project_id: str, info: dict) -> None:
    pdir = _project_dir(settings, project_id)
    if pdir is None:
        raise ValueError(f"Bad project id {project_id!r}")
    info["touched_at"] = time.time()
    payload = {**info, "versions": [v.model_dump() for v in info["versions"]]}
    fd, tmp = tempfile.mkstemp(dir=str(pdir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, pdir / "info.json")
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _new_version(project_id: str, version: int, meta: dict, parent: int | None) -> VersionInfo:
    fields = {
        k: v for k, v in meta.items() if k in VersionInfo.model_fields and k not in _RESERVED
    }
    if fields.get("params") is None:
        fields.pop("params", None)
    fields.setdefault("op", "generate")
    return VersionInfo(
        project_id=project_id,
        version=version,
        glb_url=version_url(project_id, version),
        parent=parent,
        created_at=time.time(),
        **fields,
    )


def _index_of(versions: list[VersionInfo], version: int) -> int:
    for i, v in enumerate(versions):
        if v.version == version:
            return i
    return len(versions) - 1


def create_project(settings: Any, kind: str, glb_src: Path, **meta: Any) -> VersionInfo:
    """Start a new project; moves `glb_src` in as v1."""
    project_id = uuid.uuid4().hex[:12]
    pdir = Path(settings.projects_dir) / project_id
    pdir.mkdir(parents=True, exist_ok=False)
    shutil.move(str(glb_src), str(pdir / "v1.glb"))
    first = _new_version(project_id, 1, {**meta, "kind": kind}, None)
    _write_info(
        settings,
        project_id,
        {"current": 1, "last_version": 1, "versions": [first], "saved_to_drive": False},
    )
    return first


def append_version(settings: Any, project_id: str, glb_src: Path, **meta: Any) -> VersionInfo:
    """
    Add a version after the current one; moves `glb_src` in.

    Anything past the current version (the redo tail) is dropped first, like
    any editor. Meta the caller leaves out is inherited from the parent.
    Raises ValueError for an unknown project.
    """
    info = load_info(settings, project_id)
    if info is None:
        raise ValueError(f"Unknown project {project_id!r}")
    pdir = _project_dir(settings, project_id)
    versions: list[VersionInfo] = info["versions"]
    idx = _index_of(versions, info["current"])
    parent = versions[idx]
    for dropped in versions[idx + 1 :]:
        (pdir / f"v{dropped.version}.glb").unlink(missing_ok=True)
    versions = versions[: idx + 1]

    n = max(int(info["last_version"]), max(v.version for v in versions)) + 1
    for key in _INHERITED:
        if key not in meta:
            meta[key] = getattr(parent, key)
    shutil.move(str(glb_src), str(pdir / f"v{n}.glb"))
    new = _new_version(project_id, n, meta, parent.version)
    versions.append(new)
    info.update(current=n, last_version=n, versions=versions)
    _write_info(settings, project_id, info)
    prune(settings, project_id)
    return new


def get_version(settings: Any, project_id: str, version: int) -> VersionInfo | None:
    info = load_info(settings, project_id)
    if info is None:
        return None
    return next((v for v in info["versions"] if v.version == version), None)


def current_version(settings: Any, project_id: str) -> VersionInfo | None:
    info = load_info(settings, project_id)
    if info is None:
        return None
    return info["versions"][_index_of(info["versions"], info["current"])]


def get_project(settings: Any, project_id: str) -> dict | None:
    """JSON-safe copy of info.json plus the project id, for the GET route."""
    info = load_info(settings, project_id)
    if info is None:
        return None
    return {
        "project_id": project_id,
        "current": info["current"],
        "versions": [v.model_dump() for v in info["versions"]],
        "saved_to_drive": bool(info.get("saved_to_drive")),
        "touched_at": info.get("touched_at"),
    }


def prune(settings: Any, project_id: str, keep: int = MAX_VERSIONS) -> None:
    """Keep v1 (the original), the newest keep-1, and always the current one."""
    info = load_info(settings, project_id)
    if info is None:
        return
    versions: list[VersionInfo] = info["versions"]
    keep = max(1, int(keep))
    if len(versions) <= keep:
        return
    kept_nums = {versions[0].version, info["current"]}
    for v in reversed(versions):
        if len(kept_nums) >= keep:
            break
        kept_nums.add(v.version)
    pdir = _project_dir(settings, project_id)
    for v in versions:
        if v.version not in kept_nums:
            (pdir / f"v{v.version}.glb").unlink(missing_ok=True)
    info["versions"] = [v for v in versions if v.version in kept_nums]
    _write_info(settings, project_id, info)


def _step(settings: Any, project_id: str, delta: int) -> VersionInfo | None:
    """Move the current pointer through the kept versions; None if it cannot move."""
    info = load_info(settings, project_id)
    if info is None:
        return None
    versions: list[VersionInfo] = info["versions"]
    idx = _index_of(versions, info["current"])
    target = max(0, min(len(versions) - 1, idx + delta))
    if target == idx:
        return None
    info["current"] = versions[target].version
    _write_info(settings, project_id, info)
    return versions[target]


def undo(settings: Any, project_id: str, steps: int = 1) -> VersionInfo | None:
    """Step back (clamped at the oldest kept version). None if nothing to undo."""
    return _step(settings, project_id, -max(1, int(steps)))


def redo(settings: Any, project_id: str, steps: int = 1) -> VersionInfo | None:
    """Step forward (clamped at the newest). None if nothing to redo."""
    return _step(settings, project_id, max(1, int(steps)))


def mark_saved_to_drive(settings: Any, project_id: str) -> None:
    """Exempt a project from TTL cleanup. Unknown projects are ignored."""
    info = load_info(settings, project_id)
    if info is None:
        logger.warning("mark_saved_to_drive: unknown project %s", project_id)
        return
    info["saved_to_drive"] = True
    _write_info(settings, project_id, info)


def _sweep_files(directory: Path, ttl_s: float, now: float) -> int:
    """Delete plain files older than ttl_s by mtime. Dot-files are left alone."""
    removed = 0
    try:
        entries = list(Path(directory).iterdir())
    except FileNotFoundError:
        return 0
    for path in entries:
        try:
            if path.name.startswith(".") or not path.is_file():
                continue
            if now - path.stat().st_mtime > ttl_s:
                path.unlink(missing_ok=True)
                removed += 1
        except Exception as exc:
            logger.warning("Cleanup skipped %s: %s", path.name, exc)
    return removed


def cleanup(settings: Any, now: float | None = None) -> dict[str, int]:
    """
    Delete spoken replies older than 1 h, staged reference photos older than
    24 h, and projects untouched for 7 days unless marked saved_to_drive.
    Returns how many entries were deleted per kind. Never raises.
    """
    now = time.time() if now is None else float(now)
    counts = {
        "audio": _sweep_files(settings.audio_dir, AUDIO_TTL_S, now),
        "ref": _sweep_files(settings.ref_dir, REF_TTL_S, now),
        "projects": 0,
    }
    try:
        project_dirs = [p for p in Path(settings.projects_dir).iterdir() if p.is_dir()]
    except FileNotFoundError:
        project_dirs = []
    for pdir in project_dirs:
        try:
            info = load_info(settings, pdir.name)
            if info is not None and info.get("saved_to_drive"):
                continue
            touched = (info or {}).get("touched_at") or pdir.stat().st_mtime
            if now - float(touched) > PROJECT_TTL_S:
                shutil.rmtree(pdir, ignore_errors=True)
                counts["projects"] += 1
        except Exception as exc:
            logger.warning("Cleanup skipped project %s: %s", pdir.name, exc)
    return counts
