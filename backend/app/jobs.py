"""
Background builds.

Image-to-3D runs for minutes. No dev proxy, headset radio or phone network
holds a single request open that long, so the build runs detached and the
client polls for it. A dropped connection then costs one poll, not the model.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.models import CommandResponse

logger = logging.getLogger(__name__)

RETENTION_S = 900.0


class Job:
    def __init__(self, job_id: str, progress: dict[str, Any] | None = None) -> None:
        self.job_id = job_id
        self.started_at = time.monotonic()
        self.finished_at: float | None = None
        self.result: CommandResponse | None = None
        self.error: str | None = None
        self.progress: dict[str, Any] = progress or {}

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    @property
    def elapsed_s(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at


_jobs: dict[str, Job] = {}


def _sweep() -> None:
    now = time.monotonic()
    for job_id, job in list(_jobs.items()):
        if job.finished_at and now - job.finished_at > RETENTION_S:
            _jobs.pop(job_id, None)


def start(
    work: Callable[[], Awaitable[CommandResponse]],
    progress: dict[str, Any] | None = None,
) -> str:
    _sweep()
    job = Job(uuid.uuid4().hex[:12], progress=progress)
    _jobs[job.job_id] = job

    async def run() -> None:
        try:
            job.result = await work()
        except Exception as exc:
            logger.exception("Job %s crashed", job.job_id)
            job.error = str(exc)
        finally:
            job.finished_at = time.monotonic()
            logger.info("Job %s finished in %.1fs", job.job_id, job.elapsed_s)

    task = asyncio.create_task(run())
    task.add_done_callback(lambda _: None)
    job.task = task  # type: ignore[attr-defined]
    return job.job_id


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def set_app_status(job_id: str, slug: str, status: str) -> None:
    job = _jobs.get(job_id)
    if not job:
        return
    apps = job.progress.setdefault("apps", [])
    for app in apps:
        if app.get("slug") == slug:
            app["status"] = status
            return
