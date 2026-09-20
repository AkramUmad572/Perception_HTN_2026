#!/usr/bin/env python3
"""Live verification: CAD export, Drive upload, and unnamed-email clarify."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
SESSION = "verify-publish"


def _req(method: str, path: str, payload: dict | None = None, timeout: float = 180) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise SystemExit(f"HTTP {exc.code} {path}: {body}") from exc


def _poll(job_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _req("GET", f"/api/jobs/{job_id}?session_id={SESSION}", timeout=30)
        action = last.get("action")
        if action not in ("publishing", "searching", "building"):
            return last
        time.sleep(1.2)
    raise SystemExit(f"job {job_id} still {last.get('action')}: {last.get('reply')}")


def main() -> None:
    health = _req("GET", "/api/health")
    print("health", health.get("ok") or health)

    built = _req(
        "POST",
        "/api/script",
        {
            "script": 'import cadquery as cq\nresult = cq.Workplane("XY").box(24, 12, 4)\n',
            "session_id": SESSION,
            "color": "#FFD700",
        },
        timeout=90,
    )
    assert built.get("ok"), built
    assert built.get("glb_url"), built
    print("built", built.get("glb_url"), built.get("action"))

    unnamed = _req("POST", "/api/command", {"text": "email them saying we finished", "session_id": SESSION})
    assert unnamed.get("action") == "clarify", unnamed
    assert "Who" in (unnamed.get("reply") or ""), unnamed
    print("clarify", unnamed.get("reply"))

    export = _req(
        "POST",
        "/api/command",
        {
            "text": "Export this to STL and store it in the HTN folder as percy_verify_keychain.",
            "session_id": SESSION,
        },
    )
    assert export.get("action") == "publishing", export
    assert export.get("job_id"), export
    print("publishing", export.get("reply"), export.get("apps"))
    done = _poll(export["job_id"])
    print("published", done.get("action"), done.get("reply"), done.get("apps"))
    if done.get("action") != "published" or not done.get("ok"):
        raise SystemExit(f"export/drive failed: {json.dumps(done, indent=2)[:1200]}")
    reply = (done.get("reply") or "").lower()
    if "htn" not in reply and "drive" not in reply:
        raise SystemExit(f"unexpected publish reply: {done.get('reply')}")
    print("all live verify checks passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL", exc)
        sys.exit(1)
