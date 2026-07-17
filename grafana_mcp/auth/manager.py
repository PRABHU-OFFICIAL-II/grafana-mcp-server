import asyncio
import sys
import time
from typing import Optional

from grafana_mcp.auth.session import Session, load_session, save_session, session_expires_in_ms, should_refresh
from grafana_mcp.config import config

_current: Optional[Session] = None
_refresh_task: Optional[asyncio.Task] = None


class SessionExpiredError(Exception):
    def __init__(self):
        super().__init__("Grafana session expired or missing — call login tool first")


async def get_session() -> Session:
    global _current

    if not _current:
        _current = load_session()

    if not _current:
        raise SessionExpiredError()

    now_ms = int(time.time() * 1000)
    if _current.expires_at < now_ms:
        print("[auth] Session expired — attempting silent refresh...", file=sys.stderr)
        from grafana_mcp.auth.okta import try_silent_refresh
        refreshed = await try_silent_refresh(_current)
        if refreshed:
            _current = refreshed
            _schedule_refresh(_current)
            return _current
        raise SessionExpiredError()

    if should_refresh(_current):
        snapshot = _current
        asyncio.ensure_future(_background_refresh(snapshot))
    else:
        _schedule_refresh(_current)

    return _current


async def init_session(username: str, password: str) -> Session:
    global _current
    from grafana_mcp.auth.okta import login_with_okta
    _current = await login_with_okta(username, password)
    _schedule_refresh(_current)
    return _current


def inject_session(grafana_session: str, expires_at_ms: int) -> Session:
    global _current
    _current = Session(grafana_session=grafana_session, expires_at=expires_at_ms)
    save_session(_current)
    _schedule_refresh(_current)
    print("[auth] Session injected manually", file=sys.stderr)
    return _current


def _schedule_refresh(session: Session) -> None:
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()

    ms_until_refresh = session_expires_in_ms(session) - config.auth.refresh_before_expiry_ms
    if ms_until_refresh <= 0:
        return

    minutes = round(ms_until_refresh / 60000)
    print(f"[auth] Silent refresh scheduled in {minutes} minutes", file=sys.stderr)

    try:
        loop = asyncio.get_running_loop()
        _refresh_task = loop.create_task(_delayed_refresh(ms_until_refresh / 1000, session))
    except RuntimeError:
        print("[auth] _schedule_refresh called outside running event loop — skipping", file=sys.stderr)


async def _delayed_refresh(delay_seconds: float, session: Session) -> None:
    global _current
    await asyncio.sleep(delay_seconds)
    print("[auth] Proactive refresh triggered", file=sys.stderr)
    from grafana_mcp.auth.okta import try_silent_refresh
    refreshed = await try_silent_refresh(session)
    if refreshed:
        _current = refreshed
        _schedule_refresh(refreshed)
    else:
        print("[auth] ⚠️  Silent refresh failed — OKTA push may be required on next request", file=sys.stderr)


async def _background_refresh(session: Session) -> None:
    global _current
    from grafana_mcp.auth.okta import try_silent_refresh
    try:
        refreshed = await try_silent_refresh(session)
        if refreshed:
            _current = refreshed
            _schedule_refresh(refreshed)
    except Exception as e:
        print(f"[auth] Background refresh failed: {e}", file=sys.stderr)
