"""Allowlist for the eight connected Composio apps."""

from __future__ import annotations

APPS: dict[str, dict[str, str]] = {
    "gmail": {"label": "Gmail", "color": "#EA4335"},
    "googledrive": {"label": "Google Drive", "color": "#4285F4"},
    "googlephotos": {"label": "Google Photos", "color": "#E4405F"},
    "googlecalendar": {"label": "Google Calendar", "color": "#4285F4"},
    "googlesheets": {"label": "Google Sheets", "color": "#34A853"},
    "notion": {"label": "Notion", "color": "#000000"},
    "github": {"label": "GitHub", "color": "#181717"},
    "figma": {"label": "Figma", "color": "#F24E1E"},
}

# Longer phrases first so "google drive" wins over "drive".
ALIASES: list[tuple[str, str]] = [
    ("google photos", "googlephotos"),
    ("google photo", "googlephotos"),
    ("google drive", "googledrive"),
    ("google calendar", "googlecalendar"),
    ("google sheets", "googlesheets"),
    ("google sheet", "googlesheets"),
    ("gdrive", "googledrive"),
    ("e-mail", "gmail"),
    ("email", "gmail"),
    ("inbox", "gmail"),
    ("gmail", "gmail"),
    ("photos", "googlephotos"),
    ("drive", "googledrive"),
    ("calendar", "googlecalendar"),
    ("sheets", "googlesheets"),
    ("notion", "notion"),
    ("github", "github"),
    ("git hub", "github"),
    ("figma", "figma"),
]

DISCONNECTED: list[tuple[str, str]] = [
    ("slack", "Slack"),
    ("outlook", "Outlook"),
    ("teams", "Teams"),
    ("dropbox", "Dropbox"),
    ("linear", "Linear"),
    ("jira", "Jira"),
]

IMAGE_SET = ("googlephotos", "googledrive", "figma", "gmail")
PICKUP_SET = ("gmail", "notion", "github", "googlecalendar", "googlesheets", "googledrive")
PUBLISH_SET = ("gmail", "googledrive", "notion", "github", "googlecalendar", "googlesheets")


def chip(slug: str, status: str = "queued") -> dict[str, str]:
    meta = APPS.get(slug, {"label": slug, "color": "#888888"})
    return {"slug": slug, "label": meta["label"], "status": status, "color": meta["color"]}
