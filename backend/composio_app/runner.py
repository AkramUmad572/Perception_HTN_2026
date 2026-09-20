"""Background Composio pull: update per-app HUD status, then photos or item cards."""

from __future__ import annotations

import logging
import re
from typing import Any

from app import jobs
from app.config import Settings
from app.models import CommandResponse, Intent, SessionState
from app.session import save_session
from pathlib import Path

from cad.export import default_stem, export_session_model, safe_stem
from composio_app.adapters import (
    public_folder_images,
    pull_calendar,
    pull_drive_images,
    pull_figma_account,
    pull_figma_images,
    pull_github,
    pull_google_photos,
    pull_emails,
    pull_last_email,
    pull_notion_pages,
    pull_sheets,
    lookup_gmail_person,
    send_gmail,
    upload_drive_file,
)
from composio_app.apps import APPS, chip
from composio_app.client import ComposioAuthError, ComposioPermissionError
from composio_app.publish_parse import compose_email_body, resolve_recipient
from composio_app.summary import summarize_emails
from voice.speech import synthesize_speech

logger = logging.getLogger(__name__)

_NOREPLY = ("noreply", "no-reply", "google", "slack", "devpost", "accounts.google")


def _rank_emails(items: list[dict[str, Any]], topic: str) -> list[dict[str, Any]]:
    words = [w for w in re.findall(r"[a-z0-9]{3,}", (topic or "").lower())]

    def score(it: dict[str, Any]) -> int:
        title = str(it.get("title") or "").lower()
        body = str(it.get("body") or "").lower()
        sender = str(it.get("subtitle") or it.get("sender_name") or "").lower()
        s = 0
        if words and all(w in title for w in words):
            s += 12
            extras = [t for t in re.findall(r"[a-z0-9]{3,}", title) if t not in words]
            s -= min(len(extras), 8)
        elif words:
            s += sum(3 for w in words if w in title)
            s += sum(1 for w in words if w in body)
        if any(x in sender for x in _NOREPLY):
            s -= 3
        return s

    return sorted(items, key=score, reverse=True)


async def _run_app(
    slug: str,
    kind: str,
    settings: Settings,
    query: str,
    days: int = 2,
    on_date: str | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (status, photos, items)."""
    try:
        if slug == "googledrive" and kind in ("photos", "list"):
            photos = await pull_drive_images(settings, query)
            if not photos and kind == "photos":
                photos = await public_folder_images(settings)
                return ("done" if photos else "empty", photos, [])
            return ("done" if photos else "empty", photos, [])
        if slug == "googlephotos":
            photos = []
            try:
                photos = await pull_google_photos(settings)
            except (ComposioAuthError, ComposioPermissionError):
                photos = []
            # Library API only returns app-uploaded items; fall back to Drive.
            if not photos:
                try:
                    photos = await pull_drive_images(settings, query)
                except (ComposioAuthError, ComposioPermissionError):
                    photos = []
            if not photos:
                photos = await public_folder_images(settings)
            return ("done" if photos else "empty", photos, [])
        if slug == "gmail" and kind == "email_search":
            items = await pull_emails(settings, query, days=days, on_date=on_date)
            return ("done" if items else "empty", [], items)
        if slug == "gmail" and kind in ("last_email", "list"):
            items = await pull_last_email(settings)
            return ("done" if items else "empty", [], items)
        if slug == "gmail" and kind == "photos":
            return ("empty", [], [])
        if slug == "notion":
            items = await pull_notion_pages(settings)
            return ("done" if items else "empty", [], items)
        if slug == "googlecalendar":
            items = await pull_calendar(settings)
            return ("done" if items else "empty", [], items)
        if slug == "github":
            items = await pull_github(settings)
            return ("done" if items else "empty", [], items)
        if slug == "googlesheets":
            items = await pull_sheets(settings)
            return ("done" if items else "empty", [], items)
        if slug == "figma":
            if kind == "photos":
                photos = await pull_figma_images(settings)
                return ("done" if photos else "empty", photos, [])
            items = await pull_figma_account(settings)
            return ("done" if items else "empty", [], items)
        if slug == "googledrive":
            items = await pull_sheets(settings)
            return ("done" if items else "empty", [], items)
        return ("empty", [], [])
    except (ComposioAuthError, ComposioPermissionError):
        if slug == "googledrive" and kind == "photos":
            photos = await public_folder_images(settings)
            return ("done" if photos else "error", photos, [])
        raise
    except Exception as exc:
        logger.warning("App %s failed: %s", slug, exc)
        if slug == "googledrive" and kind == "photos":
            try:
                photos = await public_folder_images(settings)
                return ("done" if photos else "error", photos, [])
            except Exception:
                pass
        return ("error", [], [])


async def run_pull(intent: Intent, session: SessionState, settings: Settings, job_id: str) -> CommandResponse:
    apps = list(intent.apps or [])
    kind = intent.pull_kind or "list"
    query = intent.photo_query or ""
    try:
        days = int((intent.params or {}).get("days") or 2)
    except (TypeError, ValueError):
        days = 2
    on_date = (intent.params or {}).get("on_date") or None
    if on_date:
        on_date = str(on_date)
    photos: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    auth_failed = False
    perm_failed = False
    missing_conn: list[str] = []

    for slug in apps:
        jobs.set_app_status(job_id, slug, "live")
        try:
            status, got_photos, got_items = await _run_app(
                slug, kind, settings, query, days=days, on_date=on_date
            )
        except ComposioAuthError as exc:
            logger.warning("Composio auth failed on %s: %s", slug, exc)
            jobs.set_app_status(job_id, slug, "error")
            auth_failed = True
            continue
        except ComposioPermissionError as exc:
            logger.warning("Composio permission failed on %s: %s", slug, exc)
            jobs.set_app_status(job_id, slug, "error")
            msg = str(exc).lower()
            if "no connected account" in msg or "connectedaccountnotfound" in msg:
                missing_conn.append(slug)
            else:
                perm_failed = True
            continue
        jobs.set_app_status(job_id, slug, status)
        photos.extend(got_photos)
        items.extend(got_items)

    # Dedup photos by id
    seen: set[str] = set()
    uniq_photos = []
    for p in photos:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        uniq_photos.append(p)
    photos = uniq_photos
    if kind in ("last_email", "email_search") and items:
        items = _rank_emails(items, query)

    session.last_photos = photos
    session.last_items = items
    if items:
        session.last_brief = " ".join(
            f"{i.get('title','')}: {i.get('body','')}" for i in items[:3]
        ).strip()
    save_session(session)

    if photos:
        action = "find_photos"
        reply = f"Here's {len(photos)} from your photos." if len(photos) != 1 else "Found it. Pinch to confirm."
    elif items:
        action = "show_items"
        if kind in ("last_email", "email_search"):
            reply, suggestion = await summarize_emails(items, settings, topic=query, days=days)
            if suggestion:
                items[0]["suggested_reply"] = suggestion
        else:
            reply = f"Here's {len(items)} from {apps[0]}."
    elif auth_failed:
        action = "clarify"
        reply = "Composio's API key was rejected. Add a valid project key and I'll pull your apps."
    elif missing_conn:
        action = "clarify"
        names = ", ".join(APPS.get(s, {}).get("label", s) for s in missing_conn)
        reply = f"{names} isn't connected on this Composio project yet. Open Platform, Auth Configs, add it, then connect your account."
    elif perm_failed:
        action = "clarify"
        reply = (
            "This Composio key can't run tools yet. In Platform, open API Keys, "
            "turn on Tool execution write, then connect that app on this project."
        )
    else:
        action = "clarify"
        reply = "I didn't find anything there."

    audio_url, tts_ms = await synthesize_speech(reply, settings)
    return CommandResponse(
        ok=action != "clarify",
        reply=reply,
        action=action,
        session=session,
        backend="mesh",
        reply_audio_url=audio_url,
        candidates=photos,
        items=items,
        apps=jobs.get(job_id).progress.get("apps") if jobs.get(job_id) else [chip(s, "done") for s in apps],
        latency_ms={"tts_ms": tts_ms},
    )


def start_pull_job(
    intent: Intent,
    session: SessionState,
    settings: Settings,
    transcript: str | None,
) -> CommandResponse:
    apps = [chip(s, "queued") for s in (intent.apps or [])]
    caption = intent.reply or "On it."
    box: dict[str, str] = {}

    async def work() -> CommandResponse:
        result = await run_pull(intent, session, settings, box["id"])
        result.transcript = transcript
        return result

    job_id = jobs.start(work, progress={"apps": apps, "caption": caption})
    box["id"] = job_id

    return CommandResponse(
        ok=True,
        transcript=transcript,
        reply=caption,
        action="searching",
        session=session,
        backend="mesh",
        job_id=job_id,
        apps=apps,
    )


def _has_model(session: SessionState) -> bool:
    return bool((session.last_script or "").strip() or (session.glb_url or "").strip())


def _needed_formats(spec: dict[str, Any]) -> list[str]:
    raw = spec.get("formats") or [spec.get("format") or "stl"]
    out: list[str] = []
    for item in raw:
        fmt = "step" if item in ("step", "stp") else "stl"
        if fmt not in out:
            out.append(fmt)
    return out or ["stl"]


def _publish_reply(spec: dict[str, Any], dest_name: str, emailed: str | None) -> str:
    fmt = (spec.get("format") or "stl").upper()
    bits: list[str] = []
    if dest_name:
        if spec.get("drive"):
            if " and " in dest_name:
                bits.append(f"saved as {dest_name} files successfully in Google Drive")
            else:
                bits.append(f"saved as a {dest_name} file successfully in Google Drive")
        else:
            bits.append(f"exported {dest_name}")
    if emailed:
        bits.append(f"emailed {emailed}")
    if not bits:
        bits.append(f"exported to {fmt}")
    text = " and ".join(bits)
    return text[0].upper() + text[1:] + "."


async def run_publish(intent: Intent, session: SessionState, settings: Settings, job_id: str) -> CommandResponse:
    spec = dict(intent.params or {})
    formats = _needed_formats(spec)
    fmt = formats[0]
    apps = list(intent.apps or [])

    # Exporting (and therefore needing a model on screen) is only required
    # when something actually consumes the exported file: an attachment or
    # a Drive upload. A plain "email John saying X" touches neither, so it
    # must work with nothing built yet.
    needs_model = bool(spec.get("attach")) or bool(spec.get("drive"))

    if needs_model and not _has_model(session):
        reply = "Build something first, then I can export it."
        audio_url, tts_ms = await synthesize_speech(reply, settings)
        return CommandResponse(
            ok=False,
            reply=reply,
            action="clarify",
            session=session,
            backend="mesh",
            reply_audio_url=audio_url,
            apps=jobs.get(job_id).progress.get("apps") if jobs.get(job_id) else [],
            latency_ms={"tts_ms": tts_ms},
        )

    paths: dict[str, Path] = {}
    dest_path: Path | None = None
    dest_name = ""
    export_ms = 0.0

    if needs_model:
        stem = safe_stem(spec.get("filename"), default_stem(session))
        exported_by_fmt: dict[str, dict[str, Any]] = {}
        last_error = ""
        dest = Path(settings.glb_dir) / "publish" / f"{stem}.stl"
        for item in formats:
            dest = Path(settings.glb_dir) / "publish" / f"{stem}.{'step' if item == 'step' else 'stl'}"
            exported = export_session_model(session, settings, dest, item)
            export_ms += float(exported.get("ms") or 0)
            if not exported.get("ok") and item == "step" and not spec.get("format_explicit"):
                if "stl" not in exported_by_fmt:
                    dest = dest.with_suffix(".stl")
                    exported = export_session_model(session, settings, dest, "stl")
                    export_ms += float(exported.get("ms") or 0)
                    item = "stl"
                else:
                    logger.warning("STEP export failed (%s); keeping STL", exported.get("error"))
                    continue
            if exported.get("ok"):
                exported_by_fmt[item] = exported
            else:
                last_error = str(exported.get("error") or "export failed")
        if not exported_by_fmt:
            err = last_error or "export failed"
            if err == "step_needs_cad":
                reply = "I need the CAD script to export STEP. Ask me to rebuild it, then try again."
            elif err == "no_model":
                reply = "Build something first, then I can export it."
            else:
                reply = "I couldn't export that. Rebuild it, then try again."
            audio_url, tts_ms = await synthesize_speech(reply, settings)
            return CommandResponse(
                ok=False,
                reply=reply,
                action="clarify",
                session=session,
                backend="mesh",
                reply_audio_url=audio_url,
                error=str(err),
                apps=jobs.get(job_id).progress.get("apps") if jobs.get(job_id) else [],
                latency_ms={"tts_ms": tts_ms},
            )

        paths = {key: Path(val.get("path") or "") for key, val in exported_by_fmt.items() if val.get("path")}
        dest_path = paths.get("stl") or paths.get("step") or Path(next(iter(exported_by_fmt.values())).get("path") or dest)
        dest_name = " and ".join(p.name for p in (paths.get("stl"), paths.get("step")) if p)
        if not dest_name:
            dest_name = dest_path.name
        spec["format"] = "step" if "step" in paths and "stl" not in paths else ("stl" if "stl" in paths else fmt)

    emailed: str | None = None
    auth_failed = False
    perm_failed = False

    if spec.get("drive") and "googledrive" in apps:
        jobs.set_app_status(job_id, "googledrive", "live")
        try:
            to_upload = [p for p in (paths.get("stl"), paths.get("step")) if p] or [dest_path]
            uploaded = {"ok": False}
            for cad_path in to_upload:
                uploaded = await upload_drive_file(settings, cad_path, spec.get("folder"))
                if not uploaded.get("ok"):
                    break
            jobs.set_app_status(job_id, "googledrive", "done" if uploaded.get("ok") else "error")
            if uploaded.get("ok") and session.project_id:
                from app import projects

                projects.mark_saved_to_drive(settings, session.project_id)
        except ComposioAuthError as exc:
            logger.warning("Drive upload auth failed: %s", exc)
            jobs.set_app_status(job_id, "googledrive", "error")
            auth_failed = True
        except ComposioPermissionError as exc:
            logger.warning("Drive upload permission failed: %s", exc)
            jobs.set_app_status(job_id, "googledrive", "error")
            perm_failed = True
        except Exception as exc:
            logger.warning("Drive upload failed: %s", exc)
            jobs.set_app_status(job_id, "googledrive", "error")

    if spec.get("wants_email"):
        spoken = spec.get("recipient")
        to = resolve_recipient(spoken, session.last_items)
        if not to and spoken and "@" not in str(spoken):
            try:
                to = await lookup_gmail_person(settings, str(spoken))
            except Exception as exc:
                logger.warning("Gmail people lookup failed: %s", exc)
                to = None
        if not to:
            reply = (
                f"I don't have an email for {spoken}. Say the address?"
                if spoken
                else "Who should I send it to?"
            )
            if "gmail" in apps:
                jobs.set_app_status(job_id, "gmail", "error")
            audio_url, tts_ms = await synthesize_speech(reply, settings)
            return CommandResponse(
                ok=False,
                reply=reply,
                action="clarify",
                session=session,
                backend="mesh",
                reply_audio_url=audio_url,
                apps=jobs.get(job_id).progress.get("apps") if jobs.get(job_id) else [],
                latency_ms={"tts_ms": tts_ms},
            )
        if "gmail" in apps:
            jobs.set_app_status(job_id, "gmail", "live")
        try:
            attach: Path | list[Path] | None = None
            if spec.get("attach"):
                files = [p for p in (paths.get("step"), paths.get("stl")) if p]
                if len(files) == 1:
                    attach = files[0]
                elif files:
                    attach = files
            subject = (dest_path.stem.replace("_", " ") if dest_path else "") or "Message from Percy"
            sent = await send_gmail(
                settings,
                to,
                subject=subject,
                body=compose_email_body(spec),
                attachment=attach,
            )
            if "gmail" in apps:
                jobs.set_app_status(job_id, "gmail", "done" if sent.get("ok") else "error")
            if sent.get("ok"):
                emailed = spoken or to
            else:
                perm_failed = True
        except ComposioAuthError as exc:
            logger.warning("Gmail send auth failed: %s", exc)
            if "gmail" in apps:
                jobs.set_app_status(job_id, "gmail", "error")
            auth_failed = True
        except ComposioPermissionError as exc:
            logger.warning("Gmail send permission failed: %s", exc)
            if "gmail" in apps:
                jobs.set_app_status(job_id, "gmail", "error")
            perm_failed = True
        except Exception as exc:
            logger.warning("Gmail send failed: %s", exc)
            if "gmail" in apps:
                jobs.set_app_status(job_id, "gmail", "error")
            perm_failed = True

    save_session(session)
    progress_apps = jobs.get(job_id).progress.get("apps") if jobs.get(job_id) else [chip(s, "done") for s in apps]
    if auth_failed and not emailed and not spec.get("drive"):
        reply = "Composio's API key was rejected. Add a valid project key and I'll send this out."
        action = "clarify"
        ok = False
    elif perm_failed and not emailed and spec.get("wants_email"):
        reply = "I couldn't send that email. Check the Gmail connection and try again."
        action = "clarify"
        ok = False
    else:
        reply = _publish_reply(spec, dest_name, emailed)
        action = "published"
        ok = True

    audio_url, tts_ms = await synthesize_speech(reply, settings)
    return CommandResponse(
        ok=ok,
        reply=reply,
        action=action,
        session=session,
        backend=session.last_backend or "cad",
        reply_audio_url=audio_url,
        glb_url=session.glb_url,
        model_id=session.model_id,
        apps=progress_apps,
        latency_ms={"tts_ms": tts_ms, "export_ms": export_ms},
    )


def start_publish_job(
    intent: Intent,
    session: SessionState,
    settings: Settings,
    transcript: str | None,
) -> CommandResponse:
    apps = [chip(s, "queued") for s in (intent.apps or [])]
    caption = intent.reply or "Sending this out…"
    box: dict[str, str] = {}

    async def work() -> CommandResponse:
        result = await run_publish(intent, session, settings, box["id"])
        result.transcript = transcript
        return result

    job_id = jobs.start(work, progress={"apps": apps, "caption": caption, "kind": "publish"})
    box["id"] = job_id

    return CommandResponse(
        ok=True,
        transcript=transcript,
        reply=caption,
        action="publishing",
        session=session,
        backend="mesh",
        job_id=job_id,
        apps=apps,
        progress={"apps": apps, "caption": caption, "kind": "publish"},
    )
