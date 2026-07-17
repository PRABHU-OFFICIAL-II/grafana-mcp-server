# Grafana MCP Server

A Python [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives Claude direct, conversational access to Grafana. Ask questions in plain English — Claude navigates folders, resolves datasources, runs PromQL queries, and surfaces anomalies without you writing a single query.

```
"Are there any CPU spikes on the NA2 TASKFLOW pods in the last hour?"
"Run a full health check on the PROD summary dashboard."
"Which tenants are dispatching the most BPEL processes right now?"
```

---

## How it works

```
You (natural language)
    ↓
Claude Code
    ↓  MCP stdio
Grafana MCP Server  (Python process, managed by Claude Code)
    ↓  Playwright headless Chromium  ← first login / MFA only
Okta SSO  →  grafana_session cookie  →  .grafana-session.json
    ↓  HTTPS + session cookie
Grafana  →  Prometheus / Thanos datasource proxy
```

After the first login, the server silently refreshes your session using saved Okta cookies — no push notification needed again until the next day.

---

## Quick start

**1. Install dependencies**

```bash
cd grafana-mcp-server
pip install -r requirements.txt
playwright install chromium
```

**2. Register with Claude Code**

```bash
claude mcp add --scope user grafana python "C:\path\to\grafana-mcp-server\server.py"
claude mcp list
# grafana: python ...\server.py  ✔ Connected
```

**3. Login (once per day)**

Start a Claude Code conversation and say:

```
Login to Grafana with my Okta credentials
```

Claude calls the `login` tool, a headless browser opens, Okta SSO runs, and an **Okta Verify push** is sent to your phone. Approve it — done. The session is saved and auto-renewed for the rest of the day.

---

## Tools

| Tool | What it does |
|------|-------------|
| `login` | Full Okta SSO login via headless Chromium — sends MFA push to phone |
| `inject_session` | Manually inject a `grafana_session` cookie from browser DevTools |
| `auth_status` | Show session validity and minutes until expiry |
| `list_folders` | List all Grafana dashboard folders with UIDs |
| `list_dashboards` | List all dashboards inside a folder |
| `get_dashboard_info` | Full dashboard definition — panels, template variables, datasource refs |
| `get_label_values` | Enumerate Prometheus label values (namespace, pod, cluster, etc.) |
| `query_metrics` | Run any PromQL expression against a datasource |
| `detect_anomalies` | Run a PromQL query and flag threshold breaches / spikes |
| `check_dashboard_health` | Query every panel in a dashboard and report all anomalies in one call |
| `get_alert_rules` | Get active Grafana alert rule states for a dashboard |

---

## Example conversations

**Investigate a service**
```
List the dashboards in the CAI-TASKFLOW folder, then run a health check
on the PROD summary dashboard for the last 2 hours.
```

**Root-cause a CPU spike**
```
Query java_operatingsystem_processcpuload for the taskflow-prod-use2 pods
over the last 3 hours and detect any anomalies.
```

**Tenant analysis**
```
Query org_activebpel_rt_bpel_metrics_deployment_tenant_activebpelcount
grouped by tenant for the last hour. Which tenants are most active?
```

**Check auth**
```
What is my current Grafana session status?
```

---

## Session lifecycle

| Event | What happens |
|-------|-------------|
| First login | Playwright browser → Okta SSO → MFA push → session saved |
| Server start | Loads `.grafana-session.json`, schedules proactive refresh |
| 3 min before expiry | Silent refresh via saved Okta cookies — no push needed |
| Okta cookies expire (~8–24 h) | Call `login` tool again |

The only recurring manual action is approving one push notification each morning.

---

## Project structure

```
grafana-mcp-server/
├── server.py                    # Entry point — stdio or HTTP/SSE mode
├── requirements.txt
├── .grafana-session.json        # Auto-managed session cache (gitignored)
└── grafana_mcp/
    ├── config.py                # All config — reads from env vars with defaults
    ├── auth/
    │   ├── session.py           # Session dataclass, load/save to JSON
    │   ├── okta.py              # Playwright Okta login + silent refresh flow
    │   └── manager.py           # In-memory session cache + background refresh scheduler
    ├── grafana/
    │   ├── client.py            # httpx async HTTP client with session cookie injection
    │   └── api.py               # Grafana REST API + datasource proxy calls
    ├── parser/
    │   └── metrics.py           # Grafana frames parser + anomaly detection engine
    └── tools/
        └── index.py             # MCP tool registration (11 tools)
```

---

## Configuration

All settings have defaults and can be overridden via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_URL` | `https://grafana.cloudtrust.rocks` | Target Grafana base URL |
| `GRAFANA_TLS_VERIFY` | `false` | Set `true` to enable TLS certificate verification |
| `OKTA_ORG` | `https://informatica.okta.com` | Okta organisation URL |
| `OKTA_CLIENT_ID` | *(built-in)* | Okta OAuth client ID |
| `OKTA_HEADLESS` | `true` | Set `false` to show the browser window during login |
| `SESSION_FILE` | `.grafana-session.json` | Path to the session cache file |
| `MCP_MODE` | `stdio` | `stdio` for Claude Code, `http` for SSE server |
| `MCP_PORT` | `3001` | HTTP server port (http mode only) |
| `MCP_HOST` | `0.0.0.0` | HTTP server bind address |
| `THRESHOLD_CPU` | `85` | CPU % above which an anomaly is flagged |
| `THRESHOLD_MEMORY` | `90` | Memory % threshold |
| `THRESHOLD_THREADS` | `100` | Thread count threshold |
| `THRESHOLD_RESPONSE_MS` | `3000` | Response time ms threshold |

---

## Running modes

**stdio** (default — used by Claude Code)
```bash
python server.py
```

**HTTP / SSE** (for remote access or multi-client use)
```bash
MCP_MODE=http MCP_PORT=3001 python server.py

# Endpoints:
# GET  /health  — liveness check
# GET  /sse     — SSE stream
# POST /messages/  — MCP message handler
```

---

## Anomaly detection

`detect_anomalies` and `check_dashboard_health` apply these rules to every metric series:

| Type | Condition | Severity |
|------|-----------|----------|
| CPU | value ≥ `THRESHOLD_CPU` % | warning / critical at 95% |
| Memory | value ≥ `THRESHOLD_MEMORY` % | warning / critical at 95% |
| Threads | count ≥ `THRESHOLD_THREADS` | warning / critical at 1.5× |
| Response time | ms ≥ `THRESHOLD_RESPONSE_MS` | warning / critical at 2× |
| Spike | current > 2× avg AND near series max | warning |

Values in the 0–1 range (e.g. Prometheus CPU ratios) are automatically normalised to 0–100%.

---

## Manual cookie injection

If Playwright cannot run (headless-blocked environment, CI):

1. Open Grafana in Chrome
2. DevTools → Application → Cookies → copy `grafana_session`
3. Tell Claude: *"Inject this Grafana session: `<value>`, expires `<unix timestamp>`"*

---

## Troubleshooting

**`No active session` on startup**
Normal on first run. Tell Claude to log in to Grafana.

**`ERR_TOKEN_REVOKED: maxConcurrentSessions=3`**
Grafana allows 3 concurrent sessions. Close other Grafana browser tabs, then log in again.

**MFA push never arrives**
Set `OKTA_HEADLESS=false`, run `python server.py` manually, and watch the browser.

**`Failed to connect` in `claude mcp list`**
Run `python server.py` directly and check the error. Most likely cause: `pip install -r requirements.txt` or `playwright install chromium` was not run.

**After code changes**
No build step needed. Restart Claude Code — the server process is relaunched automatically.

---

## Requirements

- Python 3.11+
- `mcp >= 1.0.0`
- `httpx >= 0.27.0`
- `playwright >= 1.44.0`
- `uvicorn >= 0.30.0` *(http mode only)*
- `starlette >= 0.37.0` *(http mode only)*
