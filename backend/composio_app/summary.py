"""Short spoken summaries for pulled Gmail threads."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import Settings
from app.httpclient import get_http_client

logger = logging.getLogger(__name__)


def _fallback(items: list[dict[str, Any]], topic: str, days: int) -> str:
    first = items[0]
    who = first.get("sender_name") or first.get("subtitle") or "Someone"
    who = re.split(r"[<@]", str(who), maxsplit=1)[0].strip() or "Someone"
    body = re.sub(r"\s+", " ", str(first.get("body") or first.get("title") or "")).strip()
    if body:
        return f"{who} wrote: {body[:160]}"
    who_topic = f" about {topic}" if topic else ""
    return f"I found {len(items)} email{'s' if len(items) != 1 else ''} from the last {days} days{who_topic}."


async def summarize_emails(
    items: list[dict[str, Any]],
    settings: Settings,
    topic: str = "",
    days: int = 2,
) -> tuple[str, str | None]:
    """Return (spoken_summary, suggested_reply)."""
    if not items:
        return "I didn't find any matching emails.", None
    if not settings.gemini_api_key:
        return _fallback(items, topic, days), None

    blobs = []
    for it in items[:3]:
        body = re.sub(r"\s+", " ", str(it.get("body") or ""))[:360]
        blobs.append(
            f"From: {it.get('sender_name') or it.get('subtitle')}\n"
            f"Subject: {it.get('title')}\n"
            f"Body: {body}"
        )
    about = f" The user asked about: {topic}." if topic else ""
    prompt = (
        "You are Percy, a headset assistant. Summarize these inbox emails "
        "in 1-2 short spoken sentences. Lead with the most personal ask. "
        "Say who wrote and what they want, including any deadline. "
        "No lists, no subject lines, no markdown. Max 40 words."
        f"{about}\n\n"
        + "\n\n---\n\n".join(blobs)
        + "\n\nThen add a line: REPLY: <one short suggested reply>"
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 220,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        client = get_http_client()
        resp = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=12.0,
        )
        resp.raise_for_status()
        raw = (
            resp.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        spoken, suggestion = text, None
        if "REPLY:" in text:
            spoken, suggestion = text.split("REPLY:", 1)
            suggestion = re.sub(r"\s+", " ", suggestion).strip() or None
        if spoken.strip().startswith("{"):
            try:
                data = json.loads(spoken)
                spoken = str(data.get("spoken") or spoken)
                suggestion = suggestion or data.get("suggested_reply")
            except json.JSONDecodeError:
                m = re.search(r'"spoken"\s*:\s*"((?:\\.|[^"\\])*)"', text)
                if m:
                    spoken = m.group(1)
        spoken = re.sub(r"\s+", " ", spoken).strip().strip('"')
        if len(spoken) >= 24:
            return spoken[:280], (str(suggestion).strip()[:140] if suggestion else None)
        logger.warning("Email summary too short: %r", spoken)
    except Exception as exc:
        logger.warning("Email summary failed: %s", exc)
    return _fallback(items, topic, days), None
