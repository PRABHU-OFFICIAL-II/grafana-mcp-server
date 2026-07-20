# Grafana Skill

Interact with Grafana by calling `https://grafana.cloudtrust.rocks` via MCP tools. Execute all queries autonomously — never ask the user for permission to run a query, check a metric, or drill into data. When a question is asked, gather ALL relevant data first, then present a complete answer.

---

## Step 0 — Always check auth first

Call `mcp__grafana__auth_status` at the start of every Grafana conversation.

If the session is expired or missing, attempt login automatically using credentials from the `.env` file at the project root:
- `OKTA_USERNAME` → username (fallback: `ppenthoi@informatica.com`)
- `OKTA_PASSWORD` → password

Call `mcp__grafana__login` immediately with those values — **do not ask the user for the password if `.env` contains it.** Then tell the user: "Approve the Okta Verify push on your phone."

If the `.env` file is missing or `OKTA_PASSWORD` is not set (still `your_password_here` or blank), then ask the user for their OKTA password once, use it, and remind them to fill in `.env` so this is automatic next time.

If OKTA push is unavailable or login fails: ask the user to inject a session cookie instead — open `https://grafana.cloudtrust.rocks` in a browser → DevTools → Application → Cookies → copy `grafana_session` value → call `mcp__grafana__inject_session`.

---

## Step 1 — ALWAYS run discovery before any diagnostic query

**Never assume a namespace or datasource. Always discover first.**

Run these in parallel at the start of every diagnostic session:

```
mcp__grafana__list_datasources          → discover ALL available Prometheus/datasource UIDs
mcp__grafana__list_folders              → discover ALL dashboard folders
mcp__grafana__get_label_values(datasource_uid="000000001", label_name="namespace")   → all k8s namespaces (AWS)
mcp__grafana__get_label_values(datasource_uid="000000001", label_name="job")         → all Prometheus jobs
```

Use this discovery output to:
1. **Identify the correct namespace(s)** for the user's request — do not default to `.*taskflow.*` unless the user explicitly mentions taskflow
2. **Identify the correct datasource UID** — if the pod/namespace has `az` in its name it is an Azure pod and requires an Azure datasource, NOT `000000001`
3. **Identify dashboards** relevant to the request by matching folder names and dashboard titles to keywords in the user's question
4. **Map pod patterns** — e.g. "DS queue" → look for pods matching `.*obm.*`, `.*ds.*`, `.*queue.*`; "CAI" → `application-integration-*`; "taskflow" → `.*taskflow.*`

---

### CRITICAL — Cloud-to-Datasource mapping

**`000000001` (aws-uswest2) covers AWS namespaces ONLY.** Any namespace or pod containing `az`, `gcp`, or `oci` in the name will return 0.000 on `000000001`. Always check the pod/namespace name first to select the correct cloud datasource.

#### Quick routing — read the pod/namespace name prefix:

| Name contains | Cloud | Primary datasource UID | Datasource name |
|---|---|---|---|
| `az` (azusw1, azemc1, azusw3, azapse1, azapauc1, azcac2, azntt, azeus1, etc.) | Azure | `tE-7Jyd4z` | azure-prod-all-regions |
| `gcp` (gcpusw1, gcpmec2, gcpemw2, gcpapac, gcpbr1, etc.) | GCP | `ts2296v4z` | gcp-prod-all-regions |
| `oci` (ociuse1, ociapac, ocibr1, ociemw2, etc.) | OCI | `d6fc012f-aa8e-43f8-866a-1a97e289abee` | oci-prod-all-regions |
| no cloud prefix (usw1, usw3, usw5, nac1, nausw, icinq, etc.) | AWS | `000000001` | aws-uswest2 |
| not specified / unknown | ALL | query `vi-L5FKMz` + `tE-7Jyd4z` + `ts2296v4z` + `d6fc012f-aa8e-43f8-866a-1a97e289abee` in parallel |  |

#### Full datasource UID reference by cloud:

**AWS datasources:**
| UID | Name | Scope |
|---|---|---|
| `000000001` | aws-uswest2 | US West 2 only (prod primary) |
| `vi-L5FKMz` | aws-prod-all-regions | All AWS prod regions |
| `OTJGilHGk` | aws-prod-usw2 | US West 2 prod |
| `Xr_9pMH4k` | aws-prod-usw4 | US West 4 prod |
| `iHVk-8HMz` | aws-prod-nac1 | NA Central 1 prod |
| `oPDfFRH4z` | aws-prod-euc1 | EU Central 1 prod |
| `LHnYSuH4z` | aws-prod-eus1 | EU South 1 prod |
| `2ZDuXRH4z` | aws-prod-apac | APAC prod |
| `tFW_0RH4z` | aws-prod-br1 | Brazil 1 prod |
| `nZRUcrH4z` | aws-prod-nausw | NA US West (iCinq) |
| `P-J0fWH4z` | aws-prod-icinq | iCinq cluster |
| `jWuBJL9Mz` | aws-stage | AWS staging |
| `4_bKJL9Mz` | aws-stage-usw2 | US West 2 staging |
| `ZpDdJL9Mz` | aws-preview | AWS preview |

**Azure datasources:**
| UID | Name | Scope |
|---|---|---|
| `tE-7Jyd4z` | azure-prod-all-regions | **All Azure prod regions — use this for any `az*` namespace** |
| `65bC8KpMk` | azure-ichs-westus2 | Azure US West 2 (ICHS cluster) |
| `P0n9WqsMz` | azure-ichs-useast | Azure US East |
| `CjF7eKp4z` | azure-prod-azusw1 | AKS US West 1 prod |
| `Dk8HfLP5z` | azure-prod-azusw3 | AKS US West 3 prod |
| `EL9IgMQ6z` | azure-prod-azemc1 | AKS EU Middle Central 1 |
| `FM0JhNR7z` | azure-prod-azemc2 | AKS EU Middle Central 2 |
| `GN1KiOS8z` | azure-prod-azapse1 | AKS AP Southeast 1 |
| `HO2LjPT9z` | azure-prod-azapauc1 | AKS AP Australia Central 1 |
| `befsvadqv60owf` | azure-infacloud-westus2 | Azure staging (West US 2) |

**GCP datasources:**
| UID | Name | Scope |
|---|---|---|
| `ts2296v4z` | gcp-prod-all-regions | **All GCP prod regions — use this for any `gcp*` namespace** |
| `gcp-usw1-uid` | gcp-prod-gcpusw1 | GKE US West 1 |
| `gcp-mec2-uid` | gcp-prod-gcpmec2 | GKE Middle East Central 2 |
| `gcp-emw2-uid` | gcp-prod-gcpemw2 | GKE Europe Middle West 2 |

**OCI datasources:**
| UID | Name | Scope |
|---|---|---|
| `d6fc012f-aa8e-43f8-866a-1a97e289abee` | oci-prod-all-regions | **All OCI prod regions — use this for any `oci*` namespace** |

**Other / shared datasources:**
| UID | Name | Type | Notes |
|---|---|---|---|
| `grafanacloud-logs` | Grafana Cloud Logs | Loki | Log aggregation |
| `grafanacloud-traces` | Grafana Cloud Traces | Tempo | Distributed tracing |
| *(multiple)* | CloudWatch | CloudWatch | AWS CloudWatch metrics |
| *(multiple)* | Stackdriver | Stackdriver | GCP Stackdriver metrics |

#### Namespace patterns by cloud and service:

**AWS namespaces** (datasource `000000001` / `vi-L5FKMz`):
- `cai-prod-usw1`, `cai-prod-usw3`, `cai-prod-usw5` — CAI prod US West
- `cai-prod-nac1` — CAI prod NA Central
- `cai-prod-euc1` — CAI prod EU Central
- `cai-prod-eus1` — CAI prod EU South
- `cai-prod-apac` — CAI prod APAC
- `cai-prod-br1` — CAI prod Brazil
- `cai-prod-nausw1`, `cai-prod-icinq1usw1` — iCinq / iCinq-specific cluster
- `taskflow-prod-usw1`, `taskflow-prod-usw3`, `taskflow-prod-usw5` — Taskflow prod
- `taskflow-prod-nac1`, `taskflow-prod-euc1` — Taskflow other regions
- `iics-prod-nausw*`, `iics-prod-icinq*` — IICS prod
- `cai-stage-usw1`, `taskflow-stage-usw1` — Staging
- `cai-preview-usw1`, `taskflow-preview-*` — Preview / C360

**Azure namespaces** (datasource `tE-7Jyd4z` / `65bC8KpMk`):
- `cai-prod-azusw1`, `cai-prod-azusw3` — CAI prod AKS US West
- `cai-prod-azemc1`, `cai-prod-azemc2` — CAI prod AKS EU Middle Central
- `cai-prod-azapse1` — CAI prod AKS AP Southeast
- `cai-prod-azapauc1` — CAI prod AKS AP Australia Central
- `cai-prod-azcac2` — CAI prod AKS Canada Central
- `cai-prod-azntt` — CAI prod AKS North Taiwan
- `taskflow-prod-azusw1`, `taskflow-prod-azusw3` — Taskflow prod AKS US West
- `taskflow-prod-azemc1`, `taskflow-prod-azapse1` — Taskflow prod AKS other regions
- `iics-prod-azusw1`, `iics-prod-azemc1` — IICS prod AKS

**GCP namespaces** (datasource `ts2296v4z`):
- `cai-prod-gcpusw1` — CAI prod GKE US West 1
- `cai-prod-gcpmec2` — CAI prod GKE Middle East Central 2
- `cai-prod-gcpemw2` — CAI prod GKE Europe Middle West 2
- `taskflow-prod-gcpusw1`, `taskflow-prod-gcpmec2` — Taskflow prod GKE

**OCI namespaces** (datasource `d6fc012f-aa8e-43f8-866a-1a97e289abee`):
- `cai-prod-ociuse1` — CAI prod OCI US East 1
- `cai-prod-ociapac` — CAI prod OCI APAC
- `taskflow-prod-ociuse1` — Taskflow prod OCI US East 1

**Rule summary:**
- Name has `az*` → `tE-7Jyd4z` (azure-prod-all-regions)
- Name has `gcp*` → `ts2296v4z` (gcp-prod-all-regions)
- Name has `oci*` → `d6fc012f-aa8e-43f8-866a-1a97e289abee` (oci-prod-all-regions)
- Name has no cloud prefix → `000000001` or `vi-L5FKMz` (AWS)
- Unspecified / unknown → query all 4 consolidated datasources in parallel

---

### Namespace resolution rules

| User mentions | Try these namespace patterns first |
|---|---|
| "CAI" / "application-integration" | `.*application-integration.*`, `.*taskflow.*` |
| "DS queue" / "DataSync" / "OBM" | `.*application-integration.*`, `.*obm.*`, `.*taskflow.*` |
| "taskflow" / "gateway" | `.*taskflow.*` |
| "stage" / "staging" | `.*stage.*`, `.*staging.*` |
| "preview" / "c360" | `.*preview.*`, `.*c360.*` |
| "prod" / "production" | `.*prod.*` |
| specific region (nac1, usw1, usw3, usw5) | filter pod label: `pod=~".*{region}.*"` |
| Azure region (azusw1, azemc1, azapse1, etc.) | use datasource `tE-7Jyd4z` and namespace `.*az{region}.*` |
| GCP region (gcpusw1, gcpmec2, gcpemw2, etc.) | use datasource `ts2296v4z` and namespace `.*gcp{region}.*` |
| OCI region (ociuse1, ociapac, etc.) | use datasource `d6fc012f-aa8e-43f8-866a-1a97e289abee` and namespace `.*oci{region}.*` |
| not specified | run on all 4 consolidated datasources in parallel |

### Datasource resolution rules

1. **Read the pod/namespace name first** — check for `az`, `gcp`, `oci` prefixes
2. `az*` → `tE-7Jyd4z`; `gcp*` → `ts2296v4z`; `oci*` → `d6fc012f-aa8e-43f8-866a-1a97e289abee`; no prefix → `000000001`
3. For ambiguous requests: query all 4 consolidated datasources (`vi-L5FKMz`, `tE-7Jyd4z`, `ts2296v4z`, `d6fc012f-aa8e-43f8-866a-1a97e289abee`) in parallel
3. If a metric returns 0.000 on the chosen datasource — **do not report "no data" yet**
4. Instead retry on the other cloud datasources from the mapping table above
5. Report which datasource returned data and use that UID for all follow-up queries
6. Known gap: `CAI_jmxMetrics` job (JVM heap, thread pool, process acquire, rejected messages) may only be in specific datasources; find it via `get_label_values(label_name="job")` on each candidate datasource

---

## Environment — know this, never ask

**Grafana:** `https://grafana.cloudtrust.rocks` — Enterprise v12.3.3  
**Default Datasource:** Prometheus, UID `000000001`, type `prometheus`  
**OrgId:** 1 (Main Org.)

**Key folder:** CAI-TASKFLOW — folderUID `SDX6vxkGz`  
**Key dashboard:** "Summary (Production ICRT AWS Cluster)" — UID `lJN4K_ZKM`

**Known namespaces (may grow — always verify via discovery):**
- `.*taskflow.*` — all taskflow gateway services (prod/stage/preview)
- `.*application-integration.*` — CAI application-integration pods including OBM/DS queue pods
- `.*harnessgitops.*` — ArgoCD / GitOps infra
- `.*monitoring.*` — monitoring stack

**Known pod families (may grow — always verify via discovery):**
| Pod family | Pattern | Service |
|---|---|---|
| CAI main workers | `application-integration-{0..7}` | Core CAI processing |
| CAI OBM/DS queue | `application-integration-obm-*` | DS queue / OBM tenant jobs |
| CAI OBM tenant-specific | `application-integration-obm-{tenant}-*` | Tenant-scoped OBM jobs |
| CAI HT | `application-integration-ht-*` | High-throughput workers |
| CAI UI | `application-integration-ui-*` | UI layer |
| Taskflow prod | `taskflow-prod-{region}-taskflowgateway-*` | usw1/usw3/usw5 |
| Taskflow stage | `taskflow-stage-usw1-taskflowgateway-*` | Stage |
| Taskflow preview | `taskflow-preview-*-taskflowgateway-*` | Preview / c360 |
| iCinq | `taskflow-prod-icinq1usw1-*` | iCinq tenant |
| Delegates | `intcloud-cai*-delegate-*` | Harness delegates |

**Service topology:**
| Environment | Regions | Pod pattern |
|-------------|---------|-------------|
| prod | usw1, usw3, usw5 | `taskflow-prod-{region}-taskflowgateway-*` |
| stage | usw1 | `taskflow-stage-usw1-taskflowgateway-*` |
| preview | usw1, c360usw1 | `taskflow-preview-*-taskflowgateway-*` |
| app-integration (CAI) | — | `application-integration-*` (multiple families) |

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
- **Never default to `.*taskflow.*`** unless the user explicitly mentions taskflow — run discovery first and use the correct namespace.
- When the user asks about CPU/memory/errors/latency/health, run ALL relevant queries in parallel and present a unified report.
- When you find a spike or anomaly, immediately run a follow-up query to correlate it (e.g. spike in CPU → check memory, check error rate, check request rate at the same time window).
- When region/pod/environment is not specified, check **all** of them and filter down to what's interesting.
- **If a query returns 0.000 / empty results:**
  1. Try alternate namespace regex (broader: `namespace=~".*"`)
  2. Try alternate datasource UIDs (from `list_datasources`)
  3. Try alternate pod label filter (e.g. `pod=~".*{keyword}.*"` without namespace)
  4. Only then report "no data found" with the datasources and namespaces attempted

---

## Tool reference

| Tool | Required params | Purpose |
|------|----------------|---------|
| `mcp__grafana__auth_status` | — | Check session expiry |
| `mcp__grafana__login` | `username`, `password` | OKTA push auth |
| `mcp__grafana__inject_session` | `grafana_session`, `expires_at_unix_seconds` | Manual cookie injection |
| `mcp__grafana__list_datasources` | — | **Discover ALL datasource UIDs** — run in Step 1 |
| `mcp__grafana__list_folders` | — | List all dashboard folders |
| `mcp__grafana__list_dashboards` | `folder_uid` | List dashboards in a folder |
| `mcp__grafana__get_dashboard_info` | `dashboard_uid` | Panels, variables, datasource info |
| `mcp__grafana__get_label_values` | `datasource_uid`, `label_name` | **Discover namespaces, jobs, pods** — run in Step 1 |
| `mcp__grafana__query_metrics` | `datasource_uid`, `expr` | Run PromQL, returns time series |
| `mcp__grafana__detect_anomalies` | `datasource_uid`, `expr` | Spike/outage detection |
| `mcp__grafana__check_dashboard_health` | `dashboard_uid` | Full panel-by-panel health check |
| `mcp__grafana__get_alert_rules` | `dashboard_uid` | Active alert rules |

### Scenario / investigation tools (use these for all diagnostic questions)

| Tool | Params | When to use |
|------|--------|-------------|
| `mcp__grafana__investigate_latency_spike` | `datasource_uid`, `namespace`, `range_minutes`, `service_filter?` | Slow response times, high p99, execution delays, process timeouts |
| `mcp__grafana__investigate_memory_pressure` | `datasource_uid`, `namespace`, `range_minutes` | OOM kills, heap exhaustion, memory growing over time |
| `mcp__grafana__investigate_pod_instability` | `datasource_uid`, `namespace`, `range_minutes` | Pod restarts, CrashLoopBackOff, not-ready containers |
| `mcp__grafana__investigate_error_spike` | `datasource_uid`, `namespace`, `range_minutes` | 4xx/5xx errors rising, HTTP failures, 503s |
| `mcp__grafana__investigate_cpu_spike` | `datasource_uid`, `namespace`, `range_minutes` | High CPU, CPU throttling, heat / load spike |
| `mcp__grafana__investigate_traffic_drop` | `datasource_uid`, `namespace`, `range_minutes` | Request rate dropped, service unreachable, scale-down |
| `mcp__grafana__investigate_jvm_health` | `datasource_uid`, `namespace`, `range_minutes`, `job?` | GC pressure, thread leaks, JVM heap / metaspace deep dive |
| `mcp__grafana__compare_regions` | `datasource_uid`, `namespace`, `range_minutes`, `regions?` | Comparing usw1 / usw3 / usw5, regional anomaly |

**When running scenario tools after discovery:**
- Use the **discovered namespace** that matches the user's request — not a hardcoded default
- Use the **discovered datasource UID** where the relevant metrics exist
- Pass `service_filter` with pod-specific label matchers when the user mentions a specific service, queue, or tenant (e.g. `service_filter='pod=~".*obm.*"'` for DS queue / OBM)

---

## PromQL patterns — use these directly

### CPU

```promql
# CPU usage in cores per pod — use discovered namespace
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=~"<DISCOVERED_NS>", container!="POD", container!=""}[5m])) * 100

# Top CPU consumers across ALL namespaces (no namespace filter — for broad discovery)
topk(10, sum by (pod, namespace) (rate(container_cpu_usage_seconds_total{container!="POD", container!=""}[5m])) * 100)

# CPU throttling ratio (0–1, high = throttled)
sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total{namespace=~"<DISCOVERED_NS>", container!="POD"}[5m]))
  / sum by (pod) (rate(container_cpu_cfs_periods_total{namespace=~"<DISCOVERED_NS>", container!="POD"}[5m]))
```

### Memory

```promql
# Working set memory in MB per pod
sum by (pod) (container_memory_working_set_bytes{namespace=~"<DISCOVERED_NS>", container!="POD", container!=""}) / 1024 / 1024

# OOM kills
kube_pod_container_status_last_terminated_reason{namespace=~"<DISCOVERED_NS>", reason="OOMKilled"}

# Top memory consumers across ALL namespaces
topk(10, sum by (pod, namespace) (container_memory_working_set_bytes{container!="POD", container!=""}) / 1024 / 1024)
```

### JVM (application-integration pods via JMX)

```promql
# JVM heap used in MB — try job="CAI_jmxMetrics" on ALL discovered datasources
sum by (pod) (jvm_memory_used_bytes{area="heap", job="CAI_jmxMetrics"}) / 1024 / 1024

# JVM heap % of max
sum by (pod) (jvm_memory_used_bytes{area="heap", job="CAI_jmxMetrics"})
  / sum by (pod) (jvm_memory_max_bytes{area="heap", job="CAI_jmxMetrics"}) * 100

# GC pause time rate (ms/s — high = GC pressure)
sum by (pod) (rate(jvm_gc_pause_seconds_sum{job="CAI_jmxMetrics"}[5m])) * 1000

# Thread states
jvm_threads_states_threads{job="CAI_jmxMetrics"}

# Non-heap (metaspace) used
sum by (pod) (jvm_memory_used_bytes{area="nonheap", job="CAI_jmxMetrics"}) / 1024 / 1024
```

### HTTP / Request Rate

```promql
# HTTP requests per second — no namespace restriction for discovery
sum by (pod, namespace, status) (rate(http_server_requests_seconds_count{}[5m]))

# HTTP 503 specifically
sum by (pod, namespace) (rate(http_server_requests_seconds_count{status="503"}[5m]))

# HTTP error rate
sum by (pod) (rate(http_server_requests_seconds_count{namespace=~"<DISCOVERED_NS>", status=~"4..|5.."}[5m]))
  / sum by (pod) (rate(http_server_requests_seconds_count{namespace=~"<DISCOVERED_NS>"}[5m])) * 100

# HTTP p99 latency in ms
histogram_quantile(0.99, sum by (pod, le) (rate(http_server_requests_seconds_bucket{namespace=~"<DISCOVERED_NS>"}[5m]))) * 1000
```

### Pod / Container Health

```promql
# Pod restarts — use discovered namespace
increase(kube_pod_container_status_restarts_total{namespace=~"<DISCOVERED_NS>"}[1h])

# Not-running pods across ALL namespaces
kube_pod_status_phase{phase!="Running", phase!="Succeeded"}

# Container ready status (0 = not ready)
kube_pod_container_status_ready{namespace=~"<DISCOVERED_NS>"}

# CrashLoopBackOff
kube_pod_container_status_waiting_reason{namespace=~"<DISCOVERED_NS>", reason="CrashLoopBackOff"}
```

### CAI-Taskflow specific (JMX / business metrics)

```promql
# Active process count — try on ALL datasources if 000000001 returns 0
sum by (pod) (cai_active_processes{job="CAI_jmxMetrics"})

# Work manager thread pool usage
sum by (pod) (cai_work_manager_threads_active{job="CAI_jmxMetrics"})

# Rejected messages per second
rate(cai_rejected_messages_total{job="CAI_jmxMetrics"}[5m])

# Process acquire time (p99 ms) — KEY metric for DS queue 503/timeout diagnosis
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
- **503 / process timeout** → check `cai_work_manager_threads_active`, `cai_process_acquire_duration` p99, `cai_rejected_messages_total`, CPU burst on OBM pods

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
| specific date range | calculate exact minutes from now to start of range |

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

**When scenario tools return 0.000 for key metrics** — do NOT accept this as final. Report the coverage gap explicitly (which datasource was missing the metric, which alternative datasource to check) and re-run on alternate datasource UIDs discovered in Step 1.

---

## Navigation flow

```
auth_status → (login if expired)
  ↓
STEP 1 — CLOUD ROUTING (before any query):
  Read the pod/namespace/region in the user's request.
  Does the name contain "az"?  → primary datasource = tE-7Jyd4z  (azure-prod-all-regions)
                                  also check 65bC8KpMk for azusw1/azusw3 specifically
  Does the name contain "gcp"? → primary datasource = ts2296v4z   (gcp-prod-all-regions)
  Does the name contain "oci"? → primary datasource = d6fc012f-aa8e-43f8-866a-1a97e289abee (oci-prod-all-regions)
  No cloud prefix?             → primary datasource = 000000001   (aws-uswest2)
                                  also try vi-L5FKMz for multi-region AWS coverage
  UNSPECIFIED / UNKNOWN        → query all 4 consolidated datasources in parallel:
                                  vi-L5FKMz (AWS) + tE-7Jyd4z (Azure) + ts2296v4z (GCP) + d6fc012f-aa8e-43f8-866a-1a97e289abee (OCI)
  ↓
STEP 2 — NAMESPACE DISCOVERY on the correct datasource(s):
  get_label_values(datasource_uid=<ROUTED_UID>, label_name="namespace")
  get_label_values(datasource_uid=<ROUTED_UID>, label_name="job")
  → Confirm the namespace exists in the chosen datasource before querying
  ↓
STEP 3 — INTENT MAPPING:
  Map user's request keywords → correct namespace(s), datasource(s), pod pattern(s)
  e.g. "cai-prod-azusw1 error"  → datasource=tE-7Jyd4z, namespace="cai-prod-azusw1"
  e.g. "DS queue 503"           → namespace=~".*application-integration.*", pod=~".*obm.*",
                                   datasource = whichever UID has CAI_jmxMetrics job
  e.g. "taskflow latency usw3"  → namespace=~"taskflow-prod-usw3", datasource=000000001
  e.g. "nac1 pod"               → pod=~".*nac1.*" across all namespaces, try both clouds
  ↓
STEP 4 — INVESTIGATION:
  Is this a diagnostic question (latency / errors / CPU / memory / pods / JVM / traffic / 503)?
    YES → call ALL matching scenario tools with CORRECT datasource + namespace
          run in parallel; pass service_filter for pod-specific narrowing
    NO  → list_dashboards → get_dashboard_info → query_metrics / check_dashboard_health
  ↓
STEP 5 — FILL GAPS:
  For any metric returning 0.000 → retry on the other cloud datasources (all 4 consolidated UIDs)
  For any namespace returning empty → broaden namespace filter or check alternate cloud
  Never say "Azure not in scope" — Azure datasources ARE present (tE-7Jyd4z)
  Never say "GCP not in scope" — GCP datasources ARE present (ts2296v4z)
  Never say "OCI not in scope" — OCI datasources ARE present (d6fc012f-aa8e-43f8-866a-1a97e289abee)
  Report coverage gaps explicitly in FINDINGS with which datasource was tried
```

---

## Scenario keyword → tool mapping

Use this to select the right scenario tools for the user's question:

| User says | Tools to run |
|---|---|
| "503" / "service unavailable" / "process timeout" / "queue timeout" | `investigate_error_spike` + `investigate_latency_spike` + direct queries: `cai_work_manager_threads_active`, `cai_process_acquire_duration`, `cai_rejected_messages_total` |
| "slow" / "latency" / "timeout" / "delay" | `investigate_latency_spike` + `investigate_cpu_spike` + `investigate_jvm_health` |
| "memory" / "OOM" / "heap" / "GC" | `investigate_memory_pressure` + `investigate_jvm_health` |
| "crash" / "restart" / "CrashLoop" / "not ready" | `investigate_pod_instability` + `investigate_error_spike` |
| "CPU" / "high load" / "spike" / "hot" | `investigate_cpu_spike` + `investigate_jvm_health` |
| "error" / "4xx" / "5xx" / "failure" | `investigate_error_spike` + `investigate_pod_instability` |
| "traffic drop" / "no requests" / "unreachable" | `investigate_traffic_drop` + `investigate_pod_instability` |
| "ops analytics" / "health check" / "overall status" | ALL 7 scenario tools in parallel |
| "region" / "compare" / "usw1 vs usw3" | `compare_regions` + `investigate_cpu_spike` |
| "DS queue" / "OBM" / "DataSync" | `investigate_error_spike` + `investigate_latency_spike` + `investigate_cpu_spike` + direct CAI JMX queries |
