import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional, List

from grafana_mcp.config import config


@dataclass
class Session:
    grafana_session: str
    expires_at: int  # Unix ms
    refresh_token: Optional[str] = None
    okta_cookies: List[dict] = field(default_factory=list)


def load_session() -> Optional[Session]:
    try:
        if not os.path.exists(config.auth.session_file):
            return None
        with open(config.auth.session_file, "r") as f:
            data = json.load(f)
        return Session(
            grafana_session=data["grafanaSession"],
            expires_at=data["expiresAt"],
            refresh_token=data.get("refreshToken"),
            okta_cookies=data.get("oktaCookies", []),
        )
    except Exception as e:
        print(f"[auth] Failed to load session from {config.auth.session_file}: {e}", file=sys.stderr)
        return None


def save_session(session: Session) -> None:
    data = {
        "grafanaSession": session.grafana_session,
        "expiresAt": session.expires_at,
        "oktaCookies": session.okta_cookies,
    }
    if session.refresh_token:
        data["refreshToken"] = session.refresh_token
    with open(config.auth.session_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[auth] Session saved, expires at {_ms_to_iso(session.expires_at)}", file=sys.stderr)


def session_expires_in_ms(session: Session) -> int:
    return session.expires_at - int(time.time() * 1000)


def should_refresh(session: Session) -> bool:
    return session_expires_in_ms(session) < config.auth.refresh_before_expiry_ms


def _ms_to_iso(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat()
