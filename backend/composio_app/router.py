"""Named-app voice router. Runs before the old Drive photo regex."""

from __future__ import annotations

import re
from typing import Any

from app.models import Intent
from composio_app.apps import ALIASES, APPS, DISCONNECTED, IMAGE_SET, PICKUP_SET, PUBLISH_SET

_ALL_PHOTOS = re.compile(
    r"\b(?:all|every|across)\b.+\b(?:photos?|pictures?|pics|images?)\b"
    r"|\b(?:photos?|pictures?)\b.+\b(?:all|every|across)\b.+\b(?:apps?|accounts?)\b",
    re.I,
)
_LAST_EMAIL = re.compile(r"\b(?:last|latest|most recent|newest)\b.+\b(?:e-?mail|inbox)\b|\b(?:e-?mail|inbox)\b.+\b(?:last|latest)\b", re.I)
_EMAIL_SEARCH = re.compile(
    r"\bemails\b"
    r"|\b(?:give me|pull up|check|get)\b.+\bcontext\b"
    r"|\bcontext\b.+\b(?:about|on|for|pertaining|related)"
    r"|\be-?mail\b.+\b(?:about|related|regarding|pertaining|context|days|subject|search|today)"
    r"|\b(?:about|related|regarding|pertaining|context|days|subject|today)\b.+\be-?mails?\b",
    re.I,
)
_DAYS = re.compile(
    r"\b(?:past|last|previous)\s+(\d+)\s+days?\b|\b(\d+)\s+days?\b|\b(?:past|last|previous)\s+few\s+days\b",
    re.I,
)
_HOURS = re.compile(
    r"\b(?:past|last|previous)\s+(?:twenty[-\s]?four|\d+)\s+hours?\b|\b(?:24|twenty[-\s]?four)\s+hours?\b",
    re.I,
)
_TODAY = re.compile(r"\b(?:today|tonight|this morning|this afternoon)\b", re.I)
_TOPIC = re.compile(
    r"\b(?:related to|about|regarding|pertaining to|involving|to do with|on the subject of|subject)\s+(.+)$",
    re.I,
)
_OBJECT_REF = re.compile(r"\b(?:this|that|the)\s+(?:object|model|thing|part|one)\b|\bthis object\b", re.I)
_TOPIC_STOP = {
    "my", "the", "this", "that", "object", "model", "thing", "part", "one", "emails",
    "email", "gmail", "inbox", "please", "hey", "percy", "can", "you", "me", "from",
    "past", "last", "days", "day", "related", "context", "give", "pull", "up", "and",
    "yellow", "red", "blue", "green", "black", "white", "built", "build", "make",
    "today", "tonight", "any", "pertaining",
    "twenty", "four", "hours", "hour", "week", "weeks",
    "for", "september", "october", "november", "december", "january",
    "february", "august", "june", "july", "april", "march",
    "sep", "sept", "oct", "nov", "dec", "jan", "feb", "aug",
}
_PHOTOS = re.compile(r"\b(?:photos?|pictures?|pics|images?)\b", re.I)
_PUBLISH = re.compile(r"\b(?:email the team|send (?:this|it)|upload|publish|share this|note (?:it )?in notion|put the model)\b", re.I)
_PICKUP = re.compile(
    r"\b(?:what (?:should|do) i (?:work on|pick up|do)|check (?:gmail|notion|github)|figure out what i need)\b",
    re.I,
)
_BUILD_THAT = re.compile(r"\b(?:yeah,?\s*)?(?:build|make|sculpt) that\b", re.I)
_CONNECTIONS = re.compile(
    r"\bwhat\s+(?:are\s+your|apps?\s+are\s+you)\s+connect(?:ed|ions?)\b"
    r"|\bwhat\s+connections?\s+do\s+you\s+have\b"
    r"|\blist\s+(?:your\s+|my\s+)?connections?\b"
    r"|\bwhat\s+(?:integrations?|accounts?)\s+(?:are\s+you\s+connected\s+to|do\s+you\s+have)\b"
    r"|\bwhich\s+apps?\s+are\s+you\s+connected\s+to\b"
    r"|\bwhat\s+apps?\s+(?:do\s+you\s+have\s+connected|are\s+connected)\b",
    re.I,
)


def _tokens(text: str) -> str:
    """Lowercase and treat punctuation as spaces so 'Notion?' still matches."""
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "


def _named_apps(text: str) -> list[str]:
    found: list[str] = []
    hay = _tokens(text)
    for phrase, slug in ALIASES:
        needle = _tokens(phrase).strip()
        if not needle:
            continue
        if f" {needle} " in hay:
            if slug not in found:
                found.append(slug)
            hay = hay.replace(f" {needle} ", " ")
    return found


def _disconnected(text: str) -> str | None:
    hay = _tokens(text)
    for phrase, label in DISCONNECTED:
        needle = _tokens(phrase).strip()
        if needle and f" {needle} " in hay:
            return label
    return None


_MONTH = (
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE_PHRASE = re.compile(
    rf"\b(?:on|for)\s+(?:{_MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?"
    rf"|\b(?:{_MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    re.I,
)
_MONTH_NUM = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _normalize_email_text(text: str) -> str:
    t = text or ""
    t = re.sub(r"\bhockton(?:\s+north)?\b", "hack the north", t, flags=re.I)
    t = re.sub(
        r"\b(?:hyphen|hocks?|hacks?|hacked|hock|hac|hoc|hackathon|act)\s+"
        r"(?:and\s+)?(?:on\s+|the\s+)?(?:north|notes)\b",
        "hack the north",
        t,
        flags=re.I,
    )
    t = re.sub(r"\bhacks?\s+and\s+notes\b", "hack the north", t, flags=re.I)
    t = re.sub(r"\bhtn\b", "hack the north", t, flags=re.I)
    return t


def _email_on_date(text: str) -> str | None:
    """Exact local calendar day when the user named one (or said today)."""
    from datetime import date

    m = re.search(rf"({_MONTH})\s+(\d{{1,2}})", text or "", re.I)
    if m:
        month = _MONTH_NUM.get(m.group(1).lower())
        try:
            day = int(m.group(2))
            today = date.today()
            return date(today.year, month or today.month, day).strftime("%Y/%m/%d")
        except ValueError:
            return date.today().strftime("%Y/%m/%d")
    if _DAYS.search(text or ""):
        return None
    if _TODAY.search(text or "") or _HOURS.search(text or ""):
        return date.today().strftime("%Y/%m/%d")
    return None


def _email_days(text: str) -> int:
    if _email_on_date(text or ""):
        return 1
    m = _DAYS.search(text or "")
    if m:
        n = m.group(1) or m.group(2)
        if not n:
            return 3
        try:
            return max(1, min(int(n), 30))
        except (TypeError, ValueError):
            return 2
    return 2


def _topic_from_object(hint: str | None) -> str:
    words = re.findall(r"[a-z0-9]{3,}", (hint or "").lower())
    keep = [w for w in words if w not in _TOPIC_STOP][:4]
    return " ".join(keep)


def _email_topic(text: str, object_hint: str | None) -> str:
    text = _normalize_email_text(text or "")
    if _OBJECT_REF.search(text):
        return _topic_from_object(object_hint)
    m = _TOPIC.search(text)
    if m:
        raw = m.group(1)
        raw = _DATE_PHRASE.sub(" ", raw)
        raw = re.split(
            r"\b(?:from|in|on|over|for)\s+(?:gmail|my inbox|the inbox|the\s+)?(?:past|last|previous|today)\b",
            raw,
            maxsplit=1,
            flags=re.I,
        )[0]
        words = [w for w in re.findall(r"[a-z0-9]{3,}", raw.lower()) if w not in _TOPIC_STOP and not w.isdigit()]
        if words:
            return " ".join(words[:5])
    return ""


def route_composio(text: str, *, has_brief: bool = False, object_hint: str | None = None) -> Intent | None:
    t = (text or "").strip()
    if not t:
        return None

    if _BUILD_THAT.search(t) and has_brief:
        return Intent(action="build_from_brief", backend="cad", reply="Building that from the brief.")

    if _CONNECTIONS.search(t):
        return Intent(action="list_connections", backend="mesh", reply="Let me check, sir…")

    blocked = _disconnected(t)
    if blocked:
        return Intent(
            action="clarify",
            backend="mesh",
            reply=f"I don't have {blocked} connected.",
        )

    apps = _named_apps(t)

    if _PUBLISH.search(t) and apps:
        named = [a for a in apps if a in PUBLISH_SET]
        if named:
            return Intent(
                action="publish_work",
                backend="mesh",
                reply="Sending this out…",
                apps=named,
                pull_kind="publish",
            )

    if _ALL_PHOTOS.search(t) or (
        _PHOTOS.search(t) and re.search(r"\b(?:all|every|across)\b", t, re.I)
    ):
        return Intent(
            action="image_find",
            backend="mesh",
            reply="Looking through your photos…",
            apps=list(IMAGE_SET),
            pull_kind="photos",
            photo_query=t,
        )

    cleaned = _normalize_email_text(t)
    has_window = bool(
        _DAYS.search(t) or _HOURS.search(t) or _TODAY.search(t) or _DATE_PHRASE.search(t)
        or _TOPIC.search(t) or _TOPIC.search(cleaned) or _OBJECT_REF.search(t)
    )
    if _LAST_EMAIL.search(t) and not has_window:
        return Intent(
            action="pull_app",
            backend="mesh",
            reply="Opening Gmail…",
            apps=["gmail"],
            pull_kind="last_email",
        )
    wants_email_search = bool(_EMAIL_SEARCH.search(t) or _EMAIL_SEARCH.search(cleaned)) or (
        "gmail" in apps and has_window
    ) or (
        "hack the north" in cleaned.lower()
        and bool(re.search(r"\b(?:context|mail|inbox|about|pertaining)\b", t, re.I))
    )
    if wants_email_search:
        days = _email_days(t)
        on_date = _email_on_date(t)
        topic = _email_topic(t, object_hint)
        reply = "Checking your email…" if not topic else f"Looking for mail about {topic}…"
        params: dict[str, Any] = {"days": days}
        if on_date:
            params["on_date"] = on_date
        return Intent(
            action="pull_app",
            backend="mesh",
            reply=reply,
            apps=["gmail"],
            pull_kind="email_search",
            photo_query=topic or None,
            params=params,
        )

    if _LAST_EMAIL.search(t) or (apps == ["gmail"] and re.search(r"\b(?:last|latest|recent)\b", t, re.I)):
        return Intent(
            action="pull_app",
            backend="mesh",
            reply="Opening Gmail…",
            apps=["gmail"],
            pull_kind="last_email",
        )

    if apps:
        kind = "photos" if _PHOTOS.search(t) else "list"
        if apps == ["gmail"] and kind != "photos":
            kind = "last_email"
        from composio_app.adapters import drive_search_terms

        if "googledrive" in apps and (_PHOTOS.search(t) or drive_search_terms(t)):
            if _PHOTOS.search(t):
                apps = ["googledrive"]
            kind = "photos"
        if apps == ["googlephotos"]:
            kind = "photos"
        first_label = APPS.get(apps[0], {}).get("label", apps[0])
        labels = {
            "photos": "Looking through your photos…",
            "last_email": "Opening Gmail…",
            "list": f"Opening {first_label}…",
        }
        return Intent(
            action="pull_app",
            backend="mesh",
            reply=labels.get(kind, "On it."),
            apps=apps,
            pull_kind=kind,
            photo_query=t if kind == "photos" else None,
        )

    if _PICKUP.search(t):
        return Intent(
            action="pickup_work",
            backend="mesh",
            reply="Checking what the team asked for…",
            apps=list(PICKUP_SET),
            pull_kind="list",
        )

    return None
