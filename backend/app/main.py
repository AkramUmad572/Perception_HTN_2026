"""FastAPI entrypoint for Perception CAD."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai.intent import parse_intent
from app import jobs, projects
from app.config import get_settings
from app.httpclient import aclose_http_client
from app.models import (
    CommandRequest,
    CommandResponse,
    HistoryRequest,
    ParamUpdateRequest,
    PhotoChooseRequest,
    ResizeRequest,
    ScriptRequest,
    Selection,
)
from app.pipeline import (
    apply_intent,
    apply_param_update,
    apply_resize,
    apply_semantic_edit,
    build_chosen_photo,
    build_from_image,
    confirm_chosen_photo,
    execute_script_direct,
    history_step,
    save_client_version,
)
from app.session import clear_session, get_session
from mesh.refimage import isolate_subject
from photos.drive import ensure_local_file, valid_file_id
from voice.speech import synthesize_speech, transcribe_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("perception_cad")

settings = get_settings()

# Old audio replies, staged photos and abandoned projects are swept on startup
# and then hourly (app/projects.py:cleanup has the TTLs).
CLEANUP_INTERVAL_S = 3600


def _parse_selection(raw: str | None) -> Selection | None:
    """A client-sent `selection` field, or None if absent/unparseable. Never raises."""
    if not raw:
        return None
    try:
        return Selection.model_validate_json(raw)
    except Exception as exc:
        logger.info("Ignoring unparseable selection: %s", exc)
        return None


async def _cleanup_loop() -> None:
    while True:
        try:
            counts = await asyncio.to_thread(projects.cleanup, settings)
            if any(counts.values()):
                logger.info("Storage cleanup removed %s", counts)
        except Exception as exc:
            logger.exception("Storage cleanup failed: %s", exc)
        await asyncio.sleep(CLEANUP_INTERVAL_S)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Startup/shutdown for the whole app. Everything that used to hang off
    @app.on_event lives here: passing `lifespan` to FastAPI makes Starlette
    ignore on_event handlers entirely, so the shared httpx client has to be
    closed from this finally block or it never gets closed at all.
    """
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await aclose_http_client()


app = FastAPI(title="Perception CAD", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media/glb", StaticFiles(directory=str(settings.glb_dir)), name="glb")
app.mount("/media/audio", StaticFiles(directory=str(settings.audio_dir)), name="audio")
app.mount("/media/ref", StaticFiles(directory=str(settings.ref_dir)), name="ref")
app.mount(
    "/media/projects", StaticFiles(directory=str(settings.projects_dir)), name="projects"
)

WEB_DIST = Path(__file__).resolve().parents[2] / "web-client" / "dist"


@app.get("/api/health")
async def health():
    from cad.builder import _cadquery_available
    from mesh.factory import mesh_providers, mesh_ready

    providers = mesh_providers(settings)
    return {
        "ok": True,
        "cadquery": _cadquery_available(),
        "sandbox": True,
        "stt": bool(
            settings.elevenlabs_api_key
            or settings.deepgram_api_key
            or settings.openai_api_key
        ),
        "tts": bool(settings.elevenlabs_api_key),
        "llm": bool(settings.gemini_api_key or settings.openai_api_key),
        "llm_provider": (
            "gemini"
            if settings.gemini_api_key
            else "openai"
            if settings.openai_api_key
            else None
        ),
        "mesh": mesh_ready(settings),
        "mesh_provider": providers[0] if providers else None,
        "mesh_providers": providers,
        "stt_provider": (
            "elevenlabs"
            if settings.elevenlabs_api_key
            else "deepgram"
            if settings.deepgram_api_key
            else "openai"
            if settings.openai_api_key
            else None
        ),
        "drive": bool(settings.google_drive_api_key and settings.google_drive_folder_id),
        "composio": bool(settings.composio_api_key),
    }


@app.get("/api/session/{session_id}")
async def session_state(session_id: str = "default"):
    return get_session(session_id)


@app.post("/api/session/{session_id}/reset")
async def reset_session(session_id: str = "default"):
    """Wipe the in-headset session so the next find-photo starts clean."""
    clear_session(session_id)
    return get_session(session_id)


def _time_of_day_greeting(hour: int) -> str:
    if 5 <= hour < 12:
        period = "Good morning"
    elif 12 <= hour < 17:
        period = "Good afternoon"
    else:
        period = "Good evening"
    return f"{period}, sir. What would you like to work on today?"


@app.get("/api/greet", response_model=CommandResponse)
async def greet(session_id: str = "default", local_hour: int | None = None):
    """
    Deterministic, no-LLM greeting for the moment the user enters AR.

    `local_hour` (0-23), from the client's `new Date().getHours()`, is used
    instead of server time — the server's timezone won't generally match the
    user's. Falls back to server local time if omitted.
    """
    import datetime

    session = get_session(session_id)
    hour = local_hour if local_hour is not None else datetime.datetime.now().hour
    hour = max(0, min(23, hour))
    reply = _time_of_day_greeting(hour)
    audio_url, tts_ms = await synthesize_speech(reply, settings)
    return CommandResponse(
        ok=True,
        reply=reply,
        action="greet",
        session=session,
        reply_audio_url=audio_url,
        latency_ms={"tts_ms": tts_ms},
    )


@app.get("/api/projects/{project_id}")
async def project_info(project_id: str):
    """Version history of one project. Read-only."""
    try:
        info = projects.get_project(settings, project_id)
    except Exception as exc:
        logger.exception("Project read failed: %s", exc)
        info = None
    if info is None:
        return {"ok": False, "error": "Unknown project"}
    return {"ok": True, **info}


async def _history_route(project_id: str, body: HistoryRequest, direction: str) -> CommandResponse:
    t_all = time.perf_counter()
    session = get_session(body.session_id)
    try:
        result = await history_step(session, settings, project_id, direction, body.steps)
    except Exception as exc:
        logger.exception("%s failed: %s", direction, exc)
        result = CommandResponse(
            ok=False,
            reply=f"I couldn't {direction} that.",
            action="clarify",
            session=session,
            error=str(exc),
        )
    result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
    return result


@app.post("/api/projects/{project_id}/undo", response_model=CommandResponse)
async def project_undo(project_id: str, body: HistoryRequest):
    """Step back; responds rebuilt=True with the stored version's URL."""
    return await _history_route(project_id, body, "undo")


@app.post("/api/projects/{project_id}/redo", response_model=CommandResponse)
async def project_redo(project_id: str, body: HistoryRequest):
    return await _history_route(project_id, body, "redo")


@app.post("/api/projects/{project_id}/versions", response_model=CommandResponse)
async def project_save_version(
    project_id: str,
    glb: UploadFile = File(...),
    op: str = Form("hand_edit"),
    summary: str | None = Form(None),
    session_id: str = Form("default"),
):
    """
    Store a client-side edit (exported GLB) as the next version.
    Responds rebuilt=False, action="version_saved": the client already shows it.
    """
    session = get_session(session_id)
    try:
        raw = await glb.read()
        return await save_client_version(session, settings, project_id, raw, op, summary)
    except Exception as exc:
        logger.exception("Version save failed: %s", exc)
        return CommandResponse(
            ok=False,
            reply="That edit didn't save. Try again.",
            action="clarify",
            session=session,
            error=str(exc),
        )


@app.post("/api/projects/{project_id}/params", response_model=CommandResponse)
async def project_update_params(project_id: str, body: ParamUpdateRequest):
    """
    Rewrite named PARAMS on the project's current CAD script and rerun the
    sandbox — no LLM. Fired on drag release from the client's dimension panel.
    """
    session = get_session(body.session_id)
    try:
        return await apply_param_update(session, settings, project_id, body.updates)
    except Exception as exc:
        logger.exception("Param update failed: %s", exc)
        return CommandResponse(
            ok=False,
            reply="That change didn't save. Try again.",
            action="clarify",
            session=session,
            error=str(exc),
        )


@app.post("/api/projects/{project_id}/resize", response_model=CommandResponse)
async def project_resize(project_id: str, body: ResizeRequest):
    """
    Two-hand-stretch release: scale the project's real dimensions by `factor`.
    CAD rewrites every "_mm" PARAM and rebuilds; a sculpt folds the factor
    into its stored real size. No LLM.
    """
    session = get_session(body.session_id)
    try:
        return await apply_resize(session, settings, project_id, body.factor)
    except Exception as exc:
        logger.exception("Resize failed: %s", exc)
        return CommandResponse(
            ok=False,
            reply="That resize didn't save. Try again.",
            action="clarify",
            session=session,
            error=str(exc),
        )


@app.post("/api/projects/{project_id}/semantic_edit", response_model=CommandResponse)
async def project_semantic_edit(
    project_id: str,
    image: UploadFile = File(...),
    text: str = Form(...),
    session_id: str = Form("default"),
):
    """
    Semantic sculpt edit. The headset sends a PNG of what it is looking at with
    a circle drawn where the user pinched; this edits that picture with Gemini
    and rebuilds a mesh from it.

    Runs as a job because the whole chain is ~40-70 s: the answer here is just
    the job_id, and the client polls /api/jobs/{job_id} for the result.
    """
    session = get_session(session_id)
    try:
        png = await image.read()
    except Exception as exc:
        logger.exception("Could not read the uploaded render: %s", exc)
        return CommandResponse(
            ok=False, reply="That didn't upload. Try again.",
            action="clarify", session=session, error=str(exc),
        )

    async def work() -> CommandResponse:
        return await apply_semantic_edit(session, settings, project_id, png, text)

    job_id = jobs.start(work, progress={"stage": "editing"})
    return CommandResponse(
        ok=True, reply="That'll take a minute.", action="semantic_edit",
        session=session, job_id=job_id,
    )


@app.post("/api/command", response_model=CommandResponse)
async def command(body: CommandRequest):
    t_all = time.perf_counter()
    session = get_session(body.session_id)
    location = (
        f"{body.lat},{body.lon}" if body.lat is not None and body.lon is not None else None
    )
    intent, intent_ms = await parse_intent(
        body.text,
        settings,
        session.template,
        session.params,
        session.last_script,
        last_summary=session.last_summary,
        current_color=session.color,
        last_backend=session.last_backend,
        last_mesh_prompt=session.last_mesh_prompt,
        selection=body.selection,
        last_brief=session.last_brief,
        location=location,
    )
    result = await apply_intent(
        intent,
        session,
        settings,
        transcript=body.text,
        extra_latency={"intent_ms": intent_ms},
    )
    result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
    return result


@app.post("/api/script", response_model=CommandResponse)
async def execute_script(body: ScriptRequest):
    """
    Execute a CadQuery script directly in the sandbox.

    This endpoint is for Taha's codegen integration - bypasses intent parsing
    and executes the script directly with full safety (timeout, no FS/network,
    non-manifold rejection).

    The script must define a 'result', 'solid', or 'model' variable.
    """
    t_all = time.perf_counter()
    session = get_session(body.session_id)

    result = await execute_script_direct(
        script=body.script,
        session=session,
        settings=settings,
        color=body.color,
    )
    result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
    return result


@app.post("/api/image", response_model=CommandResponse)
async def image_to_3d(
    image: UploadFile = File(...),
    session_id: str = Form("default"),
    prompt: str = Form(""),
    quality: str = Form("draft"),
):
    """
    Build a model from a reference photo.

    three.ws fetches the image itself, so PUBLIC_BASE_URL must be an address
    reachable from the internet (a tunnel in dev) — not localhost.
    """
    t_all = time.perf_counter()
    session = get_session(session_id)
    try:
        raw = await image.read()
        if not raw or len(raw) < 1024:
            return CommandResponse(
                ok=False,
                reply="That image didn't come through. Try another one.",
                action="clarify",
                session=session,
                latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
            )

        suffix = Path(image.filename or "ref.png").suffix.lower() or ".png"
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            suffix = ".png"
        ref_id = uuid.uuid4().hex[:12]
        raw_path = settings.ref_dir / f"{ref_id}_raw{suffix}"
        raw_path.write_bytes(raw)

        # Image-to-3D rebuilds whatever fills the frame, so an un-cut photo
        # comes back as a flat card. Fall back to the original if the cut fails.
        served = f"{ref_id}_raw{suffix}"
        cut_path = settings.ref_dir / f"{ref_id}.png"
        cut = isolate_subject(raw_path, cut_path)
        if cut.get("ok"):
            served = cut_path.name
        else:
            logger.info("Subject isolation skipped: %s", cut.get("reason"))

        base = settings.public_base_url.rstrip("/")
        image_url = f"{base}/media/ref/{served}"
        # HF Spaces upload the local file. Only three.ws still needs a public URL.

        local = settings.ref_dir / served
        result = await build_from_image(
            image_url,
            session,
            settings,
            prompt=prompt,
            quality=quality,
            image_path=local if local.exists() else None,
        )
        result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
        return result
    except Exception as exc:
        logger.exception("Image-to-3D failed: %s", exc)
        return CommandResponse(
            ok=False,
            reply="That photo didn't work — try another.",
            action="clarify",
            session=session,
            error=str(exc),
            latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
        )


@app.get("/api/photos/{file_id}/preview")
async def photo_preview(file_id: str):
    """
    Drive photo for the AR picker.

    The headset loads this same-origin URL. We fetch the file from Google
    (API key cannot use alt=media, so we fall back to the public export)
    and cache it under storage/ref.
    """
    if not valid_file_id(file_id):
        raise HTTPException(status_code=400, detail="Invalid file id")
    try:
        if ":" in file_id:
            from composio_app.adapters import cache_prefixed

            path, mime = await cache_prefixed(file_id, settings)
        else:
            path, mime = await ensure_local_file(file_id, settings)
    except Exception as exc:
        logger.warning("Drive preview %s failed: %s", file_id, exc)
        raise HTTPException(status_code=404, detail="Photo unavailable") from exc
    return FileResponse(
        path,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/photos/confirm", response_model=CommandResponse)
async def confirm_photo(body: PhotoChooseRequest):
    """Speak a confirmation as soon as the user pinches a photo."""
    session = get_session(body.session_id)
    return await confirm_chosen_photo(body.file_id, session, settings)


@app.post("/api/photos/choose", response_model=CommandResponse)
async def choose_photo(body: PhotoChooseRequest):
    """
    Start building the Drive photo the user picked in AR.

    Returns a job id immediately — the sculpt takes minutes, which is longer
    than the connection between the headset and here reliably survives.
    """
    session = get_session(body.session_id)
    file_id = body.file_id
    session_id = body.session_id

    async def work() -> CommandResponse:
        t_all = time.perf_counter()
        result = await build_chosen_photo(file_id, get_session(session_id), settings)
        result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
        return result

    return CommandResponse(
        ok=True,
        reply="Building that from your photo.",
        action="building",
        session=session,
        backend="mesh",
        job_id=jobs.start(work),
    )


@app.get("/api/jobs/{job_id}", response_model=CommandResponse)
async def job_status(job_id: str, session_id: str = "default"):
    """Poll a detached build. `action` stays "building" until it lands."""
    session = get_session(session_id)
    job = jobs.get(job_id)

    if job is None:
        return CommandResponse(
            ok=False,
            reply="That build is gone. Ask me to find the photo again.",
            action="clarify",
            session=session,
            error=f"Unknown job {job_id}",
        )

    if job.result is not None:
        job.result.job_id = job_id
        return job.result

    if job.done:
        return CommandResponse(
            ok=False,
            reply="That build failed. Try again.",
            action="clarify",
            session=session,
            error=job.error,
            job_id=job_id,
        )

    # No reply_audio_url: a poll every few seconds must not talk over itself.
    apps = (job.progress or {}).get("apps") or []
    if apps:
        return CommandResponse(
            ok=True,
            reply=(job.progress or {}).get("caption") or "Still looking…",
            action="searching",
            session=session,
            backend="mesh",
            job_id=job_id,
            apps=apps,
            progress=job.progress,
            latency_ms={"elapsed_ms": job.elapsed_s * 1000},
        )
    return CommandResponse(
        ok=True,
        reply="Still sculpting…",
        action="building",
        session=session,
        backend="mesh",
        job_id=job_id,
        latency_ms={"elapsed_ms": job.elapsed_s * 1000},
    )


@app.post("/api/voice", response_model=CommandResponse)
async def voice(
    audio: UploadFile = File(...),
    session_id: str = Form("default"),
    selection: str | None = Form(None),
    lat: float | None = Form(None),
    lon: float | None = Form(None),
):
    t_all = time.perf_counter()
    session = get_session(session_id)
    location = f"{lat},{lon}" if lat is not None and lon is not None else None
    try:
        raw = await audio.read()
        if not raw or len(raw) < 200:
            return CommandResponse(
                ok=False,
                transcript="",
                reply="Hold to talk a bit longer, then release.",
                action="clarify",
                session=session,
                latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
            )

        transcript, stt_ms = await transcribe_audio(
            raw, audio.filename or "audio.webm", settings
        )
        if not transcript:
            return CommandResponse(
                ok=False,
                transcript="",
                reply="I didn't catch that. Try again.",
                action="clarify",
                session=session,
                latency_ms={"stt_ms": stt_ms},
            )

        intent, intent_ms = await parse_intent(
            transcript,
            settings,
            session.template,
            session.params,
            session.last_script,
            last_summary=session.last_summary,
            current_color=session.color,
            last_backend=session.last_backend,
            last_mesh_prompt=session.last_mesh_prompt,
            selection=_parse_selection(selection),
            last_brief=session.last_brief,
            location=location,
        )
        result = await apply_intent(
            intent,
            session,
            settings,
            transcript=transcript,
            extra_latency={"stt_ms": stt_ms, "intent_ms": intent_ms},
        )
        result.latency_ms["total_ms"] = (time.perf_counter() - t_all) * 1000
        return result
    except Exception as exc:
        logger.exception("Voice pipeline failed: %s", exc)
        return CommandResponse(
            ok=False,
            transcript=None,
            reply="Voice failed — try again.",
            action="clarify",
            session=session,
            latency_ms={"total_ms": (time.perf_counter() - t_all) * 1000},
        )


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:

    @app.get("/")
    async def root_fallback():
        return {
            "message": "Perception CAD API. Build web-client (npm run build) or use Vite dev server.",
            "health": "/api/health",
        }


def main():
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
