"""List and download images from a public Google Drive folder."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.httpclient import get_http_client
from photos.stage import suffix_for

logger = logging.getLogger(__name__)

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DRIVE_UC = "https://drive.google.com/uc"
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/heic")
_TIMEOUT = 45.0
_FILE_ID_RE = re.compile(r"^[\w-]{8,128}$")
_CONFIRM_RE = re.compile(r"confirm=([0-9A-Za-z_-]+)")


def preview_url(file_id: str) -> str:
    """Same-origin URL the headset loads — Vite proxies /api to the backend."""
    return f"/api/photos/{file_id}/preview"


def valid_file_id(file_id: str) -> bool:
    return bool(_FILE_ID_RE.match((file_id or "").strip()))


def _mime_for_suffix(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get((suffix or "").lower(), "image/jpeg")


def _existing_raw(settings: Settings, file_id: str) -> Path | None:
    matches = sorted(settings.ref_dir.glob(f"drive_{file_id}_raw.*"))
    return matches[0] if matches else None


async def list_images(settings: Settings) -> list[dict[str, Any]]:
    """Image files in the configured public folder."""
    folder = (settings.google_drive_folder_id or "").strip()
    key = (settings.google_drive_api_key or "").strip()
    if not folder or not key:
        raise RuntimeError("Drive isn't configured. Set GOOGLE_DRIVE_API_KEY and GOOGLE_DRIVE_FOLDER_ID.")

    query = f"'{folder}' in parents and trashed = false"
    params = {
        "q": query,
        "fields": "files(id,name,mimeType,thumbnailLink)",
        "pageSize": 20,
        "key": key,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    client = get_http_client()
    resp = await client.get(DRIVE_FILES, params=params, timeout=_TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"Drive list failed ({resp.status_code}): {resp.text[:240]}")
    data = resp.json()

    out: list[dict[str, Any]] = []
    for f in data.get("files") or []:
        mime = (f.get("mimeType") or "").lower()
        name = f.get("name") or ""
        if mime.startswith("image/") or name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            out.append(
                {
                    "id": f.get("id"),
                    "name": name,
                    "mime": mime or "image/jpeg",
                    "thumbnail_link": f.get("thumbnailLink"),
                }
            )
    return [item for item in out if item.get("id")]


def _image_payload(resp, *, source: str) -> tuple[bytes, str] | None:
    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    body = resp.content or b""
    if resp.status_code >= 400 or len(body) < 32:
        return None
    if ctype.startswith("image/"):
        return body, ctype
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return body, "image/png"
    if body[:2] == b"\xff\xd8":
        return body, "image/jpeg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return body, "image/webp"
    logger.info("Drive %s was not an image (%s, %s bytes)", source, ctype or "unknown", len(body))
    return None


async def _download_via_api(file_id: str, settings: Settings) -> tuple[bytes, str] | None:
    key = (settings.google_drive_api_key or "").strip()
    client = get_http_client()
    resp = await client.get(
        f"{DRIVE_FILES}/{file_id}",
        params={"alt": "media", "key": key, "supportsAllDrives": "true"},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        logger.info("Drive API media %s → %s; trying public export", file_id, resp.status_code)
        return None
    return _image_payload(resp, source="api")


async def _download_via_uc(file_id: str) -> tuple[bytes, str]:
    """
    Public export URL. API keys often 403 on files.get?alt=media even when the
    folder is listed as public; this path is what a browser 'Download' uses.
    """
    client = get_http_client()
    resp = await client.get(
        DRIVE_UC,
        params={"export": "download", "id": file_id},
        follow_redirects=True,
        timeout=_TIMEOUT,
    )
    got = _image_payload(resp, source="uc")
    if got:
        return got

    text = resp.text if "html" in (resp.headers.get("content-type") or "") else ""
    confirm = _CONFIRM_RE.search(text)
    if confirm:
        resp = await client.get(
            DRIVE_UC,
            params={"export": "download", "id": file_id, "confirm": confirm.group(1)},
            follow_redirects=True,
            timeout=_TIMEOUT,
        )
        got = _image_payload(resp, source="uc-confirm")
        if got:
            return got

    raise RuntimeError(f"Drive download failed ({resp.status_code}): {(resp.text or '')[:200]}")


async def download_file(file_id: str, settings: Settings) -> tuple[bytes, str]:
    """Return (bytes, mime). Uses a disk cache so the picker and the build share one fetch."""
    path, mime = await ensure_local_file(file_id, settings)
    return path.read_bytes(), mime


async def ensure_local_file(file_id: str, settings: Settings) -> tuple[Path, str]:
    """Fetch a Drive photo onto disk if needed. Headset then loads our copy."""
    if not valid_file_id(file_id):
        raise RuntimeError("Invalid Drive file id.")
    settings.ref_dir.mkdir(parents=True, exist_ok=True)
    existing = _existing_raw(settings, file_id)
    if existing and existing.stat().st_size > 32:
        return existing, _mime_for_suffix(existing.suffix)

    fetched = await _download_via_api(file_id, settings)
    if fetched is None:
        fetched = await _download_via_uc(file_id)
    raw, mime = fetched
    path = settings.ref_dir / f"drive_{file_id}_raw{suffix_for(mime)}"
    path.write_bytes(raw)
    return path, mime
