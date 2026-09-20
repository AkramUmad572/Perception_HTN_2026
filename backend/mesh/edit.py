"""Semantic sculpt edits: edit a render of the model with Gemini.

There is no model that edits a 3D mesh semantically, but there are good ones
that edit images. The headset sends a PNG of what it is looking at, with a
circle drawn where the user pinched; this asks Gemini to make the change
inside that circle and hands the edited image back. Turning that image into a
mesh is app/pipeline.py's job, not this module's.

Verified against the live API before this was written: the source object
survives the edit, the change lands inside the circle, and the circle itself
does not appear in the output.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.config import Settings
from app.httpclient import get_http_client

logger = logging.getLogger(__name__)


class EditError(ValueError):
    """Message is spoken aloud: no paths, no exception names, no markdown."""


_PROMPT = (
    "Edit this picture of a 3D model. {instruction}. "
    "Make the change only inside the red circle. "
    "Do not draw the red circle in your output. "
    "Keep the rest of the object identical, same colours, same proportions, "
    "same camera angle. Plain white background."
)

_NO_IMAGE = "I couldn't picture that change"
_FAILED = "That edit didn't come back"

DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"


def _image_part(payload: dict[str, Any]) -> bytes | None:
    """First inline image in the response, or None. Accepts either key spelling."""
    for cand in payload.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                try:
                    return base64.b64decode(blob["data"])
                except Exception:
                    return None
    return None


async def edit_image(
    png: bytes, instruction: str, settings: Settings, timeout_s: float = 90.0
) -> bytes:
    """
    Return edited image bytes, or raise EditError with a speakable message.

    The key goes in the query string, so nothing from the request is ever put
    into the error text — the message is spoken and logged.
    """
    if not settings.gemini_api_key:
        raise EditError(_FAILED)

    model = getattr(settings, "gemini_image_model", "") or DEFAULT_IMAGE_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={settings.gemini_api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _PROMPT.format(instruction=instruction.strip().rstrip("."))},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(png).decode(),
                        }
                    },
                ]
            }
        ]
    }

    client = get_http_client()
    try:
        resp = await client.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # Log the type only: the URL carries the API key.
        logger.warning("Gemini image edit failed: %s", type(exc).__name__)
        raise EditError(_FAILED) from exc

    raw = _image_part(data)
    if not raw:
        logger.info("Gemini image edit returned no image part")
        raise EditError(_NO_IMAGE)
    return raw
