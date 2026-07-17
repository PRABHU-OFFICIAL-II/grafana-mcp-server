import asyncio
import os
import sys
import time
from typing import Optional

from grafana_mcp.config import config
from grafana_mcp.auth.session import Session, save_session

PUSH_POLL_INTERVAL = 3.0
PUSH_POLL_TIMEOUT = 120.0


async def login_with_okta(username: str, password: str) -> Session:
    from playwright.async_api import async_playwright

    print("[auth] Starting OKTA login flow (headless browser)...", file=sys.stderr)
    headless = os.environ.get("OKTA_HEADLESS", "true").lower() not in ("false", "0", "no")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            context = await browser.new_context()
            page = await context.new_page()

            print("[auth] Navigating to Grafana login...", file=sys.stderr)
            await page.goto(f"{config.grafana.base_url}/login/generic_oauth", wait_until="domcontentloaded")
            okta_domain = config.okta.org.rstrip("/").split("//")[-1]
            await page.wait_for_url(f"**/{okta_domain}/**", timeout=30_000)
            print(f"[auth] On OKTA: {page.url}", file=sys.stderr)

            print("[auth] Filling username...", file=sys.stderr)
            username_field = page.locator('input[name="identifier"], #okta-signin-username, input[type="text"]').first
            await username_field.wait_for(state="visible", timeout=15_000)
            await username_field.fill(username)
            next_btn = page.locator('[type="submit"], [data-type="save"], #okta-signin-submit').first
            await next_btn.click()
            print("[auth] Username submitted", file=sys.stderr)

            print("[auth] Waiting for password field...", file=sys.stderr)
            password_field = page.locator('input[type="password"], input[name="credentials.passcode"]').first
            await password_field.wait_for(state="visible", timeout=15_000)
            await password_field.fill(password)
            submit_btn = page.locator('[type="submit"], [data-type="save"]').first
            await submit_btn.click()
            print("[auth] Password submitted — waiting for MFA push...", file=sys.stderr)

            await _select_push_notification(page)

            print("[auth] OKTA Verify push sent — please approve on your phone...", file=sys.stderr)
            await _wait_for_grafana_landing(page, okta_domain)
            print("[auth] Redirected back to Grafana — login successful", file=sys.stderr)

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


async def _wait_for_grafana_landing(page, okta_domain: str) -> None:
    deadline = time.time() + PUSH_POLL_TIMEOUT
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

    raise RuntimeError("OKTA Verify push timed out after 2 minutes")


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
