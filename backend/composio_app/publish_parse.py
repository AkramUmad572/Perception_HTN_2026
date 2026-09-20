"""Split one spoken sentence into export / Drive / Gmail slots."""

from __future__ import annotations

import re
from typing import Any

from composio_app.adapters import drive_folder_hint

_EMAIL_ADDR = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
_NAME = r"[A-Za-z][A-Za-z0-9._\-]{0,30}"
_BAD_RECIPIENTS = {
    "them", "the", "team", "someone", "everybody", "everyone", "it", "this",
    "me", "us", "him", "her", "my", "a", "an", "please", "and", "then",
    "to", "from", "saying", "gmail", "email", "send", "that", "i",
}
_EXPORT = re.compile(
    r"\bexport\b|\b(?:to|as)\s+(?:an?\s+)?(?:stl|step|stp)\b",
    re.I,
)
_PRINT = re.compile(r"\b(?:3-?d\s+)?print(?:ing|er|able)?\b|\bslicer\b", re.I)
_EXPLICIT_STL = re.compile(r"\b(?:to|as)\s+(?:an?\s+)?stl\b|\bstl\s+file\b", re.I)
_EXPLICIT_STEP = re.compile(r"\b(?:to|as)\s+(?:an?\s+)?(?:step|stp)\b|\b(?:step|stp)\s+file\b", re.I)
_SHARE = re.compile(r"\b(?:e-?mail|gmail|notion|share)\b|\bsend\s+(?:this|it|the\s+model)\b", re.I)
_SEND_SEARCH = re.compile(
    r"\b(?:last|latest|recent)\s+e-?mails?\b"
    r"|\be-?mails?\s+(?:about|from|related|pertaining|for)\b"
    r"|\b(?:pull|check|open|context|give me)\b.+\be-?mails?\b",
    re.I,
)
_SEND_EMAIL = re.compile(
    rf"\b(?:e-?mail|send)\s+(?:them|the\s+team|{_EMAIL_ADDR}|{_NAME})\b"
    r"|\b(?:e-?mail|send)\b.+\b(?:saying|tell them|that says)\b"
    r"|\bsend\s+(?:this|it)(?:\s+(?:model|file))?\s+to\b"
    r"|\bsend\s+the\s+(?:model|file)\s+to\b",
    re.I,
)
_RECIPIENT = re.compile(
    rf"\b(?:e-?mail|send)\s+(?:an?\s+(?:e-?mail|message)\s+)?(?:to\s+)?({_EMAIL_ADDR})"
    rf"|\b(?:e-?mail|send)\s+(?:an?\s+(?:e-?mail|message)\s+)?to\s+(?!the\s+team\b)({_NAME})"
    rf"|\b(?:e-?mail)\s+(?!the\s+team\b)(?!them\b)(?!saying\b)(?!to\b)(?!from\b)({_NAME})"
    rf"|\bsend\s+(?:this|it)(?:\s+(?:model|file))?\s+to\s+({_EMAIL_ADDR}|{_NAME})"
    rf"|\bsend\s+the\s+(?:model|file)\s+to\s+({_EMAIL_ADDR}|{_NAME})",
    re.I,
)
_SAYING = re.compile(r"\b(?:saying|tell them|that says)\s+(.+)$", re.I)
# Attaching the model is opt-in: a plain "email John saying X" must never
# require a model to exist. Only an explicit export/print cue, or explicitly
# naming "the model"/"this file"/"attach", pulls the model in.
_EXPLICIT_ATTACH = re.compile(
    r"\bsend\s+(?:this|it|the)\s+(?:model|file)\b|\battach(?:ed|ing)?\b",
    re.I,
)
_NAME_IT = re.compile(r"\bname(?:d)?\s+it\s+([A-Za-z0-9][A-Za-z0-9_\-]*)", re.I)
_AS_NAME = re.compile(
    r"\bas\s+(?!an?\s)(?!stl\b)(?!step\b)(?!stp\b)([A-Za-z0-9][A-Za-z0-9_\-]*)",
    re.I,
)
_SIGNOFF = re.compile(
    r"(?:,?\s*(?:and\s+)?(?:send\s+)?)((?:best\s+)?regards(?:\s+from)?\s+.+)$"
    r"|\bsincerely,?\s+(.+)$",
    re.I,
)
_DRIVE_VERB = re.compile(r"\b(?:store|save|upload|put)\b", re.I)
_DRIVE_PLACE = re.compile(r"\b(?:drive|folder|google)\b", re.I)
_PUT_IN = re.compile(
    r"\b(?:store|save|upload|put)\s+(?:this|it|the\s+\w+)?\s*"
    r"(?:in(?:to)?|on)\s+(?:my\s+|the\s+)?([A-Za-z0-9][A-Za-z0-9_\-]*)",
    re.I,
)
_FOLDER_STOP = {"google", "drive", "my", "the", "this", "stl", "step", "stp", "gmail", "email"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _recipient_of(text: str) -> str | None:
    m = _RECIPIENT.search(text or "")
    if not m:
        return None
    raw = next((g for g in m.groups() if g), "")
    name = raw.strip(" .,;:")
    if not name or name.lower() in _BAD_RECIPIENTS:
        return None
    return name


def _signoff_of(text: str) -> str | None:
    m = _SIGNOFF.search(text or "")
    if not m:
        return None
    raw = next((g for g in m.groups() if g), "")
    raw = _clean(raw).strip(" .,")
    if not raw:
        return None
    if raw.lower().startswith("regards"):
        raw = "Best " + raw
    elif raw.lower().startswith("best"):
        raw = "Best" + raw[4:]
    return raw


def _strip_commands(text: str) -> str:
    t = text or ""
    t = _SIGNOFF.sub(" ", t)
    t = re.sub(
        r"\bexport(?:\s+(?:this|it|the\s+\w+))?(?:\s+to\s+(?:an?\s+)?(?:stl|step|stp))?",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(
        rf"\b(?:e-?mail|send)\s+(?:this\s+to\s+|it\s+to\s+)?(?:{_EMAIL_ADDR}|the\s+team|them|{_NAME})",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"\b(?:store|upload|save|put)\s+(?:this|it|the\s+\w+)?\s*"
        r"(?:in(?:to)?\s+)?(?:my\s+|the\s+)?(?:google\s+)?drive\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\b(?:store|upload|save|put)\s+(?:this|it)?\b", " ", t, flags=re.I)
    t = re.sub(r"\bin\s+(?:my\s+|the\s+)?.+?\s+folder", " ", t, flags=re.I)
    t = re.sub(r"\b(?:name(?:d)?\s+it|as)\s+[A-Za-z0-9._\-]+", " ", t, flags=re.I)
    t = re.sub(r"\b(?:to\s+(?:an?\s+)?(?:stl|step|stp)|attach(?:\s+it)?)\b", " ", t, flags=re.I)
    t = re.sub(r"\b(?:and|then|also)\b", " ", t, flags=re.I)
    return _clean(t).strip(" ,.")


def _body_of(text: str) -> str:
    m = _SAYING.search(text or "")
    raw = m.group(1) if m else (text or "")
    body = _strip_commands(raw)
    return body or "We finished the model."


def _wants_email(text: str) -> bool:
    if _SEND_SEARCH.search(text or ""):
        return False
    return bool(_SEND_EMAIL.search(text or ""))


def _folder_of(text: str) -> str | None:
    hinted = drive_folder_hint(text)
    if hinted:
        return hinted
    m = _PUT_IN.search(text or "")
    if not m:
        return None
    name = m.group(1).lower()
    if name in _FOLDER_STOP:
        return None
    return name


def _wants_drive(text: str) -> bool:
    if _folder_of(text):
        return True
    return bool(_DRIVE_VERB.search(text or "") and _DRIVE_PLACE.search(text or ""))


def choose_export_formats(text: str, *, wants_email: bool = False) -> dict[str, Any]:
    """Print owns STL, send owns STEP. Both verbs → both files. Spoken format wins."""
    t = text or ""
    explicit_stl = bool(_EXPLICIT_STL.search(t))
    explicit_step = bool(_EXPLICIT_STEP.search(t))
    wants_print = bool(_PRINT.search(t))
    wants_send = bool(wants_email or _SHARE.search(t))
    if explicit_stl or explicit_step:
        formats = [fmt for fmt, hit in (("stl", explicit_stl), ("step", explicit_step)) if hit]
        reason = "explicit"
    elif wants_print and wants_send:
        formats = ["stl", "step"]
        reason = "both"
    elif wants_print:
        formats = ["stl"]
        reason = "print"
    elif wants_send:
        formats = ["step"]
        reason = "send"
    else:
        formats = ["stl"]
        reason = "default"
    return {
        "formats": formats,
        "reason": reason,
        "print": wants_print,
        "send": wants_send,
    }


def choose_export_format(text: str, *, wants_email: bool = False) -> tuple[str, str]:
    chosen = choose_export_formats(text, wants_email=wants_email)
    return chosen["formats"][0], chosen["reason"]


def parse_publish(text: str) -> dict[str, Any] | None:
    t = _clean(text)
    if not t:
        return None
    wants_export = bool(_EXPORT.search(t) or _PRINT.search(t))
    wants_email = _wants_email(t)
    wants_drive = _wants_drive(t)
    if not (wants_export or wants_email or wants_drive):
        return None

    chosen = choose_export_formats(t, wants_email=wants_email)
    formats = list(chosen["formats"])
    fmt = formats[0]
    reason = chosen["reason"]
    wants_print = bool(chosen["print"])
    recipient = _recipient_of(t) if wants_email else None
    filename = None
    named = _NAME_IT.search(t) or _AS_NAME.search(t)
    if named:
        filename = named.group(1)
    # Print / bare export land on Drive. Email-only stays local unless they named Drive or print.
    drive = wants_drive or wants_print or (wants_export and not wants_email)
    # Attaching the model is opt-in — "email John saying the demo is ready"
    # must not require a model to exist. Only an explicit export/print cue,
    # or explicitly naming "the model"/"this file"/"attach", pulls it in.
    attach = wants_export or bool(_EXPLICIT_ATTACH.search(t))
    return {
        "format": fmt,
        "formats": formats,
        "format_reason": reason,
        "format_explicit": reason == "explicit",
        "export": attach or drive,
        "drive": drive,
        "gmail": bool(wants_email and recipient),
        "wants_email": wants_email,
        "folder": _folder_of(t),
        "filename": filename,
        "recipient": recipient,
        "attach": attach,
        "signoff": _signoff_of(t) if wants_email else None,
        "email_body": _body_of(t) if wants_email else None,
    }


def compose_email_body(spec: dict[str, Any]) -> str:
    body = (spec.get("email_body") or "We finished the model.").strip()
    signoff = (spec.get("signoff") or "").strip()
    if signoff:
        return f"{body}\n\n{signoff}"
    return body


def resolve_recipient(spoken: str | None, last_items: list[dict[str, Any]] | None) -> str | None:
    spoken = (spoken or "").strip()
    if not spoken or spoken.lower() in _BAD_RECIPIENTS:
        return None
    if "@" in spoken:
        return spoken
    needle = spoken.lower()
    for it in last_items or []:
        name = str(it.get("sender_name") or "").lower()
        sub = str(it.get("subtitle") or "")
        title = str(it.get("title") or "").lower()
        body = str(it.get("body") or "").lower()
        if needle not in name and needle not in sub.lower() and needle not in title and needle not in body:
            continue
        from email.utils import parseaddr

        _, addr = parseaddr(sub)
        if addr and "@" in addr:
            return addr
        found = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", sub)
        if found:
            return found.group(0)
    return None
