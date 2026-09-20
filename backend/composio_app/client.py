"""Composio SDK wrapper. Auth is the project API key; user_id is resolved from accounts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

_user_id: str | None = None
_auth_ok: bool | None = None


class ComposioAuthError(RuntimeError):
    pass


class ComposioPermissionError(RuntimeError):
    pass


def _sdk(settings: Settings):
    from composio import Composio

    key = (settings.composio_api_key or "").strip()
    if not key:
        raise ComposioAuthError("COMPOSIO_API_KEY is not set.")
    return Composio(api_key=key)


def composio_ready(settings: Settings) -> bool:
    return bool((settings.composio_api_key or "").strip())


def _account_rows(client) -> list:
    rows = []
    cursor = None
    for _ in range(6):
        listed = client.connected_accounts.list(**({"cursor": cursor} if cursor else {}))
        items = getattr(listed, "items", None) or []
        rows.extend(items)
        cursor = getattr(listed, "next_cursor", None)
        if not cursor or not items:
            break
    return rows


def _acc_field(acc, *names):
    for name in names:
        val = acc.get(name) if isinstance(acc, dict) else getattr(acc, name, None)
        if val:
            return val
    return None


def resolve_user_id(settings: Settings) -> str:
    """Use the user that owns an ACTIVE connection, not an unfinished initiate."""
    global _user_id, _auth_ok
    if _user_id:
        return _user_id
    configured = (settings.composio_user_id or "").strip()
    try:
        client = _sdk(settings)
        rows = _account_rows(client)
        _auth_ok = True
        logger.info("Composio listed %s connected accounts", len(rows))
        active_uid = None
        any_uid = None
        for acc in rows:
            uid = _acc_field(acc, "user_id", "userId")
            status = str(_acc_field(acc, "status") or "").upper()
            if uid and status == "ACTIVE" and not str(uid).startswith("perception-"):
                active_uid = str(uid)
                break
            if uid and status == "ACTIVE" and not active_uid:
                active_uid = str(uid)
            if uid and not any_uid:
                any_uid = str(uid)
        _user_id = active_uid or configured or any_uid or "default"
        logger.info("Composio user_id=%s", _user_id)
        return _user_id
    except Exception as exc:
        _auth_ok = False
        logger.warning("Composio account list failed: %s", exc)
        if configured:
            _user_id = configured
            return _user_id
        raise ComposioAuthError(str(exc)) from exc


def execute_sync(settings: Settings, slug: str, arguments: dict[str, Any] | None = None) -> Any:
    user_id = resolve_user_id(settings)
    client = _sdk(settings)
    logger.info("Composio execute %s user=%s", slug, user_id)
    return client.tools.execute(
        slug,
        user_id=user_id,
        arguments=arguments or {},
        dangerously_skip_version_check=True,
    )


async def execute(settings: Settings, slug: str, arguments: dict[str, Any] | None = None) -> Any:
    try:
        return await asyncio.to_thread(execute_sync, settings, slug, arguments)
    except ComposioAuthError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "Invalid API key" in msg:
            raise ComposioAuthError(msg) from exc
        if (
            "403" in msg
            or "InsufficientPermissions" in msg
            or "tool_execution" in msg
            or "no connected account" in msg.lower()
            or "not connected" in msg.lower()
        ):
            raise ComposioPermissionError(msg) from exc
        raise
