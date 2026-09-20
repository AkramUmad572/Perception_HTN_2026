#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from composio_app.adapters import (
    _files_from,
    _is_web_image,
    _name_score,
    bytes_from_composio,
    drive_folder_hint,
    drive_search_terms,
    file_refs,
)
from composio_app.router import route_composio


def test_files_from() -> None:
    drive = {"successful": True, "data": {"files": [{"id": "1", "name": "a.png", "mimeType": "image/png"}]}}
    notion = {"results": [{"id": "n1", "url": "https://app.notion.com/p/x"}]}
    sheets = {"data": {"spreadsheets": [{"id": "s1", "name": "Jobs"}]}}
    github = {"repositories": [{"full_name": "a/b"}]}
    assert [f["id"] for f in _files_from(drive)] == ["1"]
    assert [f["id"] for f in _files_from(notion)] == ["n1"]
    assert [f["id"] for f in _files_from(sheets)] == ["s1"]
    assert [f["full_name"] for f in _files_from(github)] == ["a/b"]
    print("ok files_from")


def test_web_image() -> None:
    assert _is_web_image("image/jpeg", "pikachu_keychain")
    assert _is_web_image("image/png", "gear1")
    assert not _is_web_image("image/raf", "DSCF7563.RAF")
    assert not _is_web_image("image/heic", "IMG_0001.HEIC")
    print("ok web_image")


def test_s3_refs() -> None:
    payload = {
        "successful": True,
        "data": {
            "mimeType": "image/jpeg",
            "downloaded_file_content": {
                "mimetype": "image/jpeg",
                "name": "pikachu_keychain",
                "s3url": "https://temp.example.com/pikachu.jpg",
            },
            "display_url": "https://drive.google.com/file/d/13LGdi4itFBtEynQXGUhB3IKB7GoNX4II/view",
        },
    }
    blobs, urls, mime = file_refs(payload)
    assert mime == "image/jpeg"
    assert urls == ["https://temp.example.com/pikachu.jpg"]
    assert not any(isinstance(b, str) and "drive.google.com" in b for b in blobs)
    print("ok s3_refs")


def test_bytes_from_s3() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    payload = {
        "data": {
            "downloaded_file_content": {
                "mimetype": "image/png",
                "s3url": "https://temp.example.com/gear.png",
            }
        }
    }
    resp = SimpleNamespace(status_code=200, content=png, headers={"content-type": "image/png"})
    client = SimpleNamespace(get=AsyncMock(return_value=resp))

    async def run() -> tuple[bytes, str]:
        with patch("composio_app.adapters.get_http_client", return_value=client):
            return await bytes_from_composio(payload)

    raw, mime = asyncio.run(run())
    assert raw == png
    assert mime == "image/png"
    print("ok bytes_from_s3")


def test_bytes_from_b64() -> None:
    raw = b"\xff\xd8\xff" + b"y" * 64
    payload = {"downloaded_file_content": base64.b64encode(raw).decode()}

    async def run() -> tuple[bytes, str]:
        return await bytes_from_composio(payload)

    got, mime = asyncio.run(run())
    assert got == raw
    assert mime == "image/jpeg"
    print("ok bytes_from_b64")


def test_drive_photo_query_keeps_subject() -> None:
    got = route_composio("go through all of my photos across all apps and find pikachu")
    assert got and got.action == "image_find"
    assert "pikachu" in (got.photo_query or "").lower()
    print("ok drive_photo_query")


def test_drive_terms_and_folder() -> None:
    assert drive_search_terms("find me the picture of pikachu_keychain in my google drive photos") == [
        "pikachu_keychain"
    ]
    assert drive_search_terms("find pikachu keychain in my google drive") == ["keychain", "pikachu"]
    assert drive_folder_hint("find gears in the HTN folder on google drive") == "htn"
    assert drive_search_terms("find photos in the HTN folder on google drive") == []
    assert drive_search_terms("find pikachu in the HTN folder") == ["pikachu"]
    assert drive_folder_hint("pull up my google drive photos") is None
    assert _name_score("pikachu_keychain", ["pikachu", "keychain"]) > _name_score(
        "bulbasaur keychain.jpeg", ["pikachu", "keychain"]
    )
    print("ok drive_terms_and_folder")


if __name__ == "__main__":
    test_files_from()
    test_web_image()
    test_s3_refs()
    test_bytes_from_s3()
    test_bytes_from_b64()
    test_drive_photo_query_keeps_subject()
    test_drive_terms_and_folder()
    print("all adapter tests passed")
