#!/usr/bin/env python3
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models import Intent, SessionState
from composio_app.runner import run_publish, start_publish_job


def _session(**kwargs) -> SessionState:
    base = dict(
        session_id="t",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10,10,4)',
        glb_url="/media/glb/abc.glb",
        model_id="abc",
        last_summary="pikachu keychain",
        last_items=[{"sender_name": "Omer", "subtitle": "Omer <omer.sjd05@gmail.com>"}],
    )
    base.update(kwargs)
    return SessionState(**base)


def _settings(tmp: Path) -> SimpleNamespace:
    return SimpleNamespace(glb_dir=tmp, projects_dir=tmp)


def test_start_publish_job_hud() -> None:
    intent = Intent(
        action="publish_work",
        reply="Exporting to STL…",
        apps=["googledrive"],
        pull_kind="publish",
        params={"format": "stl", "export": True, "drive": True, "gmail": False},
    )
    session = _session()
    with patch("app.jobs.start", return_value="job1"):
        resp = start_publish_job(intent, session, _settings(Path("/tmp")), "Export this to STL.")
    assert resp.action == "publishing"
    assert resp.job_id == "job1"
    assert resp.apps[0]["slug"] == "googledrive"
    print("ok start_publish_job_hud")


def test_run_publish_export_and_email() -> None:
    async def go() -> None:
        tmp = Path("/tmp/perception_publish_test")
        tmp.mkdir(parents=True, exist_ok=True)
        dest = tmp / "publish" / "pikachu_keychain.stl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"solid test\nendsolid test\n" + b"x" * 80)
        intent = Intent(
            action="publish_work",
            apps=["gmail"],
            pull_kind="publish",
            params={
                "format": "stl",
                "export": True,
                "drive": False,
                "gmail": True,
                "wants_email": True,
                "recipient": "Omer",
                "attach": True,
                "filename": "pikachu_keychain",
                "email_body": "we finished the model please take a look",
                "signoff": "Best regards from Umad",
            },
        )
        session = _session()
        settings = _settings(tmp)
        with (
            patch("composio_app.runner.export_session_model", return_value={"ok": True, "path": str(dest), "ms": 12}),
            patch("composio_app.runner.upload_drive_file", new_callable=AsyncMock) as drive,
            patch("composio_app.runner.send_gmail", new_callable=AsyncMock) as gmail,
            patch("composio_app.runner.synthesize_speech", new_callable=AsyncMock, return_value=(None, 0)),
            patch("composio_app.runner.jobs.set_app_status"),
            patch("composio_app.runner.jobs.get", return_value=SimpleNamespace(progress={"apps": []})),
            patch("composio_app.runner.save_session"),
        ):
            gmail.return_value = {"ok": True}
            result = await run_publish(intent, session, settings, "job9")
        assert result.action == "published"
        assert result.ok
        assert "emailed Omer" in result.reply
        drive.assert_not_called()
        gmail.assert_called_once()
        args = gmail.call_args.args
        kwargs = gmail.call_args.kwargs
        to = kwargs.get("recipient") or (args[1] if len(args) > 1 else None)
        body = kwargs.get("body") or (args[3] if len(args) > 3 else None)
        assert to == "omer.sjd05@gmail.com"
        assert "we finished the model please take a look" in body
        assert "export" not in body.lower()
        assert "Umad" in body
        print("ok run_publish_export_and_email")

    asyncio.run(go())


def test_run_publish_print_and_email_both_files() -> None:
    async def go() -> None:
        tmp = Path("/tmp/perception_publish_test")
        pub = tmp / "publish"
        pub.mkdir(parents=True, exist_ok=True)
        stl = pub / "cube.stl"
        step = pub / "cube.step"
        stl.write_bytes(b"solid test\nendsolid test\n" + b"x" * 80)
        step.write_bytes(b"ISO-10303-21;" + b"x" * 80)
        intent = Intent(
            action="publish_work",
            apps=["googledrive", "gmail"],
            pull_kind="publish",
            params={
                "format": "stl",
                "formats": ["stl", "step"],
                "format_reason": "both",
                "export": True,
                "drive": True,
                "gmail": True,
                "wants_email": True,
                "recipient": "Omer",
                "attach": True,
                "filename": "cube",
                "email_body": "we finished the model",
            },
        )
        with (
            patch(
                "composio_app.runner.export_session_model",
                side_effect=lambda _s, _set, dest, fmt: {
                    "ok": True,
                    "path": str(stl if fmt == "stl" else step),
                    "ms": 4,
                },
            ) as exp,
            patch("composio_app.runner.upload_drive_file", new_callable=AsyncMock, return_value={"ok": True}) as drive,
            patch("composio_app.runner.send_gmail", new_callable=AsyncMock, return_value={"ok": True}) as gmail,
            patch("composio_app.runner.synthesize_speech", new_callable=AsyncMock, return_value=(None, 0)),
            patch("composio_app.runner.jobs.set_app_status"),
            patch("composio_app.runner.jobs.get", return_value=SimpleNamespace(progress={"apps": []})),
            patch("composio_app.runner.save_session"),
        ):
            result = await run_publish(intent, _session(), _settings(tmp), "job9")
        assert result.ok and result.action == "published"
        assert exp.call_count == 2
        assert drive.call_count == 2
        assert [c.args[1] for c in drive.call_args_list] == [stl, step]
        attach = gmail.call_args.kwargs.get("attachment") or gmail.call_args.args[4]
        assert attach == [step, stl]
        print("ok run_publish_print_and_email_both_files")

    asyncio.run(go())


def test_run_publish_no_model() -> None:
    async def go() -> None:
        intent = Intent(
            action="publish_work",
            apps=["googledrive"],
            params={"format": "stl", "export": True, "drive": True},
        )
        session = _session(last_script=None, glb_url=None)
        with (
            patch("composio_app.runner.synthesize_speech", new_callable=AsyncMock, return_value=(None, 0)),
            patch("composio_app.runner.jobs.get", return_value=SimpleNamespace(progress={"apps": []})),
        ):
            result = await run_publish(intent, session, _settings(Path("/tmp")), "job9")
        assert result.action == "clarify"
        assert "Build something first" in result.reply
        print("ok run_publish_no_model")

    asyncio.run(go())


def test_run_publish_drive_only() -> None:
    async def go() -> None:
        tmp = Path("/tmp/perception_publish_test")
        dest = tmp / "publish" / "model.stl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"solid test\nendsolid test\n" + b"x" * 80)
        intent = Intent(
            action="publish_work",
            apps=["googledrive"],
            params={
                "format": "stl",
                "export": True,
                "drive": True,
                "gmail": False,
                "folder": "htn",
                "filename": "pikachu_keychain",
            },
        )
        with (
            patch("composio_app.runner.export_session_model", return_value={"ok": True, "path": str(dest), "ms": 8}),
            patch("composio_app.runner.upload_drive_file", new_callable=AsyncMock, return_value={"ok": True}) as drive,
            patch("composio_app.runner.send_gmail", new_callable=AsyncMock) as gmail,
            patch("composio_app.runner.synthesize_speech", new_callable=AsyncMock, return_value=(None, 0)),
            patch("composio_app.runner.jobs.set_app_status"),
            patch("composio_app.runner.jobs.get", return_value=SimpleNamespace(progress={"apps": []})),
            patch("composio_app.runner.save_session"),
        ):
            result = await run_publish(intent, _session(), _settings(tmp), "job9")
        assert result.ok and result.action == "published"
        drive.assert_called_once()
        gmail.assert_not_called()
        assert drive.call_args.args[2] == "htn"
        print("ok run_publish_drive_only")

    asyncio.run(go())


if __name__ == "__main__":
    test_start_publish_job_hud()
    test_run_publish_export_and_email()
    test_run_publish_no_model()
    test_run_publish_print_and_email_both_files()
    test_run_publish_drive_only()
    print("all publish job tests passed")
