# Grafana MCP Server

A Python [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives Claude direct, conversational access to Grafana. Ask questions in plain English — Claude navigates folders, resolves datasources, runs PromQL queries, and surfaces anomalies without you writing a single query.

```
"Are there any CPU spikes on the NA2 TASKFLOW pods in the last hour?"
"Run a full health check on the PROD summary dashboard."
"Which tenants are dispatching the most BPEL processes right now?"
"Check CAI DS queue on July 15 between 4pm and 6pm IST"
```

---

## How it works

```
You (natural language)
    ↓
Claude Code
    ↓  MCP stdio
Grafana MCP Server  (Python process, managed by Claude Code)
    ↓  Playwright visible Chromium  ← first login / MFA only
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
Login to Grafana
```

Claude calls the `login` tool, a **visible browser window opens** automatically. Sign in with your Okta credentials and approve the Verify push on your phone — done. No passwords are stored anywhere. The session is saved and auto-renewed for the rest of the day.

---

## Tools

### Auth

| Tool | What it does |
|------|-------------|
| `login` | Opens a visible browser — sign in with Okta, approve the push. No credentials needed. |
| `inject_session` | Manually inject a `grafana_session` cookie from browser DevTools |
| `auth_status` | Show session validity and minutes until expiry |

### Discovery

| Tool | What it does |
|------|-------------|
| `list_datasources` | List all configured datasources with UIDs, types, and names |
| `list_folders` | List all Grafana dashboard folders with UIDs |
| `list_dashboards` | List all dashboards inside a folder |
| `get_dashboard_info` | Full dashboard definition — panels, template variables, datasource refs |
| `get_label_values` | Enumerate Prometheus label values (namespace, pod, cluster, etc.) |
| `search_dashboards` | Search dashboards by title or tag across all folders |
| `get_alert_rules` | Get active Grafana alert rule states for a dashboard |
| `get_firing_alerts` | Get all currently firing alerts from Alertmanager |

### Metrics & Logs

| Tool | What it does |
|------|-------------|
| `query_metrics` | Run any PromQL expression — supports `time_from`/`time_to` for exact historical windows |
| `detect_anomalies` | Run a PromQL query and flag threshold breaches / spikes |
| `check_dashboard_health` | Query every panel in a dashboard and report all anomalies in one call |
| `query_logs` | Run a LogQL query against a Loki datasource |
| `create_annotation` | Mark an event (deploy, incident, etc.) on Grafana dashboards |
| `create_snapshot` | Create a shareable snapshot of a dashboard and return its URL |

### Investigation / Scenario tools

These composite tools each run multiple PromQL queries in parallel and return a single structured report in seven-banner format (see [Report format](#report-format) below).

| Tool | When to use |
|------|------------|
| `investigate_latency_spike` | Service slow, high p99, execution delays, process timeouts |
| `investigate_memory_pressure` | OOM risk, heap exhaustion, memory growing |
| `investigate_pod_instability` | Pod restarts, CrashLoopBackOff, not-ready containers |
| `investigate_error_spike` | 4xx/5xx errors rising, HTTP failures, 503s |
| `investigate_cpu_spike` | High CPU, CPU throttling, heat / load spike |
| `investigate_traffic_drop` | Request rate dropped, service unreachable |
| `investigate_jvm_health` | GC pressure, thread leaks, JVM heap deep dive |
| `compare_regions` | Compare usw1 / usw3 / usw5 — find the outlier region |

---

## Time windows

All investigation tools and `query_metrics` support two ways to specify a time range:

### Relative (from now)
```
range_minutes=60   →  last 60 minutes from now
range_minutes=120  →  last 2 hours from now
```

### Absolute IST window — for historical investigations
```
time_from="2026-07-15 16:00:00"
time_to="2026-07-15 18:00:00"
```

- Times are assumed **IST (UTC+5:30)** unless a timezone suffix is included
- Sub-minute precision is supported: `time_from="2026-07-23 14:06:00"`, `time_to="2026-07-23 14:06:30"`
- `time_from`/`time_to` always take priority over `range_minutes`
- The HEADER banner in every report shows the exact IST window when absolute times are used
- The **LINKS banner** (see below) contains real clickable Grafana deep-links only when absolute times are provided

**Examples:**
```
"Check CPU spike on July 15 between 4pm and 6pm IST"
→  time_from="2026-07-15 16:00:00", time_to="2026-07-15 18:00:00"

"What happened between 14:06:00 and 14:06:30 on July 23?"
→  time_from="2026-07-23 14:06:00", time_to="2026-07-23 14:06:30"

"Check the last hour"
→  range_minutes=60  (no deep-links generated)
```

---

## Report format

Every scenario tool returns a **seven-banner structured report**:

```
## * HEADER         scenario, namespace, datasource, time window, generated timestamp
## * TIMELINE       ESCALATING / STABLE / RECOVERING with primary metric trend
## * INFRASTRUCTURE pod/k8s health: restarts, CrashLoopBackOff, OOM kills, ready status
## * SERVICE        HTTP layer: error rate, p99 latency, request rate by status
## * METRICS        resource usage: CPU, memory, GC pause time, JVM heap, thread count
## * ANOMALIES      all detected threshold / spike breaches ranked by severity
## * LINKS          Grafana deep-links pre-zoomed to the investigation window
## FINDINGS         numbered [CRITICAL] / [WARNING] / [INFO] conclusions
```

Every banner is always present. Banners not applicable to a scenario contain `N/A -- <reason>` so the reader always knows where to look.

### LINKS banner

When `time_from`/`time_to` are provided, the LINKS banner contains clickable Grafana deep-links pre-zoomed to exactly that window:

```
## * LINKS

Summary Dashboard — investigation window:
   https://grafana.cloudtrust.rocks/d/lJN4K_ZKM?orgId=1&from=1752579000000&to=1752586200000

With CAI/JMX filters (usw2 prod):
   https://grafana.cloudtrust.rocks/d/lJN4K_ZKM?orgId=1&from=1752579000000&to=1752586200000
   &var-Prometheus=aws-uswest2&var-Service=CAI&var-job=CAI_jmxMetrics
```

When only `range_minutes` is used, the banner prompts you to add `time_from`/`time_to` to get clickable links.

> **Note:** Links are included in the tool output automatically. They are not written to a file unless you explicitly ask to save the report.

---

## Example conversations

**Investigate a historical incident with deep-links**
```
Check the CAI DS queue CPU and JVM health on July 15 between 4pm and 6pm IST
```
→ Runs `investigate_cpu_spike` + `investigate_jvm_health` with `time_from="2026-07-15 16:00:00"` / `time_to="2026-07-15 18:00:00"`. Report includes LINKS banner with pre-zoomed Grafana URLs.

**Sub-minute window**
```
Show me exactly what happened on July 23 between 14:06:00 and 14:06:30 IST
```
→ Queries the exact 30-second window. Deep-links open Grafana zoomed to those 30 seconds.

**Current health check**
```
Run a full health check on the PROD summary dashboard for the last 2 hours
```

**Root-cause a CPU spike**
```
CPU spike on application-integration-obm pods — check last hour
```

**Tenant analysis**
```
Which tenants are dispatching the most BPEL processes right now?
```

---

## Session lifecycle

| Event | What happens |
|-------|-------------|
| First tool call (no session) | Visible browser opens automatically — sign in with Okta, approve push |
| Server start | Reads `.grafana-session.json` silently — no browser, no login prompt |
| 3 min before expiry | Silent refresh via saved Okta cookies — no push needed |
| Okta cookies expire (~8–24 h) | Browser opens automatically on next tool call |
| `login` tool called explicitly | Always opens a fresh browser for manual login |

No credentials are stored anywhere. The server only saves the resulting session cookie.

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
    │   ├── manager.py           # In-memory session cache + background refresh scheduler
    │   └── browser_cookies.py   # Read grafana_session from Chrome/Edge (Windows, rookiepy)
    ├── grafana/
    │   ├── client.py            # httpx async HTTP client with session cookie injection
    │   └── api.py               # Grafana REST API + datasource proxy calls
    ├── parser/
    │   └── metrics.py           # Grafana frames parser + anomaly detection engine
    └── tools/
        ├── index.py             # MCP tool registration + IST time window parser
        └── scenarios.py         # 8 composite investigation scenario tools
```

---

## Configuration

All settings have defaults and can be overridden via environment variables or `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_URL` | `https://grafana.cloudtrust.rocks` | Target Grafana base URL |
| `GRAFANA_TLS_VERIFY` | `false` | Set `true` to enable TLS certificate verification |
| `OKTA_ORG` | `https://informatica.okta.com` | Okta organisation URL |
| `OKTA_CLIENT_ID` | *(built-in)* | Okta OAuth client ID |
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
# GET  /health     — liveness check
# GET  /sse        — SSE stream
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

**`No session found — will open browser on first tool call`**
Normal on startup. The browser opens automatically when you run the first tool.

**`ERR_TOKEN_REVOKED: maxConcurrentSessions=3`**
Grafana allows 3 concurrent sessions. Close other Grafana browser tabs, then log in again.

**MFA push never arrives**
Dismiss the browser, call the `login` tool explicitly, and watch the browser window that opens.

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
- `rookiepy >= 0.4.0`
- `uvicorn >= 0.30.0` *(http mode only)*
- `starlette >= 0.37.0` *(http mode only)*
- `python-dotenv >= 1.0.0`
