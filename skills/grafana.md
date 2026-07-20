# Grafana Skill

Interact with Grafana by calling `https://grafana.cloudtrust.rocks` via MCP tools. Execute all queries autonomously — never ask the user for permission to run a query, check a metric, or drill into data. When a question is asked, gather ALL relevant data first, then present a complete answer.

---

## Step 0 — Always check auth first

Call `mcp__grafana__auth_status` at the start of every Grafana conversation. If the session is valid, proceed immediately.

If the session is **expired or missing**, follow this login flow — in order, without asking the user first:

### Login flow (execute autonomously)

**Step A — Read `.env` from the project directory**

Use the Read tool to read the file at:
```
C:\Users\ppenthoi\Documents\DEV\cdi-mcp-servers\grafana-mcp-server\.env
```
Parse `USERNAME` and `PASSWORD` values from it. The format is `KEY=value`, one per line.

**Step B — Log in with the credentials from `.env`**

Call `mcp__grafana__login` with the `username` and `password` read from the `.env` file. Approve the Okta Verify push on the phone.

**Step C — Fallback: ask the user (only if `.env` is missing or unreadable)**

If the Read tool returns an error (file not found or unreadable), ask the user for their OKTA username and password, then call `mcp__grafana__login` with the provided values.

**Do NOT ask the user for credentials if `.env` exists and contains `USERNAME`/`PASSWORD`.**

### If OKTA push is unavailable

Use `mcp__grafana__inject_session` with a `grafana_session` cookie from browser DevTools (Application → Cookies → grafana_session).

---

## Environment — know this, never ask

**Grafana:** `https://grafana.cloudtrust.rocks` — Enterprise v12.3.3  
**Datasource:** Prometheus, UID `000000001`, type `prometheus`  
**OrgId:** 1 (Main Org.)

**Key folder:** CAI-TASKFLOW — folderUID `SDX6vxkGz`  
**Key dashboard:** "Summary (Production ICRT AWS Cluster)" — UID `lJN4K_ZKM`

**Namespaces in use:**
- `.*taskflow.*` — all taskflow services (prod/stage/preview)
- `.*application-integration.*` or container label matching — application-integration pods

**Service topology:**
| Environment | Regions | Pod pattern |
|-------------|---------|-------------|
| prod | usw1, usw3, usw5 | `taskflow-prod-{region}-taskflowgateway-*` |
| stage | usw1 | `taskflow-stage-usw1-taskflowgateway-*` |
| preview | usw1, c360usw1 | `taskflow-preview-*-taskflowgateway-*` |
| app-integration | — | `application-integration-{0..7}` |

**Template variables on Summary dashboard:**
- `Prometheus` datasource = `aws-uswest2`
- `Service` = `CAI`
- `job` = `CAI_jmxMetrics`
- `pod` = `USW3:PROD`
- `host` = `$__all`

---

## Autonomous execution rules

- **Never ask** "Can I run this query?" or "Should I check X?" — just do it.
- **Never ask** "Do you want me to drill into Y?" — if it's relevant to the question, drill in.
- When the user asks about CPU/memory/errors/latency/health, run ALL relevant queries in parallel and present a unified report.
- When you find a spike or anomaly, immediately run a follow-up query to correlate it (e.g. spike in CPU → check memory, check error rate, check request rate at the same time window).
- When region/pod/environment is not specified, check **all** of them and filter down to what's interesting.
- If a query returns empty, try an alternate label selector before reporting "no data".

---

## Tool reference

| Tool | Required params | Purpose |
|------|----------------|---------|
| `mcp__grafana__auth_status` | — | Check session expiry |
| `mcp__grafana__login` | `username`, `password` | OKTA push auth |
| `mcp__grafana__inject_session` | `grafana_session`, `expires_at_unix_seconds` | Manual cookie injection |
| `mcp__grafana__list_folders` | — | List all dashboard folders |
| `mcp__grafana__list_dashboards` | `folder_uid` | List dashboards in a folder |
| `mcp__grafana__get_dashboard_info` | `dashboard_uid` | Panels, variables, datasource info |
| `mcp__grafana__get_label_values` | `datasource_uid`, `label_name` | Valid values for a Prometheus label |
| `mcp__grafana__query_metrics` | `datasource_uid`, `expr` | Run PromQL, returns time series |
| `mcp__grafana__detect_anomalies` | `datasource_uid`, `expr` | Spike/outage detection |
| `mcp__grafana__check_dashboard_health` | `dashboard_uid` | Full panel-by-panel health check |
| `mcp__grafana__get_alert_rules` | `dashboard_uid` | Active alert rules |

### Scenario / investigation tools (use these for all diagnostic questions)

| Tool | Params | When to use |
|------|--------|-------------|
| `mcp__grafana__investigate_latency_spike` | `datasource_uid`, `namespace`, `range_minutes`, `service_filter?` | Slow response times, high p99, execution delays |
| `mcp__grafana__investigate_memory_pressure` | `datasource_uid`, `namespace`, `range_minutes` | OOM kills, heap exhaustion, memory growing over time |
| `mcp__grafana__investigate_pod_instability` | `datasource_uid`, `namespace`, `range_minutes` | Pod restarts, CrashLoopBackOff, not-ready containers |
| `mcp__grafana__investigate_error_spike` | `datasource_uid`, `namespace`, `range_minutes` | 4xx/5xx errors rising, HTTP failures |
| `mcp__grafana__investigate_cpu_spike` | `datasource_uid`, `namespace`, `range_minutes` | High CPU, CPU throttling, heat / load spike |
| `mcp__grafana__investigate_traffic_drop` | `datasource_uid`, `namespace`, `range_minutes` | Request rate dropped, service unreachable, scale-down |
| `mcp__grafana__investigate_jvm_health` | `datasource_uid`, `namespace`, `range_minutes`, `job?` | GC pressure, thread leaks, JVM heap / metaspace deep dive |
| `mcp__grafana__compare_regions` | `datasource_uid`, `namespace`, `range_minutes`, `regions?` | Comparing usw1 / usw3 / usw5, regional anomaly |

All `query_metrics` and scenario tool calls use `datasource_uid = "000000001"` unless explicitly told otherwise.

---

## PromQL patterns — use these directly

### CPU

```promql
# CPU usage in cores per pod (preferred — accurate)
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=~".*taskflow.*", container!="POD", container!=""}[5m])) * 100

# CPU % of limit per pod
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=~".*taskflow.*", container!="POD", container!=""}[5m]))
  / sum by (pod) (kube_pod_container_resource_limits{namespace=~".*taskflow.*", resource="cpu"}) * 100

# CPU throttling ratio (0–1, high = throttled)
sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total{namespace=~".*taskflow.*", container!="POD"}[5m]))
  / sum by (pod) (rate(container_cpu_cfs_periods_total{namespace=~".*taskflow.*", container!="POD"}[5m]))

# Top CPU consumers right now
topk(10, sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=~".*taskflow.*", container!="POD", container!=""}[5m])) * 100)
```

### Memory

```promql
# Working set memory in MB per pod
sum by (pod) (container_memory_working_set_bytes{namespace=~".*taskflow.*", container!="POD", container!=""}) / 1024 / 1024

# Memory % of limit per pod
sum by (pod) (container_memory_working_set_bytes{namespace=~".*taskflow.*", container!="POD", container!=""})
  / sum by (pod) (kube_pod_container_resource_limits{namespace=~".*taskflow.*", resource="memory"}) * 100

# OOM kills (non-zero = pod was OOM-killed)
kube_pod_container_status_last_terminated_reason{namespace=~".*taskflow.*", reason="OOMKilled"}

# Memory RSS in MB
sum by (pod) (container_memory_rss{namespace=~".*taskflow.*", container!="POD", container!=""}) / 1024 / 1024

# Top memory consumers
topk(10, sum by (pod) (container_memory_working_set_bytes{namespace=~".*taskflow.*", container!="POD", container!=""}) / 1024 / 1024)
```

### JVM (application-integration pods via JMX)

```promql
# JVM heap used in MB
sum by (pod) (jvm_memory_used_bytes{area="heap", job="CAI_jmxMetrics"}) / 1024 / 1024

# JVM heap % of max
sum by (pod) (jvm_memory_used_bytes{area="heap", job="CAI_jmxMetrics"})
  / sum by (pod) (jvm_memory_max_bytes{area="heap", job="CAI_jmxMetrics"}) * 100

# GC pause time rate (ms/s — high = GC pressure)
sum by (pod) (rate(jvm_gc_pause_seconds_sum{job="CAI_jmxMetrics"}[5m])) * 1000

# GC collections per second
sum by (pod, cause) (rate(jvm_gc_pause_seconds_count{job="CAI_jmxMetrics"}[5m]))

# Thread count
jvm_threads_live_threads{job="CAI_jmxMetrics"}

# Thread states
jvm_threads_states_threads{job="CAI_jmxMetrics"}

# Non-heap (metaspace) used
sum by (pod) (jvm_memory_used_bytes{area="nonheap", job="CAI_jmxMetrics"}) / 1024 / 1024
```

### HTTP / Request Rate

```promql
# HTTP requests per second by pod and status
sum by (pod, status) (rate(http_server_requests_seconds_count{namespace=~".*taskflow.*"}[5m]))

# HTTP error rate (4xx+5xx / total)
sum by (pod) (rate(http_server_requests_seconds_count{namespace=~".*taskflow.*", status=~"4..|5.."}[5m]))
  / sum by (pod) (rate(http_server_requests_seconds_count{namespace=~".*taskflow.*"}[5m])) * 100

# HTTP p99 latency in ms
histogram_quantile(0.99, sum by (pod, le) (rate(http_server_requests_seconds_bucket{namespace=~".*taskflow.*"}[5m]))) * 1000

# HTTP p95 latency
histogram_quantile(0.95, sum by (pod, le) (rate(http_server_requests_seconds_bucket{namespace=~".*taskflow.*"}[5m]))) * 1000

# HTTP avg latency
sum by (pod) (rate(http_server_requests_seconds_sum{namespace=~".*taskflow.*"}[5m]))
  / sum by (pod) (rate(http_server_requests_seconds_count{namespace=~".*taskflow.*"}[5m])) * 1000
```

### Pod / Container Health

```promql
# Pod restarts in last hour
increase(kube_pod_container_status_restarts_total{namespace=~".*taskflow.*"}[1h])

# Not-running pods
kube_pod_status_phase{namespace=~".*taskflow.*", phase!="Running", phase!="Succeeded"}

# Container ready status (0 = not ready)
kube_pod_container_status_ready{namespace=~".*taskflow.*"}

# Pods not scheduled
kube_pod_status_scheduled{namespace=~".*taskflow.*", condition="false"}

# CrashLoopBackOff
kube_pod_container_status_waiting_reason{namespace=~".*taskflow.*", reason="CrashLoopBackOff"}
```

### Network

```promql
# Network receive bytes/sec per pod
sum by (pod) (rate(container_network_receive_bytes_total{namespace=~".*taskflow.*"}[5m]))

# Network transmit bytes/sec per pod
sum by (pod) (rate(container_network_transmit_bytes_total{namespace=~".*taskflow.*"}[5m]))

# Network errors
sum by (pod) (rate(container_network_receive_errors_total{namespace=~".*taskflow.*"}[5m]))
  + sum by (pod) (rate(container_network_transmit_errors_total{namespace=~".*taskflow.*"}[5m]))
```

### Disk / Storage

```promql
# Filesystem usage % per pod
(container_fs_usage_bytes{namespace=~".*taskflow.*", container!="POD", container!=""}
  / container_fs_limit_bytes{namespace=~".*taskflow.*", container!="POD", container!=""}) * 100

# PVC usage %
(kubelet_volume_stats_used_bytes{namespace=~".*taskflow.*"}
  / kubelet_volume_stats_capacity_bytes{namespace=~".*taskflow.*"}) * 100
```

### CAI-Taskflow specific (JMX / business metrics)

```promql
# Active process count
sum by (pod) (cai_active_processes{job="CAI_jmxMetrics"})

# Work manager thread pool usage
sum by (pod) (cai_work_manager_threads_active{job="CAI_jmxMetrics"})

# Rejected messages per second
rate(cai_rejected_messages_total{job="CAI_jmxMetrics"}[5m])

# Process acquire time (p99 ms)
histogram_quantile(0.99, rate(cai_process_acquire_duration_seconds_bucket{job="CAI_jmxMetrics"}[5m])) * 1000

# Process save time (p99 ms)
histogram_quantile(0.99, rate(cai_process_save_duration_seconds_bucket{job="CAI_jmxMetrics"}[5m])) * 1000

# Uptime in hours
process_uptime_seconds{job="CAI_jmxMetrics"} / 3600
```

---

## Complex query patterns

### Spike detection — run these when user asks about spikes

Use `mcp__grafana__detect_anomalies` for automated detection, but also run `query_metrics` with `range_minutes=60` and compare `max` vs `avg`. A spike is: `max > 2.5 * avg`.

For CPU spikes, also check CPU throttling ratio. For memory spikes, check OOM kills and GC pause rate.

### Correlation analysis

When a spike is found in one metric:
1. Note the time window of the spike (infer from `max` being near `current` or `min`)
2. Run the correlated metrics over the same `range_minutes`
3. Report which metrics moved together

Standard correlation pairs:
- CPU spike → check JVM GC pause rate, thread count, HTTP request rate
- Memory spike → check JVM heap %, GC frequency, OOM kills
- HTTP error spike → check pod restarts, CPU throttling, latency p99
- Latency spike → check CPU throttling, thread pool saturation, DB connection pool

### Multi-region comparison

For prod comparisons, group results by region with `label_values` or filter by pod name pattern:
- usw1: `pod=~".*usw1.*"`
- usw3: `pod=~".*usw3.*"`
- usw5: `pod=~".*usw5.*"`

### Time range guidance

| User says | `range_minutes` |
|-----------|:-:|
| "right now" / "current" | 15 |
| "last hour" / default | 60 |
| "today" / "this morning" | 480 |
| "last 24 hours" | 1440 |
| "last week" | 10080 |

---

## Reporting format — CRITICAL: always use six-banner output

**For any diagnostic or investigative question, call the matching scenario tool and output its result VERBATIM.** Do NOT re-summarize, re-format, or convert the tool output into tables or prose. The tool returns a structured six-banner report — pass it through unchanged as your response.

The six banners the tool always returns:
```
## * HEADER        — scenario, namespace, datasource, range, timestamp
## * TIMELINE      — ESCALATING / STABLE / RECOVERING with primary metric trend
## * INFRASTRUCTURE — pod restarts, CrashLoopBackOff, OOM kills, ready status
## * SERVICE       — HTTP error rate, p99 latency, request rate
## * METRICS       — CPU, memory, GC pause, JVM heap, threads
## * ANOMALIES     — all detected threshold/spike breaches
## FINDINGS        — numbered [CRITICAL] / [WARNING] / [INFO] conclusions
```

Banners irrelevant to a scenario contain `N/A -- <reason>`. This is intentional — every banner is always present so the consumer always knows where to look.

**Only use `query_metrics` / `detect_anomalies` directly** when the user asks something that does not map to any scenario tool (e.g. a one-off custom PromQL query, listing dashboards, checking alerts). For those, present the raw data concisely — no fabricated summary format.

---

## Navigation flow

```
auth_status → (login if expired)
  ↓
  Is this a diagnostic question (latency / errors / CPU / memory / pods / JVM / traffic)?
    YES → call the matching scenario tool → output the six-banner result verbatim
    NO  → list_folders → list_dashboards → get_dashboard_info
            → query_metrics / detect_anomalies / check_dashboard_health
```
