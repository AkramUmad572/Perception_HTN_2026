#!/usr/bin/env python3
"""Drive download falls back to the public export URL when alt=media 403s."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from photos.drive import download_file, preview_url, valid_file_id


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def test_preview_url() -> tuple[int, int]:
    print("\n=== Test: preview_url is same-origin ===")
    got = preview_url("1abc_DEF-123")
    if got == "/api/photos/1abc_DEF-123/preview" and valid_file_id("1abc_DEF-123"):
        print("  [ok] proxy path")
        return 1, 0
    print(f"  [FAIL] {got}")
    return 0, 1


def test_uc_fallback(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: alt=media 403 falls back to uc export ===")
    settings = SimpleNamespace(
        google_drive_api_key="fake",
        ref_dir=tmp_path,
    )

    api = MagicMock()
    api.status_code = 403
    api.headers = {"content-type": "text/html"}
    api.text = "Sorry..."
    api.content = b"<html>Sorry</html>"

    uc = MagicMock()
    uc.status_code = 200
    uc.headers = {"content-type": "image/png"}
    uc.content = PNG
    uc.text = ""

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[api, uc])

    async def run() -> tuple[bytes, str]:
        with patch("photos.drive.get_http_client", return_value=client):
            return await download_file("1GNahJZiiyHyA_pR41lIa0OWbfnjPR_MS", settings)

    raw, mime = asyncio.run(run())
    if mime == "image/png" and raw == PNG and (tmp_path / "drive_1GNahJZiiyHyA_pR41lIa0OWbfnjPR_MS_raw.png").exists():
        print("  [ok] cached PNG from uc export")
        return 1, 0
    print(f"  [FAIL] mime={mime} bytes={len(raw)}")
    return 0, 1


if __name__ == "__main__":
    import tempfile

    p, f = test_preview_url()
    with tempfile.TemporaryDirectory() as d:
        a, b = test_uc_fallback(Path(d))
        p += a
        f += b
    print(f"\n{p} passed, {f} failed")
    raise SystemExit(f > 0)
