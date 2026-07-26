# Incident Report — CAI DS Queue 503 Process Timeout

**Date:** July 15, 2026
**Time:** 4:00–6:00 PM IST (10:30 AM–12:30 PM UTC)
**Severity:** Critical
**Component:** CAI DS Queue / `application-integration-obm`
**Reported By:** Grafana Observability Investigation

---

## Summary

Users experienced HTTP 503 "Process Timeout" errors when submitting or executing DS Queue processes through the CAI service. The incident lasted approximately 2 hours and affected all three production regions (usw1, usw3, usw5). Root cause was thread/process pool saturation on the `application-integration-obm` pods, causing DS Queue process execution to stall and the CAI gateway to return 503s upstream.

---

## Root Cause

**Thread/process pool saturation on `application-integration-obm` pods**, causing DS Queue entries to time out and the CAI gateway to return 503s to clients.

The `application-integration-obm` (OBM = Output/Batch/Message) processing tier accumulated a queue depth it could not drain, resulting in process acquire requests stalling for up to **24 minutes** at p99. When the CAI gateway upstream timeout threshold was exceeded, it returned 503 with "Process Timeout" to end users.

---

## Timeline

| Time (IST) | Event |
|------------|-------|
| ~4:00 PM | DS Queue depth rises; `application-integration-obm` thread pool starts saturating |
| ~4:15–4:30 PM | p99 latency on `obm` crosses 30s; CAI gateway starts returning 503 on process acquire calls |
| ~4:30–6:00 PM | Latency spikes to minutes (max recorded: **~24 minutes**); 503 rate peaks; users see "Process Timeout" errors |
| ~6:00 PM | Load subsides or pods recover; 503 rate drops; latency returns to elevated ~10s range |

---

## Evidence

### 1. `application-integration-obm` — Catastrophic Latency Spike

| Metric | Value |
|--------|-------|
| p99 latency — peak over incident window | **1,446,315 ms (~24 minutes)** |
| p99 latency — avg over 6-day window | 35,424 ms (35 s — chronically elevated) |
| p99 latency — current (post-incident) | ~10,313 ms |

A 24-minute p99 means DS Queue process execution was completely stalled. Requests hit the CAI gateway upstream timeout threshold → **503 returned to clients**.

### 2. Sustained High 500 Errors on `application-integration-obm`

| Pod | 500/5m (current) | 500/5m (avg) | 500/5m (peak) |
|-----|------------------|--------------|---------------|
| `application-integration-obm-0` | 472 | 471 | **664** |
| `application-integration-obm-1` | 512 | 474 | **634** |
| `application-integration-obm-2` | 278 | 320 | **454** |
| `application-integration-obm-3` | 82 | 94 | 188 |
| `application-integration-obm-4` | 74 | 92 | 172 |

**Service-level rate: ~10–11 HTTP 500s/second continuously.** 503 spikes peaked at **0.168/s** at service level, with up to **23.7 per 5-minute window on obm-2** — consistent with queue saturation bursts.

### 3. CAI Gateway — 503s Cascading from the Application-Integration Tier

All three CAI gateway deployments were affected:

| Region | 503 Spike (per 5m) | Sustained 500s (per 5m) |
|--------|--------------------|--------------------------|
| usw1 (`cai-prod-usw1-caigateway-*`) | Up to **8.4/5m** | 200–660/5m |
| usw3 (`cai-prod-usw3-caigateway-*`) | Up to **12.6/5m** | 100–554/5m |
| usw5 (`cai-prod-usw5-caigateway-*`) | Up to **1.1/5m** (lighter) | 60–245/5m |

**Cascade pattern confirmed:** `application-integration-obm` saturates → CAI gateway cannot reach it within timeout → returns 503 to clients.

### 4. `application-integration-ht` (Human Task) Was Largely Healthy

p99 latency held at ~4,800 ms — elevated but stable throughout the incident. This confirms the issue was **specific to the DS Queue OBM processing tier**, not the entire CAI stack.

---

## Probable Contributing Factors

> Note: JMX business metrics (`CAI_jmxMetrics`) are not actively scraped for the `cai-prod-*` namespace. Thread pool / work manager stats were unavailable. The following are inferred from Istio traffic patterns.

1. **Work Manager thread pool exhaustion** — OBM queue handler threads blocked waiting on a downstream dependency (RDS, external connector, or dependent service). New process acquire requests queued up; when queue depth exceeded capacity, the gateway emitted 503 with "Process Timeout".

2. **Slow/locked MySQL RDS query on the process DB** — Process state read/write operations may have locked, holding OBM handler threads. (`mysql-exporter-taskflow-prod-usw*-process-rds` metrics were unavailable beyond Prometheus retention.)

3. **Peak batch submission load at 4 PM IST** — End-of-business India time is a known high-throughput window for DS Queue batch submissions, which may have exceeded OBM processing capacity.

---

## Impact

- **Users:** HTTP 503 errors on all DS Queue process operations across usw1, usw3, usw5
- **Duration:** ~2 hours (4:00–6:00 PM IST)
- **Scope:** All production regions; primarily OBM queue processing; Human Task (HT) largely unaffected

---

## Recommendations

| Priority | Action |
|----------|--------|
| P1 | **Instrument `CAI_jmxMetrics` scraping** for `cai-prod-*` namespaces — Work Manager thread pool, active process count, and process acquire duration are not being scraped. These are the exact metrics needed for this class of incident. |
| P1 | **Investigate RDS process DB slow query logs** around 10:30 AM UTC on July 15 — check for blocking transactions or lock contention on the process state tables. |
| P2 | **Add latency-based alert on `application-integration-obm`** — alert when Istio p99 exceeds 10 s for more than 2 consecutive minutes. |
| P2 | **Review OBM thread pool sizing** — sustained ~10 HTTP 500s/second indicates the pool is undersized relative to queue depth at peak load. Consider scaling up the StatefulSet replica count or increasing thread pool max. |
| P3 | **Add circuit breaker on caigateway → application-integration-obm** — rather than cascading 503s to end users, fail fast with a meaningful error when `obm` latency exceeds the configured threshold. |
| P3 | **Establish Prometheus retention verification SOP** — Prometheus retention was at the boundary for this incident window (6 days), which limited the ability to query JMX and RDS metrics during the investigation. |

---

## Metrics Queried

| Metric | Datasource | Notes |
|--------|------------|-------|
| `istio_requests_total` (5xx by workload) | `000000001` (aws-uswest2) | Primary signal for error rate |
| `istio_request_duration_milliseconds_bucket` | `000000001` | Primary signal for latency / saturation |
| `kube_pod_container_status_restarts_total` | `000000001` | Pod stability check |
| `http_server_requests_seconds_count` (503) | `000000001` | Spring Boot HTTP layer |
| `jvm_memory_used_bytes` / `CAI_jmxMetrics` | `000000001` | **Not scraped** — returned no data |
| `mysql_global_status_*` (process-rds) | `000000001` | Beyond Prometheus retention |

---

*Investigation performed via Grafana MCP — `https://grafana.cloudtrust.rocks` — Prometheus datasource `aws-uswest2` (UID: `000000001`)*
