import asyncio
import sys
import time
from typing import Optional

from grafana_mcp.config import config
from grafana_mcp.auth.session import Session, save_session

PUSH_POLL_INTERVAL = 3.0
PUSH_POLL_TIMEOUT = 120.0


async def login_with_okta() -> Session:
    """Open a visible browser window for the user to complete Okta login manually.

    No credentials are passed — the user types them in the real browser and
    approves the Okta Verify push themselves. Once they land on Grafana, we
    capture the session and Okta cookies so silent refresh can take over.
    """
    from playwright.async_api import async_playwright

    print("[auth] Opening browser for Okta login — complete sign-in in the window that appears...", file=sys.stderr)

    async with async_playwright() as p:
        # Always visible — this is the whole point. User drives the login.
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context()
            page = await context.new_page()

            okta_domain = config.okta.org.rstrip("/").split("//")[-1]

            await page.goto(f"{config.grafana.base_url}/login/generic_oauth", wait_until="domcontentloaded")
            print(f"[auth] Browser opened — waiting for you to complete Okta login at {page.url}", file=sys.stderr)
            print("[auth] Sign in and approve the Okta Verify push, then this will continue automatically.", file=sys.stderr)

            # Wait up to 5 minutes for the user to complete the full login flow
            await _wait_for_grafana_landing(page, okta_domain, timeout=300.0)
            print("[auth] Login detected — capturing session cookies...", file=sys.stderr)

            grafana_cookies = await context.cookies(config.grafana.base_url)
            okta_cookies = await context.cookies(config.okta.org)

            session_cookie = next((c for c in grafana_cookies if c["name"] == "grafana_session"), None)
            expiry_cookie = next((c for c in grafana_cookies if c["name"] == "grafana_session_expiry"), None)

            if not session_cookie:
                raise RuntimeError("grafana_session cookie not found after login")

            expires_at = (
                int(expiry_cookie["value"]) * 1000
                if expiry_cookie
                else int(time.time() * 1000) + 30 * 24 * 60 * 60 * 1000
            )

            session = Session(
                grafana_session=session_cookie["value"],
                expires_at=expires_at,
                okta_cookies=[
                    {
                        "name": c["name"], "value": c["value"], "domain": c["domain"],
                        "path": c["path"], "expires": c.get("expires"), "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", True), "sameSite": c.get("sameSite", "Lax"),
                    }
                    for c in okta_cookies
                ],
            )
            save_session(session)
            print("[auth] Session saved — browser will now close.", file=sys.stderr)
            return session
        finally:
            await browser.close()


async def try_silent_refresh(session: Session) -> Optional[Session]:
    if not session.okta_cookies:
        return None
    try:
        return await _refresh_with_prompt_none(session.okta_cookies)
    except Exception as e:
        print(f"[auth] Silent refresh failed: {e}", file=sys.stderr)
        return None


async def _select_push_notification(page) -> None:
    try:
        await page.wait_for_selector('[data-se="okta_verify-push"]', timeout=8_000)
        print("[auth] MFA method selector detected — selecting push notification...", file=sys.stderr)
        await page.click('[data-se="okta_verify-push"] a.select-factor')
        print("[auth] Push notification selected", file=sys.stderr)
    except Exception as e:
        print(f"[auth] No method selector (push may have been sent directly): {e}", file=sys.stderr)


async def _wait_for_grafana_landing(page, okta_domain: str, timeout: float = PUSH_POLL_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = page.url
        except Exception:
            await asyncio.sleep(0.5)
            continue

        if url.startswith(config.grafana.base_url) and "/login" not in url:
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            return

        if okta_domain in url:
            try:
                error_el = await page.query_selector(".o-form-error-container:not(:empty)")
                if error_el:
                    text = await error_el.text_content()
                    raise RuntimeError(f"OKTA error: {text}")
            except RuntimeError:
                raise
            except Exception:
                pass

        await asyncio.sleep(PUSH_POLL_INTERVAL)

    raise RuntimeError(f"Login timed out after {round(timeout / 60)} minutes — please try again")


async def _refresh_with_prompt_none(okta_cookies: list) -> Optional[Session]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            await context.add_cookies([
                {
                    "name": c["name"], "value": c["value"], "domain": c["domain"],
                    "path": c["path"], "expires": c.get("expires", -1),
                    "httpOnly": c.get("httpOnly", False), "secure": c.get("secure", True),
                    "sameSite": c.get("sameSite", "Lax"),
                }
                for c in okta_cookies
            ])
            page = await context.new_page()
            await page.goto(f"{config.grafana.base_url}/login/generic_oauth", wait_until="domcontentloaded", timeout=30_000)

            final_url = page.url
            if not final_url.startswith(config.grafana.base_url) or "/login" in final_url:
                raise RuntimeError("Silent refresh did not land on Grafana — OKTA session expired")

            grafana_cookies = await context.cookies(config.grafana.base_url)
            new_okta_cookies = await context.cookies(config.okta.org)

            session_cookie = next((c for c in grafana_cookies if c["name"] == "grafana_session"), None)
            expiry_cookie = next((c for c in grafana_cookies if c["name"] == "grafana_session_expiry"), None)

            if not session_cookie:
                raise RuntimeError("No grafana_session after silent refresh")

            expires_at = (
                int(expiry_cookie["value"]) * 1000
                if expiry_cookie
                else int(time.time() * 1000) + 10 * 60 * 1000
            )

            session = Session(
                grafana_session=session_cookie["value"],
                expires_at=expires_at,
                okta_cookies=[
                    {
                        "name": c["name"], "value": c["value"], "domain": c["domain"],
                        "path": c["path"], "expires": c.get("expires"), "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", True), "sameSite": c.get("sameSite", "Lax"),
                    }
                    for c in new_okta_cookies
                ],
            )
            save_session(session)
            print("[auth] Silent refresh succeeded", file=sys.stderr)
            return session
        finally:
            await browser.close()
