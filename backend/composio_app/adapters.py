"""Pull images and text items from the eight Composio apps."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.httpclient import get_http_client
from composio_app.client import ComposioAuthError, execute
from photos import drive as public_drive
from photos.stage import suffix_for

logger = logging.getLogger(__name__)

_WEB_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_WEB_IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
_SKIP_IMAGE = (".raf", ".raw", ".cr2", ".nef", ".dng", ".arw", ".tif", ".tiff", ".heic", ".heif")
_LIST_KEYS = (
    "files",
    "items",
    "messages",
    "mediaItems",
    "media_items",
    "results",
    "pages",
    "repositories",
    "spreadsheets",
)
_VIEW_URL_MARKERS = (
    "drive.google.com/file",
    "mail.google.com",
    "calendar.google.com",
    "notion.so",
    "app.notion.com",
    "github.com",
)


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    dumped = getattr(result, "model_dump", None)
    if callable(dumped):
        out = dumped()
        if isinstance(out, dict):
            return out
    return {"raw": result}


def _payload_layers(payload: Any) -> list[dict[str, Any]]:
    data = _as_dict(payload)
    layers = [data]
    inner = data.get("data")
    if isinstance(inner, dict):
        layers.append(inner)
        deeper = inner.get("data")
        if isinstance(deeper, dict):
            layers.append(deeper)
    return layers


def _files_from(payload: Any) -> list[dict[str, Any]]:
    for layer in _payload_layers(payload):
        for key in _LIST_KEYS:
            val = layer.get(key)
            if isinstance(val, list):
                return [v for v in val if isinstance(v, dict)]
        inner = layer.get("data")
        if isinstance(inner, list):
            return [v for v in inner if isinstance(v, dict)]
    data = _as_dict(payload)
    if isinstance(data.get("successful"), bool) and isinstance(data.get("data"), list):
        return [v for v in data["data"] if isinstance(v, dict)]
    return []


def _is_web_image(mime: str, name: str) -> bool:
    mime = (mime or "").lower()
    name = (name or "").lower()
    if any(name.endswith(ext) for ext in _SKIP_IMAGE):
        return False
    if any(name.endswith(ext) for ext in _WEB_IMAGE_EXTS):
        return True
    if mime in _WEB_IMAGE_MIMES:
        return True
    return mime.startswith("image/") and not any(tok in mime for tok in ("raf", "raw", "tiff", "heic", "heif"))


def _is_file_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    return not any(marker in url for marker in _VIEW_URL_MARKERS)


def file_refs(payload: Any) -> tuple[list[Any], list[str], str]:
    """Pull raw blobs and download URLs out of a Composio file payload."""
    blobs: list[Any] = []
    urls: list[str] = []
    mime = "image/jpeg"
    for layer in _payload_layers(payload):
        mime = str(layer.get("mimeType") or layer.get("mimetype") or mime)
        content = (
            layer.get("downloaded_file_content")
            or layer.get("file")
            or layer.get("content")
        )
        if isinstance(content, dict):
            mime = str(content.get("mimetype") or content.get("mimeType") or mime)
            for key in ("s3url", "s3_url", "url", "signed_url", "download_url"):
                candidate = content.get(key)
                if _is_file_url(str(candidate) if candidate is not None else ""):
                    urls.append(str(candidate))
            for key in ("content", "data", "bytes", "file"):
                nested = content.get(key)
                if nested not in (None, content):
                    blobs.append(nested)
        elif isinstance(content, (bytes, bytearray, str)):
            blobs.append(content)
        for key in ("s3url", "s3_url", "signed_url", "download_url"):
            candidate = layer.get(key)
            if _is_file_url(str(candidate) if candidate is not None else ""):
                urls.append(str(candidate))
    return blobs, urls, mime


def _decode_blob(blob: Any) -> bytes | None:
    if isinstance(blob, (bytes, bytearray)) and len(blob) > 32:
        return bytes(blob)
    if isinstance(blob, str) and _is_file_url(blob):
        return None
    if isinstance(blob, str) and len(blob) > 32:
        try:
            raw = base64.b64decode(blob)
            if len(raw) > 32:
                return raw
        except Exception:
            return None
    return None


async def bytes_from_composio(payload: Any) -> tuple[bytes, str]:
    blobs, urls, mime = file_refs(payload)
    for blob in blobs:
        if isinstance(blob, str) and _is_file_url(blob):
            urls.append(blob)
            continue
        raw = _decode_blob(blob)
        if raw:
            return raw, mime

    client = get_http_client()
    for url in urls:
        try:
            resp = await client.get(url, follow_redirects=True, timeout=45.0)
            body = resp.content or b""
            if resp.status_code < 400 and len(body) > 32:
                ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip()
                return body, ctype or mime
            logger.warning("Composio file URL %s → %s (%s bytes)", url[:80], resp.status_code, len(body))
        except Exception as exc:
            logger.warning("Composio file URL fetch failed: %s", exc)
    keys = list(_as_dict(payload).keys())
    logger.warning("Composio download had no bytes; keys=%s", keys[:20])
    raise RuntimeError("Drive download returned no bytes")


_DRIVE_STOP = {
    "pull", "up", "my", "the", "google", "drive", "gdrive", "photos", "photo",
    "pictures", "picture", "pics", "images", "image", "hey", "percy", "can",
    "you", "please", "from", "all", "every", "across", "apps", "app",
    "accounts", "account", "through", "looking", "look", "find", "this",
    "that", "really", "cute", "wanna", "want", "make", "and", "for", "with",
    "out", "get", "give", "here", "there", "some", "any", "folder", "folders",
    "named", "called", "specific", "type", "kind",
}
_FOLDER_HINT = re.compile(
    r"\b(?:in|from|inside)\s+(?:my\s+|the\s+)?(.+?)\s+folder\b",
    re.I,
)


def drive_search_terms(query: str) -> list[str]:
    """Subject words/filenames, keeping pikachu_keychain as one token."""
    cleaned_q = _FOLDER_HINT.sub(" ", query or "")
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", cleaned_q.lower())
    terms: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        cleaned = tok.strip("_-")
        if not cleaned or cleaned in _DRIVE_STOP or cleaned in seen:
            continue
        parts = [p for p in re.split(r"[_-]+", cleaned) if p]
        if parts and all(p in _DRIVE_STOP for p in parts):
            continue
        seen.add(cleaned)
        terms.append(cleaned)
    terms.sort(key=lambda w: (-len(w), w))
    return terms[:4]


def drive_folder_hint(query: str) -> str | None:
    m = _FOLDER_HINT.search(query or "")
    if not m:
        return None
    raw = re.sub(r"[^a-z0-9 _-]+", " ", m.group(1).lower())
    words = [w for w in raw.split() if w and w not in _DRIVE_STOP]
    hint = " ".join(words).strip(" -_")
    return hint or None


def _name_score(name: str, terms: list[str]) -> int:
    n = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    compact = n.replace(" ", "")
    if not terms:
        return 1
    score = 0
    joined = "".join(re.sub(r"[^a-z0-9]+", "", t) for t in terms)
    if joined and joined in compact:
        score += 24
    hits = 0
    for term in terms:
        bits = [term] + [p for p in re.split(r"[_-]+", term) if len(p) > 2]
        if any(bit in n or bit in compact for bit in bits):
            hits += 1
            score += 8
    if hits == len(terms) and len(terms) > 1:
        score += 12
    return score


def _drive_name_query(terms: list[str]) -> str:
    if not terms:
        return ""
    escaped = [t.replace("'", "\\'") for t in terms]
    and_q = " and ".join(f"name contains '{t}'" for t in escaped)
    or_q = " or ".join(f"name contains '{t}'" for t in escaped)
    if len(escaped) == 1:
        return f" and ({and_q})"
    return f" and (({and_q}) or ({or_q}))"


async def _find_drive_folder(settings: Settings, hint: str) -> str | None:
    raw = await execute(
        settings,
        "GOOGLEDRIVE_FIND_FILE",
        {
            "q": f"mimeType = 'application/vnd.google-apps.folder' and name contains '{hint.replace(chr(39), '')}' and trashed = false",
            "pageSize": 5,
            "orderBy": "modifiedTime desc",
            "fields": "files(id,name,mimeType)",
        },
    )
    folders = _files_from(raw)
    if not folders:
        return None
    ranked = sorted(folders, key=lambda f: _name_score(str(f.get("name") or ""), [hint]), reverse=True)
    fid = ranked[0].get("id")
    logger.info("Drive folder %r → %s (%s)", hint, ranked[0].get("name"), fid)
    return str(fid) if fid else None


async def pull_drive_images(settings: Settings, query: str = "") -> list[dict[str, Any]]:
    terms = drive_search_terms(query)
    folder_hint = drive_folder_hint(query)
    q = "mimeType contains 'image/' and trashed = false" + _drive_name_query(terms)
    args: dict[str, Any] = {
        "q": q,
        "pageSize": 20,
        "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType)",
    }
    if folder_hint:
        try:
            folder_id = await _find_drive_folder(settings, folder_hint)
        except ComposioAuthError:
            raise
        except Exception as exc:
            logger.warning("Drive folder lookup failed: %s", exc)
            folder_id = None
        if folder_id:
            args["folder_id"] = folder_id
    try:
        raw = await execute(settings, "GOOGLEDRIVE_FIND_FILE", args)
        files = _files_from(raw)
    except ComposioAuthError:
        raise
    except Exception as exc:
        logger.warning("Drive find failed: %s", exc)
        files = []

    scored: list[tuple[int, dict[str, Any]]] = []
    for f in files:
        mime = (f.get("mimeType") or "").lower()
        name = f.get("name") or "photo"
        fid = f.get("id")
        if not fid or not _is_web_image(mime, name):
            continue
        score = _name_score(name, terms)
        if terms and score < 8:
            continue
        cid = f"gdrive:{fid}"
        scored.append((score, {
            "id": cid,
            "name": name,
            "preview_url": f"/api/photos/{cid}/preview",
            "image_url": f"/api/photos/{cid}/preview",
            "source": "googledrive",
        }))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and terms and scored[0][0] >= 20:
        scored = [item for item in scored if item[0] >= 20]
    return [item for _, item in scored[:16]]


async def pull_google_photos(settings: Settings) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        raw = await execute(
            settings,
            "GOOGLEPHOTOS_SEARCH_MEDIA_ITEMS",
            {"pageSize": 20, "filters": {"mediaTypeFilter": {"mediaTypes": "PHOTO"}}},
        )
        items = _files_from(raw)
    except ComposioAuthError:
        raise
    except Exception as exc:
        logger.warning("Photos search failed: %s", exc)
    if not items:
        try:
            raw = await execute(settings, "GOOGLEPHOTOS_LIST_MEDIA_ITEMS", {"pageSize": 20})
            items = _files_from(raw)
        except ComposioAuthError:
            raise
        except Exception as exc:
            logger.warning("Photos list failed: %s", exc)
            items = []
    out = []
    for it in items:
        mid = it.get("id") or it.get("mediaItemId")
        if not mid:
            continue
        cid = f"gphotos:{mid}"
        out.append({
            "id": cid,
            "name": it.get("filename") or it.get("description") or "photo",
            "preview_url": f"/api/photos/{cid}/preview",
            "image_url": f"/api/photos/{cid}/preview",
            "source": "googlephotos",
        })
    return out[:16]


def _b64url_text(blob: str) -> str:
    try:
        pad = "=" * (-len(blob) % 4)
        return base64.urlsafe_b64decode(blob + pad).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _walk_plain(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    mime = str(payload.get("mimeType") or "")
    data = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    raw = data.get("data") if isinstance(data, dict) else None
    if mime.startswith("text/plain") and isinstance(raw, str) and raw:
        return _b64url_text(raw)
    for part in payload.get("parts") or []:
        got = _walk_plain(part)
        if got:
            return got
    if mime.startswith("text/html") and isinstance(raw, str) and raw:
        return re.sub(r"<[^>]+>", " ", _b64url_text(raw))
    return ""


def _textish(val: Any) -> str:
    if isinstance(val, dict):
        return str(val.get("plain") or val.get("body") or val.get("text") or "").strip()
    return str(val or "").strip()


def _sender_name(raw: str) -> str:
    from email.utils import parseaddr

    name, addr = parseaddr(raw or "")
    return (name or addr or raw or "Unknown").strip()


def _parse_dt(raw: Any):
    from datetime import datetime, timezone

    if raw in (None, ""):
        return None
    s = str(raw).strip()
    if s.isdigit():
        ts = int(s)
        if ts > 10**12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _local_day(raw: Any) -> str:
    dt = _parse_dt(raw)
    return dt.strftime("%Y/%m/%d") if dt else ""


def _fmt_when(raw: Any) -> str:
    from datetime import datetime

    dt = _parse_dt(raw)
    if dt is None:
        return str(raw or "")[:48]
    clock = dt.strftime("%-I:%M %p")
    mins = int((datetime.now(dt.tzinfo) - dt).total_seconds() // 60)
    if mins < 1:
        return f"{clock} (just now)"
    if mins < 60:
        return f"{clock} ({mins} minute{'s' if mins != 1 else ''} ago)"
    if mins < 24 * 60:
        hrs = mins // 60
        return f"{clock} ({hrs} hour{'s' if hrs != 1 else ''} ago)"
    return dt.strftime("%b %-d")


def _email_card(m: dict[str, Any]) -> dict[str, Any]:
    subject = m.get("subject") or m.get("Subject") or "(no subject)"
    sender = m.get("sender") or m.get("from") or m.get("From") or ""
    snippet = (
        _textish(m.get("messageText"))
        or _textish(m.get("preview"))
        or _textish(m.get("snippet"))
        or _textish(m.get("text"))
        or _walk_plain(m.get("payload"))
    )
    snippet = re.sub(r"<!--.*?-->", "", snippet, flags=re.S)
    snippet = re.sub(r"<style.*?>.*?</style>", " ", snippet, flags=re.S | re.I)
    snippet = re.sub(r"<[^>]+>", " ", snippet)
    snippet = re.sub(r"&nbsp;|&amp;|&lt;|&gt;|&#39;|&quot;", " ", snippet)
    snippet = re.sub(r"[ \t]+\n", "\n", snippet)
    snippet = re.sub(r"\n{3,}", "\n\n", snippet)
    snippet = re.sub(r"[ \t]{2,}", " ", snippet)
    snippet = snippet.strip()
    date = m.get("messageTimestamp") or m.get("date") or m.get("internalDate") or ""
    day = _local_day(date)
    return {
        "id": f"gmail:{m.get('messageId') or m.get('id') or 'last'}",
        "kind": "email",
        "title": str(subject)[:120],
        "subtitle": str(sender)[:80],
        "sender_name": _sender_name(str(sender))[:80],
        "to": "me",
        "body": snippet[:1200],
        "meta": _fmt_when(date),
        "day": day,
        "source": "gmail",
    }


def _inbox_only(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        labels = m.get("labelIds") or m.get("label_ids") or m.get("labels") or []
        if isinstance(labels, str):
            labels = [labels]
        names = {str(x).upper() for x in labels}
        if names & {"TRASH", "SPAM"}:
            continue
        if names and "INBOX" not in names:
            continue
        kept.append(m)
    return kept


async def _gmail_messages(settings: Settings, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    args = dict(arguments)
    args["include_spam_trash"] = False
    q = str(args.get("query") or "").strip()
    extra = "in:inbox -in:trash -in:spam"
    args["query"] = f"{q} {extra}".strip() if q else extra
    args.pop("label_ids", None)
    raw = await execute(settings, "GMAIL_FETCH_EMAILS", args)
    messages = _files_from(raw)
    if not messages:
        data = _as_dict(raw)
        messages = data.get("messages") or []
        if isinstance(data.get("data"), dict) and not messages:
            messages = data["data"].get("messages") or []
    return _inbox_only([m for m in messages if isinstance(m, dict)])


async def pull_last_email(settings: Settings) -> list[dict[str, Any]]:
    messages = await _gmail_messages(
        settings,
        {"max_results": 1, "include_payload": True, "verbose": True},
    )
    return [_email_card(messages[0])] if messages else []


_TOPIC_ALIASES = {
    "hockton": "hack",
    "hocks": "hack",
    "hacks": "hack",
    "hock": "hack",
    "hacked": "hack",
    "hyphen": "hack",
    "hackathon": "hack",
    "notes": "north",
}


def _canonical_topic(topic: str) -> str:
    t = (topic or "").lower()
    if re.search(r"hockton|hacks?\s+and\s+notes|hack.*north|north.*hack|htn", t):
        return "hack north"
    return re.sub(r"\s+", " ", t).strip()


def _topic_hits(hay: str, topic: str) -> bool:
    hay = (hay or "").lower()
    topic = _canonical_topic(topic)
    words = [w for w in re.findall(r"[a-z0-9]{3,}", topic) if w not in {"for", "the", "and"}]
    if not words:
        return True

    def present(word: str) -> bool:
        variants = {word, _TOPIC_ALIASES.get(word, word)}
        if word.endswith("s") and len(word) > 3:
            variants.add(word[:-1])
        return any(v in hay for v in variants if len(v) > 2)

    return sum(1 for w in words if present(w)) >= max(1, (len(words) + 1) // 2)


async def _fetch_inbox(settings: Settings, query: str, limit: int = 8) -> list[dict[str, Any]]:
    return await _gmail_messages(
        settings,
        {
            "query": query,
            "max_results": limit,
            "include_payload": True,
            "verbose": True,
        },
    )


def _gmail_window(days: int, on_date: str | None) -> str:
    from datetime import datetime, timedelta

    if on_date:
        try:
            day = datetime.strptime(on_date, "%Y/%m/%d")
            nxt = (day + timedelta(days=1)).strftime("%Y/%m/%d")
            return f"after:{on_date} before:{nxt}"
        except ValueError:
            return f"after:{on_date} before:{on_date}"
    days = max(1, min(int(days or 2), 30))
    after = (datetime.now().astimezone() - timedelta(days=days - 1)).strftime("%Y/%m/%d")
    return f"after:{after}"


def _keep_on_date(cards: list[dict[str, Any]], on_date: str | None) -> list[dict[str, Any]]:
    if not on_date:
        return cards
    want = on_date.replace("-", "/")
    return [c for c in cards if not c.get("day") or c.get("day") == want]


async def pull_emails(
    settings: Settings,
    topic: str = "",
    days: int = 2,
    on_date: str | None = None,
) -> list[dict[str, Any]]:
    """Keyword search. A named date is that day only."""
    topic = _canonical_topic(re.sub(r"\s+", " ", (topic or "").replace('"', "").strip()))
    window = _gmail_window(days, on_date)
    if topic == "hack north":
        q = f'{window} ("hack the north" OR (hack north) OR hackathon)'
    elif topic:
        words = topic.split()
        if len(words) == 1:
            q = f"{window} {words[0]}"
        else:
            phrase = " ".join(words)
            q = f'{window} ("{phrase}" OR ({phrase}))'
    else:
        q = window
    logger.info("Gmail search q=%r", q)
    messages = await _fetch_inbox(settings, q, 8)
    cards = _keep_on_date([_email_card(m) for m in messages], on_date)
    if topic:
        matched = [
            c for c in cards
            if _topic_hits(f"{c.get('title','')} {c.get('body','')}", topic)
        ]
        if matched:
            return matched[:8]
        logger.info("Gmail topic miss; scanning inbox in window %s", window)
        recent = await _fetch_inbox(settings, window, 12)
        cards = _keep_on_date([_email_card(m) for m in recent], on_date)
        matched = [
            c for c in cards
            if _topic_hits(f"{c.get('title','')} {c.get('body','')}", topic)
        ]
        return matched[:8]
    return cards[:8]


async def pull_notion_pages(settings: Settings) -> list[dict[str, Any]]:
    try:
        raw = await execute(
            settings,
            "NOTION_SEARCH_NOTION_PAGE",
            {
                "query": "",
                "page_size": 8,
                "timestamp": "last_edited_time",
                "direction": "descending",
                "filter_value": "page",
            },
        )
    except Exception:
        raw = await execute(settings, "NOTION_FETCH_DATA", {"fetch_type": "pages", "page_size": 8})
    pages = _files_from(raw)
    out = []
    for p in pages:
        title = p.get("title") or p.get("name") or p.get("text")
        if isinstance(title, list):
            title = "".join(str(x.get("plain_text", x) if isinstance(x, dict) else x) for x in title)
        if not title:
            props = p.get("properties") or {}
            for pv in props.values() if isinstance(props, dict) else []:
                if isinstance(pv, dict) and pv.get("type") == "title":
                    bits = pv.get("title") or []
                    title = "".join(x.get("plain_text", "") for x in bits if isinstance(x, dict))
                    if title:
                        break
        title = title or "Untitled"
        pid = p.get("id") or p.get("page_id") or title
        out.append({
            "id": f"notion:{pid}",
            "kind": "notion",
            "title": str(title)[:120],
            "subtitle": "Notion",
            "body": str(p.get("highlight") or p.get("url") or p.get("snippet") or "")[:400],
            "source": "notion",
        })
    return out[:8]


async def pull_calendar(settings: Settings) -> list[dict[str, Any]]:
    from datetime import datetime, timezone

    raw = await execute(
        settings,
        "GOOGLECALENDAR_EVENTS_LIST",
        {
            "calendarId": "primary",
            "maxResults": 6,
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    events = _files_from(raw)
    out = []
    for e in events:
        if str(e.get("status") or "").lower() == "cancelled":
            continue
        start = e.get("start") if isinstance(e.get("start"), dict) else {}
        out.append({
            "id": f"gcal:{e.get('id') or e.get('iCalUID')}",
            "kind": "event",
            "title": e.get("summary") or "Event",
            "subtitle": str(start.get("dateTime") or start.get("date") or "")[:40],
            "body": (e.get("description") or e.get("location") or "")[:300],
            "source": "googlecalendar",
        })
    return out[:6]


async def pull_github(settings: Settings) -> list[dict[str, Any]]:
    raw = await execute(
        settings,
        "GITHUB_LIST_REPOSITORIES_FOR_THE_AUTHENTICATED_USER",
        {"per_page": 5, "sort": "updated", "type": "owner"},
    )
    repos = _files_from(raw)
    if not repos:
        data = _as_dict(raw)
        repos = data.get("repos") or data.get("items") or []
    out = []
    for r in repos:
        out.append({
            "id": f"github:{r.get('full_name') or r.get('name')}",
            "kind": "repo",
            "title": r.get("full_name") or r.get("name") or "repo",
            "subtitle": "GitHub",
            "body": (r.get("description") or r.get("html_url") or "")[:300],
            "source": "github",
        })
    return out[:6]


async def pull_sheets(settings: Settings) -> list[dict[str, Any]]:
    raw = await execute(
        settings,
        "GOOGLESHEETS_SEARCH_SPREADSHEETS",
        {"max_results": 6, "order_by": "modifiedTime desc"},
    )
    files = _files_from(raw)
    return [{
        "id": f"gsheet:{f.get('id') or f.get('spreadsheetId')}",
        "kind": "sheet",
        "title": f.get("name") or f.get("title") or "Sheet",
        "subtitle": "Google Sheets",
        "body": "Spreadsheet",
        "source": "googlesheets",
    } for f in files if f.get("id") or f.get("spreadsheetId")][:6]


async def pull_figma_account(settings: Settings) -> list[dict[str, Any]]:
    raw = await execute(settings, "FIGMA_GET_CURRENT_USER", {})
    data = _as_dict(raw)
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    handle = inner.get("handle") or inner.get("email") or "Figma"
    return [{
        "id": f"figma:{inner.get('id') or handle}",
        "kind": "figma",
        "title": str(handle),
        "subtitle": "Figma",
        "body": inner.get("email") or "Connected Figma account",
        "source": "figma",
    }]


async def pull_figma_images(settings: Settings) -> list[dict[str, Any]]:
    # Figma needs a file key; without one we return empty rather than guess.
    logger.info("Figma image pull skipped (no file key in utterance)")
    return []


async def public_folder_images(settings: Settings) -> list[dict[str, Any]]:
    files = await public_drive.list_images(settings)
    out = []
    for f in files:
        fid = f.get("id")
        if not fid:
            continue
        cid = f"public:{fid}"
        out.append({
            "id": cid,
            "name": f.get("name") or "photo",
            "preview_url": f"/api/photos/{cid}/preview",
            "image_url": f"/api/photos/{cid}/preview",
            "source": "public",
        })
    return out


async def download_prefixed(file_id: str, settings: Settings) -> tuple[bytes, str]:
    """Fetch bytes for a prefixed candidate id."""
    kind, _, raw_id = file_id.partition(":")
    if not raw_id:
        raw_id = kind
        kind = "public"
    if kind == "gdrive":
        result = await execute(settings, "GOOGLEDRIVE_DOWNLOAD_FILE", {"fileId": raw_id})
        return await bytes_from_composio(result)
    if kind == "gphotos":
        result = await execute(settings, "GOOGLEPHOTOS_GET_MEDIA_ITEM_DOWNLOAD", {"mediaItemId": raw_id})
        return await bytes_from_composio(result)
    if kind == "public":
        return await public_drive.download_file(raw_id, settings)
    raise RuntimeError(f"Unknown photo source {kind}")


async def cache_prefixed(file_id: str, settings: Settings):
    from photos.drive import _existing_raw, _mime_for_suffix

    existing = _existing_raw(settings, file_id.replace(":", "_"))
    if existing and existing.stat().st_size > 32:
        return existing, _mime_for_suffix(existing.suffix)
    raw, mime = await download_prefixed(file_id, settings)
    settings.ref_dir.mkdir(parents=True, exist_ok=True)
    stem = file_id.replace(":", "_")
    path = settings.ref_dir / f"drive_{stem}_raw{suffix_for(mime)}"
    path.write_bytes(raw)
    return path, mime


def _composio_ok(payload: Any) -> bool:
    data = _as_dict(payload)
    if data.get("successful") is False:
        return False
    err = data.get("error") or data.get("error_message")
    return not err


def _first_id(payload: Any) -> str | None:
    for layer in _payload_layers(payload):
        for key in ("id", "fileId", "file_id", "folderId", "folder_id"):
            val = layer.get(key)
            if val:
                return str(val)
        file_obj = layer.get("file") or layer.get("folder")
        if isinstance(file_obj, dict) and file_obj.get("id"):
            return str(file_obj["id"])
    return None


async def find_or_create_drive_folder(settings: Settings, hint: str) -> str | None:
    folder_id = await _find_drive_folder(settings, hint)
    if folder_id:
        return folder_id
    raw = await execute(settings, "GOOGLEDRIVE_CREATE_FOLDER", {"name": hint})
    created = _first_id(raw)
    logger.info("Drive created folder %r → %s", hint, created)
    return created


async def upload_drive_file(
    settings: Settings,
    path: Path,
    folder_hint: str | None = None,
) -> dict[str, Any]:
    folder_id = (getattr(settings, "composio_drive_export_folder_id", None) or "").strip()
    if not folder_id and folder_hint:
        try:
            folder_id = await find_or_create_drive_folder(settings, folder_hint)
        except Exception as exc:
            logger.warning("Drive folder resolve failed (%s); uploading to root", exc)
    args: dict[str, Any] = {"file_to_upload": str(Path(path).resolve())}
    if folder_id:
        args["folder_to_upload_to"] = folder_id
    raw = await execute(settings, "GOOGLEDRIVE_UPLOAD_FILE", args)
    data = _as_dict(raw)
    data["ok"] = _composio_ok(raw)
    data["file_id"] = _first_id(raw)
    data["folder_id"] = folder_id
    return data


def _emails_from_people(payload: Any) -> list[tuple[str, str]]:
    """Return (display_name, email) pairs from GMAIL_SEARCH_PEOPLE."""
    out: list[tuple[str, str]] = []
    people: list[Any] = []
    for layer in _payload_layers(payload):
        for key in ("results", "people", "connections", "otherContacts"):
            val = layer.get(key)
            if isinstance(val, list):
                people.extend(val)
        person = layer.get("person")
        if isinstance(person, dict):
            people.append(person)
    for person in people:
        if not isinstance(person, dict):
            continue
        wrapped = isinstance(person.get("person"), dict) and "emailAddresses" not in person and "names" not in person
        if wrapped:
            person = person["person"]
        names = person.get("names") or []
        display = ""
        if isinstance(names, list) and names:
            display = str(names[0].get("displayName") or names[0].get("givenName") or "")
        addrs = person.get("emailAddresses") or person.get("email_addresses") or []
        if isinstance(addrs, dict):
            addrs = [addrs]
        for addr in addrs if isinstance(addrs, list) else []:
            email = ""
            if isinstance(addr, dict):
                email = str(addr.get("value") or addr.get("email") or "")
            elif isinstance(addr, str):
                email = addr
            if email and "@" in email:
                out.append((display, email))
    return out


async def lookup_gmail_person(settings: Settings, name: str) -> str | None:
    """Resolve a spoken first name via Gmail contacts / Other Contacts."""
    query = (name or "").strip()
    if not query or "@" in query:
        return None
    raw = await execute(
        settings,
        "GMAIL_SEARCH_PEOPLE",
        {"query": query, "page_size": 8, "other_contacts": True},
    )
    pairs = _emails_from_people(raw)
    if not pairs:
        return None
    needle = query.lower()
    exact = [email for display, email in pairs if needle in (display or "").lower()]
    chosen = exact[0] if exact else pairs[0][1]
    logger.info("Gmail people %r → %s (%s matches)", query, chosen, len(pairs))
    return chosen


async def send_gmail(
    settings: Settings,
    recipient: str,
    subject: str,
    body: str,
    attachment: Path | list[Path] | None = None,
) -> dict[str, Any]:
    to = (recipient or "").strip()
    if not to or to.lower() == "me":
        raise ValueError("Gmail needs a named recipient, not me.")
    args: dict[str, Any] = {
        "recipient_email": to,
        "subject": subject,
        "body": body,
        "is_html": False,
    }
    files: list[str] = []
    if isinstance(attachment, list):
        files = [str(Path(p).resolve()) for p in attachment if p]
    elif attachment:
        files = [str(Path(attachment).resolve())]
    if len(files) == 1:
        args["attachment"] = files[0]
    elif len(files) > 1:
        args["attachment"] = files
    raw = await execute(settings, "GMAIL_SEND_EMAIL", args)
    data = _as_dict(raw)
    data["ok"] = _composio_ok(raw)
    return data
