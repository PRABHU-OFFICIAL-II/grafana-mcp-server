# Grafana MCP Server — Deployment Guide

## Architecture

```
Windows Machine (Claude Code)
    ↓  stdio (in-process)
Grafana MCP Server  ←  Python process managed by Claude Code
    ↓  Playwright (headless Chromium)  ← first login only
Okta SSO  →  grafana_session cookie  →  .grafana-session.json
    ↓  HTTPS + grafana_session cookie
Grafana (grafana.cloudtrust.rocks)
```

---

## Prerequisites

- Python 3.11 or later
- `pip` available on PATH
- Claude Code CLI (`claude` command)

---

## Step 1 — Install Dependencies

```bash
cd "C:\Users\ppenthoi\Documents\DEV\grafana-mcp-server"
pip install -r requirements.txt
playwright install chromium
```

`requirements.txt`:
```
mcp>=1.0.0
httpx>=0.27.0
playwright>=1.44.0
uvicorn>=0.30.0
starlette>=0.37.0
```

---

## Step 2 — Register with Claude Code

```bash
claude mcp add --scope user grafana python "C:\Users\ppenthoi\Documents\DEV\grafana-mcp-server\server.py"
```

Verify the connection:
```bash
claude mcp list
# grafana: python C:\Users\...\server.py - ✔ Connected
```

Restart Claude Code after registering so the new server loads.

---

## Step 3 — First Login

Once inside a Claude Code conversation, call the `login` tool:

```
Use the login tool with my Okta credentials
```

What happens under the hood:
1. Playwright launches a headless Chromium browser
2. Navigates to `https://grafana.cloudtrust.rocks`
3. Okta SSO — enters username + password
4. Selects "Get a push notification" on the MFA screen
5. **Approve the Okta Verify push on your phone**
6. Waits for Grafana landing page (up to 120 s)
7. Extracts `grafana_session` cookie + Okta session cookies
8. Saves everything to `.grafana-session.json`

> **Visible browser (debug):** Set env var `OKTA_HEADLESS=false` if you need to see the browser window:
> ```powershell
> $env:OKTA_HEADLESS="false"
> python server.py   # only if running manually; otherwise set it in MCP env config
> ```

---

## Step 4 — Session Lifecycle (automatic, no action needed)

| Event | What happens |
|-------|-------------|
| Login completes | Session written to `.grafana-session.json`, refresh timer started |
| 3 min before expiry | Silent refresh via saved Okta cookies (no push needed) |
| Refresh succeeds | New session saved, new timer scheduled |
| Server cold-start | Loads existing session from file, schedules refresh if still valid |
| Okta cookies expire (~8–24 h) | Call `login` tool again |

The only recurring manual action is re-running the `login` tool the next morning when Okta cookies expire.

---

## Session File Formats

`.grafana-session.json` (auto-managed, do not edit by hand):
```json
{
  "grafanaSession": "<cookie value>",
  "expiresAt": 1784212256000,
  "oktaCookies": [
    { "name": "...", "value": "...", "domain": "informatica.okta.com" }
  ]
}
```

---

## Alternative: Manual Cookie Injection

If you cannot run Playwright (CI, headless-blocked environments), grab the cookie from your browser:

1. Open `https://grafana.cloudtrust.rocks` in Chrome
2. DevTools → Application → Cookies → copy `grafana_session` value
3. Call the `inject_session` tool:
   ```
   inject_session grafana_session=<value> expires_at_unix_seconds=<timestamp>
   ```

---

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `login` | Full Okta SSO login via headless browser — sends push to phone |
| `inject_session` | Manual cookie injection from browser DevTools |
| `auth_status` | Check session validity and minutes remaining |
| `list_folders` | List all Grafana dashboard folders |
| `list_dashboards` | List dashboards inside a folder |
| `get_dashboard_info` | Full dashboard definition — panels, variables, datasource refs |
| `get_label_values` | Available values for a Prometheus label (filter discovery) |
| `query_metrics` | Run a PromQL expression against a datasource |
| `detect_anomalies` | Query + threshold-based spike/breach detection |
| `check_dashboard_health` | Health-check all panels in a dashboard |
| `get_alert_rules` | Active alert rule states for a dashboard |

---

## Running Manually (optional / debugging)

```bash
cd "C:\Users\ppenthoi\Documents\DEV\grafana-mcp-server"

# stdio mode (same as Claude Code uses)
python server.py

# HTTP/SSE mode (for remote access or testing)
MCP_MODE=http MCP_PORT=3001 python server.py
# Health endpoint: http://localhost:3001/health
# SSE endpoint:    http://localhost:3001/sse
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_MODE` | `stdio` | `stdio` for Claude Code, `http` for SSE server |
| `MCP_PORT` | `3001` | HTTP server port (http mode only) |
| `MCP_HOST` | `0.0.0.0` | HTTP server bind address |
| `OKTA_HEADLESS` | `true` | Set `false` to show browser window during login |
| `GRAFANA_URL` | `https://grafana.cloudtrust.rocks` | Target Grafana base URL |
| `GRAFANA_TLS_VERIFY` | `false` | Set `true` to enable TLS certificate verification |
| `OKTA_ORG` | `https://informatica.okta.com` | Okta organisation URL |
| `OKTA_CLIENT_ID` | *(hardcoded)* | Okta OAuth client ID |
| `SESSION_FILE` | `.grafana-session.json` | Path to the session cache file |
| `THRESHOLD_CPU` | `85` | CPU % above which an anomaly is reported |
| `THRESHOLD_MEMORY` | `90` | Memory % threshold |
| `THRESHOLD_THREADS` | `100` | Thread count threshold |
| `THRESHOLD_ERROR_RATE` | `5` | Error rate % threshold (reserved) |
| `THRESHOLD_RESPONSE_MS` | `3000` | Response time ms threshold |

---

## Project Structure

```
grafana-mcp-server/
├── server.py                  # Entry point — stdio or HTTP/SSE
├── requirements.txt
├── .grafana-session.json      # Auto-managed session file (gitignored)
└── grafana_mcp/
    ├── config.py              # Grafana URL, Okta client ID, thresholds
    ├── auth/
    │   ├── session.py         # Session dataclass, load/save
    │   ├── okta.py            # Playwright Okta login + silent refresh
    │   └── manager.py         # In-memory session cache + background refresh
    ├── grafana/
    │   ├── client.py          # httpx HTTP client with session cookie
    │   └── api.py             # Grafana REST + datasource proxy calls
    ├── parser/
    │   └── metrics.py         # Grafana frames parser + anomaly detection
    └── tools/
        └── index.py           # MCP tool registration (11 tools)
```

---

## Updating After Code Changes

No build step needed — Python runs source directly:

```bash
# After editing any .py file, just restart Claude Code
# The MCP server process is restarted automatically on next connection
```

---

## Troubleshooting

**`No active session` on startup**
→ Normal on first run. Call the `login` tool.

**`ERR_TOKEN_REVOKED: maxConcurrentSessions=3`**
→ Grafana limits 3 concurrent sessions. Close other browser tabs logged into Grafana, then call `login` again.

**Login push never arrives**
→ Set `OKTA_HEADLESS=false` and run `python server.py` manually to watch the browser. Check Okta Verify app on your phone.

**`playwright install chromium` needed**
→ Run `playwright install chromium` once after `pip install -r requirements.txt`.

**MCP server shows `Failed to connect`**
→ Run `python server.py` manually and check for import errors. Most likely `pip install -r requirements.txt` wasn't run.
