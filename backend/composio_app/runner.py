"""Background Composio pull: update per-app HUD status, then photos or item cards."""

from __future__ import annotations

import logging
import re
from typing import Any

from app import jobs
from app.config import Settings
from app.models import CommandResponse, Intent, SessionState
from app.session import save_session
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
)
from composio_app.apps import APPS, chip
from composio_app.client import ComposioAuthError, ComposioPermissionError
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
