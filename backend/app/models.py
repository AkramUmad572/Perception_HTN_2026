"""Shared request/response and session models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CadParams(BaseModel):
    inner_diameter_mm: float | None = None
    outer_diameter_mm: float | None = None
    height_mm: float | None = None
    width_mm: float | None = None
    depth_mm: float | None = None
    diameter_mm: float | None = None
    color: str | None = None


class Intent(BaseModel):
    action: str = "noop"
    template: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    script: str | None = None
    parts: list[dict[str, Any]] = Field(default_factory=list)
    reply: str = "Okay."
    backend: str = "cad"  # cad | mesh
    mesh_prompt: str | None = None
    # Longest real-world dimension the object should have, in mm.
    size_mm: float | None = None
    # What's in the photo the user wants pulled from Drive.
    photo_query: str | None = None


class CommandRequest(BaseModel):
    text: str
    session_id: str = "default"


class PhotoChooseRequest(BaseModel):
    file_id: str
    session_id: str = "default"


class ScriptRequest(BaseModel):
    """Direct CadQuery script execution request."""
    script: str
    session_id: str = "default"
    color: str = "#C0C0C0"


class HistoryRequest(BaseModel):
    """Body of POST /api/projects/{project_id}/undo and /redo."""
    steps: int = 1
    session_id: str = "default"


class VersionInfo(BaseModel):
    """One saved state of a project. Stored in storage/projects/<id>/info.json."""
    project_id: str
    version: int  # 1-based, monotonically increasing, never reused
    kind: str  # "cad" | "mesh"
    # generate | set_material | hand_edit | param_edit | boolean | semantic_edit | photo | script
    op: str
    glb_url: str  # /media/projects/<project_id>/v<version>.glb
    summary: str | None = None
    script: str | None = None  # CAD only
    params: dict[str, float] = Field(default_factory=dict)  # CAD PARAMS snapshot
    mesh_prompt: str | None = None
    color: str | None = None
    base_size_m: float | None = None
    parent: int | None = None
    created_at: float


class SessionState(BaseModel):
    session_id: str = "default"
    template: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    color: str = "#C0C0C0"
    model_id: str | None = None
    glb_url: str | None = None
    last_script: str | None = None
    last_summary: str | None = None
    last_backend: str = "cad"  # cad | mesh
    last_mesh_prompt: str | None = None
    # Longest real-world dimension in metres, before the user's own resizing.
    base_size_m: float = 0.20
    # What "make it bigger" has done to it since.
    scale: float = 1.0
    # Last Drive photo search; each item has id, name, preview_url, build_url.
    last_photos: list[dict[str, Any]] = Field(default_factory=list)
    # Project history the model on screen belongs to (app/projects.py).
    project_id: str | None = None
    version: int | None = None


class CommandResponse(BaseModel):
    ok: bool = True
    transcript: str | None = None
    reply: str
    action: str
    rebuilt: bool = False
    color: str | None = None
    glb_url: str | None = None
    model_id: str | None = None
    reply_audio_url: str | None = None
    session: SessionState
    latency_ms: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
    textured: bool = False
    backend: str | None = None
    # Longest dimension the client should render, in metres.
    display_size_m: float | None = None
    # Drive photo matches for the AR picker. Empty unless action is find_photos.
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    # Set when the work runs detached; poll /api/jobs/{job_id} for the result.
    job_id: str | None = None
