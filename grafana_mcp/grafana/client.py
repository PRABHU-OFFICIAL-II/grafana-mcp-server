import sys
from typing import Any, Optional

import httpx

from grafana_mcp.auth.manager import get_session, SessionExpiredError
from grafana_mcp.config import config


async def _headers() -> dict:
    session = await get_session()
    return {
        "Cookie": f"grafana_session={session.grafana_session}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-grafana-org-id": str(config.grafana.org_id),
    }


async def grafana_get(path: str, params: Optional[dict] = None) -> Any:
    headers = await _headers()
    url = f"{config.grafana.base_url}{path}"
    async with httpx.AsyncClient(verify=config.grafana.tls_verify, timeout=30) as client:
        resp = await client.get(url, headers=headers, params=params)
    _check_response(resp, path)
    return resp.json()


async def grafana_post(path: str, body: Any) -> Any:
    headers = await _headers()
    url = f"{config.grafana.base_url}{path}"
    async with httpx.AsyncClient(verify=config.grafana.tls_verify, timeout=60) as client:
        resp = await client.post(url, headers=headers, json=body)
    _check_response(resp, path)
    return resp.json()


def _check_response(resp: httpx.Response, path: str) -> None:
    if resp.status_code in (401, 302):
        raise SessionExpiredError()
    if not resp.is_success:
        raise RuntimeError(f"Grafana API error {resp.status_code} on {path}: {resp.text[:300]}")
