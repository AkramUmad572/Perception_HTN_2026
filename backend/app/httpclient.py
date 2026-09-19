"""Shared, reused httpx.AsyncClient for outbound API calls.

Gemini and ElevenLabs are hit on every single voice turn. Opening a fresh
`httpx.AsyncClient` per call pays a new TLS handshake to the same host each
time; reusing one client lets httpx keep connections alive between requests.
`app.main` closes this on shutdown.
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # No fixed timeout here — callers pass their own per-request timeout,
        # since STT/TTS/codegen/photo calls each need a different budget.
        _client = httpx.AsyncClient()
    return _client


async def aclose_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
