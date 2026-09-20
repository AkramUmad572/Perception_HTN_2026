#!/usr/bin/env python3
"""Gemini image-edit client tests — mocked HTTP, no live API."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesh.edit import EditError, edit_image


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            req = httpx.Request("POST", "https://generativelanguage.googleapis.com/")
            raise httpx.HTTPStatusError("err", request=req, response=self)

    def json(self):
        return self._payload


def _settings():
    return SimpleNamespace(gemini_api_key="k", gemini_image_model="gemini-2.5-flash-image")


def _ok_payload(raw: bytes, key: str = "inlineData"):
    blob = {"mimeType": "image/png", "data": base64.b64encode(raw).decode()}
    return {"candidates": [{"content": {"parts": [{key: blob}]}}]}


def _check(name, cond, detail=""):
    if cond:
        print(f"  [ok] {name}")
        return 1, 0
    print(f"  [FAIL] {name} {detail}")
    return 0, 1


def _add(total, result):
    return total[0] + result[0], total[1] + result[1]


def test_returns_edited_bytes():
    print("\n=== Test: edit_image returns the image part ===")
    seen = {}

    async def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        return _Resp(_ok_payload(b"EDITED"))

    client = SimpleNamespace(post=fake_post)
    with patch("mesh.edit.get_http_client", lambda: client):
        out = asyncio.run(edit_image(b"SRC", "give it wings", _settings()))

    t = _check("returns the decoded image bytes", out == b"EDITED", out)
    t = _add(t, _check("uses the configured image model",
                       "gemini-2.5-flash-image" in seen["url"], seen["url"]))
    parts = seen["json"]["contents"][0]["parts"]
    t = _add(t, _check("sends the instruction text",
                       any("wings" in p.get("text", "") for p in parts)))
    t = _add(t, _check("sends the source image inline",
                       any("inline_data" in p for p in parts)))
    t = _add(t, _check("tells the model not to draw the circle",
                       any("red circle" in p.get("text", "") for p in parts)))
    return t


def test_accepts_snake_case_inline_data():
    """The REST API has used both spellings; accept either."""
    print("\n=== Test: inline_data is accepted as well as inlineData ===")

    async def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(_ok_payload(b"SNAKE", key="inline_data"))

    client = SimpleNamespace(post=fake_post)
    with patch("mesh.edit.get_http_client", lambda: client):
        out = asyncio.run(edit_image(b"SRC", "give it wings", _settings()))
    return _check("reads inline_data too", out == b"SNAKE", out)


def test_missing_image_part_is_speakable():
    print("\n=== Test: a text-only reply raises a speakable error ===")

    async def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({"candidates": [{"content": {"parts": [{"text": "I can't."}]}}]})

    client = SimpleNamespace(post=fake_post)
    t = (0, 0)
    with patch("mesh.edit.get_http_client", lambda: client):
        try:
            asyncio.run(edit_image(b"SRC", "give it wings", _settings()))
            t = _add(t, _check("raises EditError", False, "no exception"))
        except EditError as err:
            msg = str(err)
            t = _add(t, _check("raises EditError", True))
            # Spoken aloud: no paths, no exception class names, no markdown.
            t = _add(t, _check("message is speakable",
                               "/" not in msg and "Error" not in msg and "`" not in msg, msg))
    return t


def test_http_error_is_speakable():
    print("\n=== Test: an HTTP error raises a speakable error ===")

    async def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({"error": "boom"}, status_code=500)

    client = SimpleNamespace(post=fake_post)
    t = (0, 0)
    with patch("mesh.edit.get_http_client", lambda: client):
        try:
            asyncio.run(edit_image(b"SRC", "give it wings", _settings()))
            t = _add(t, _check("raises EditError", False, "no exception"))
        except EditError as err:
            t = _add(t, _check("raises EditError", True))
            t = _add(t, _check("message is speakable", "/" not in str(err), str(err)))
    return t


def test_no_key_raises_before_any_call():
    print("\n=== Test: a missing key raises before any HTTP call ===")
    called = {"n": 0}

    async def fake_post(*a, **k):
        called["n"] += 1
        return _Resp(_ok_payload(b"X"))

    s = SimpleNamespace(gemini_api_key="", gemini_image_model="m")
    t = (0, 0)
    with patch("mesh.edit.get_http_client", lambda: SimpleNamespace(post=fake_post)):
        try:
            asyncio.run(edit_image(b"SRC", "x", s))
            t = _add(t, _check("raises without a key", False))
        except EditError:
            t = _add(t, _check("raises without a key", True))
    t = _add(t, _check("no HTTP call was made", called["n"] == 0, called["n"]))
    return t


def test_key_is_not_in_the_error_message():
    """The reply is spoken and logged; a key must never leak into it."""
    print("\n=== Test: the API key never appears in an error ===")

    async def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({"error": "boom"}, status_code=403)

    s = SimpleNamespace(gemini_api_key="SECRETKEY123", gemini_image_model="m")
    t = (0, 0)
    with patch("mesh.edit.get_http_client", lambda: SimpleNamespace(post=fake_post)):
        try:
            asyncio.run(edit_image(b"SRC", "x", s))
            t = _add(t, _check("raises", False))
        except EditError as err:
            t = _add(t, _check("raises", True))
            t = _add(t, _check("key not in message", "SECRETKEY123" not in str(err), str(err)))
    return t


def run_all_tests():
    print("=" * 60)
    print("GEMINI IMAGE EDIT TESTS")
    print("=" * 60)
    total = (0, 0)
    for fn in TESTS:
        try:
            total = _add(total, fn())
        except Exception as exc:
            import traceback

            traceback.print_exc()
            print(f"  [FAIL] {fn.__name__} crashed: {exc}")
            total = _add(total, (0, 1))
    passed, failed = total
    print("\n" + "=" * 60)
    print(f"TOTAL: {passed}/{passed + failed} passed")
    if failed:
        print(f"\n{failed} TESTS FAILED")
        return 1
    print("\nALL TESTS PASSED")
    return 0


TESTS = [
    test_returns_edited_bytes,
    test_accepts_snake_case_inline_data,
    test_missing_image_part_is_speakable,
    test_http_error_is_speakable,
    test_no_key_raises_before_any_call,
    test_key_is_not_in_the_error_message,
]


if __name__ == "__main__":
    sys.exit(run_all_tests())
