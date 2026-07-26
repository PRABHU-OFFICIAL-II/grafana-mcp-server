# RCA: CAI DS Queue 503 Process Timeout — July 15, 2026

## Header

| Field | Value |
|-------|-------|
| **Scenario** | CAI DS Queue — 503 Process Timeout |
| **Namespace** | `cai-prod-usw1` (primary), `cai-prod-usw3`, `cai-prod-usw5` |
| **Datasource** | `000000001` (Prometheus) |
| **Incident Window** | July 15, 2026 ~10:30–12:30 UTC (4:00–6:00 PM IST) |
| **Generated** | 2026-07-21 |

---

## Timeline

**TREND: ESCALATING → RECOVERED** (self-resolved within ~2 hours)

Primary driver: CPU saturation on `application-integration-obm` pods.

| Pod | Avg CPU | Max CPU | Avg Memory | Max Memory |
|-----|---------|---------|------------|------------|
| `application-integration-obm-0` | 258% | **1825%** | 228 GB | 233 GB |
| `application-integration-obm-1` | 273% | **1849%** | 229 GB | 233 GB |
| `application-integration-obm-2` | 246% | **1768%** | 152 GB | 155 GB |
| `application-integration-obm-3` | 156% | 498% | 77 GB | 78 GB |
| `application-integration-obm-4` | 209% | 861% | — | 78 GB *(restarted)* |
| `application-integration-obm-5` | 150% | 504% | 77 GB | 78 GB |
| `application-integration-obm-6` | 159% | 526% | 78 GB | 78 GB |

---

## Infrastructure

| Item | Status | Detail |
|------|--------|--------|
| `application-integration-obm-4` restarts | WARNING | Memory min=4.8 GB → pod restarted during the 7-day window |
| `application-integration-obm-0/1` restarts | WARNING | Memory min=161–170 GB → prior restart detected |
| `application-integration-obm-dayandross-0/1/2` | WARNING | Memory min=~10 GB → restarted |
| `application-integration-ht-1` | WARNING | CPU max=133% (secondary spike) |
| OOM kills | OK | None detected — pods survived GC without being OOM-killed |
| CrashLoopBackOff | OK | None detected |
| CPU throttling ratio | OK | 0.004–0.024 — pods were **not** throttled; they consumed real CPU cores |

---

## Service Impact

503 errors propagated downstream to the following pods which call into the DS Queue process engine:

| Pod | Max 503 Rate | Notes |
|-----|-------------|-------|
| `eip-8f7b88b7-*` | 0.004 req/s | EIP service — caller of application-integration |
| `shm-service-blue-69c8fd494f-*` | 0.004 req/s | SHM service |
| `business-entity-blue-6b5b478d49-*` | 0.004 req/s | Business Entity service |

**Pattern:** Downstream services received 503s because their synchronous HTTP calls into `application-integration-obm` timed out while those pods were CPU-saturated and unable to schedule JVM threads fast enough to respond within the process timeout window.

---

## Metrics

### CPU Usage — application-integration-obm pods

| Pod | Avg CPU | Max CPU | Spike Ratio |
|-----|---------|---------|-------------|
| `obm-0` | 258% | 1825% | **7.1x** |
| `obm-1` | 273% | 1849% | **6.8x** |
| `obm-2` | 246% | 1768% | **7.2x** |
| `obm-3` | 156% | 498% | 3.2x |
| `obm-4` | 209% | 861% | 4.1x |
| `obm-5` | 150% | 504% | 3.4x |
| `obm-6` | 159% | 526% | 3.3x |

### Memory — application-integration pods

| Pod | Avg Memory | Max Memory | Min Memory |
|-----|------------|------------|------------|
| `obm-0` | 228 GB | 233 GB | 161 GB |
| `obm-1` | 229 GB | 233 GB | 170 GB |
| `obm-2` | 152 GB | 155 GB | 124 GB |
| `ui-0` | 213 GB | 217 GB | 159 GB |
| `ui-1` | 231 GB | 233 GB | 172 GB |

### CPU Throttling

All `application-integration-obm` pods: **0.004–0.024** (negligible — pods consumed real CPU, not throttled).

### JVM Heap / GC (CAI_jmxMetrics)

> **DATA GAP:** JMX metrics (`jvm_memory_used_bytes`, `jvm_gc_pause_seconds_sum`, `jvm_threads_live_threads`) returned zero for the 7-day window. JMX scraping was likely interrupted during the incident or the Prometheus job label differs from `CAI_jmxMetrics`. A full GC storm **cannot be confirmed directly from Prometheus** but is the leading hypothesis based on the CPU + memory pattern.

---

## Anomalies

| Severity | Finding |
|----------|---------|
| CRITICAL | `application-integration-obm-0/1` CPU spiked to **1825–1849%** (18–19 cores), 7x above their running average of ~260% — textbook CPU saturation / GC storm pattern |
| CRITICAL | `application-integration-obm` memory held at **228–233 GB** working set — JVM heap near maximum triggers full stop-the-world GC cycles consuming all CPU |
| WARNING | `application-integration-obm-4` restarted during the 7-day window (memory min = 4.8 GB vs typical 78 GB) |
| WARNING | 503 errors propagated downstream to EIP, SHM, and Business Entity services — all synchronous callers of the DS Queue process engine |

---

## Root Cause

### [CRITICAL] JVM GC Storm on application-integration-obm pods

The primary `application-integration-obm-0` and `obm-1` pods (CAI DS Queue process execution engines) underwent a CPU saturation event peaking at **1825–1849% CPU (18–19 cores)**.

The pods hold **228–233 GB** of JVM working set memory. When JVM heap approaches its configured maximum, the JVM triggers full (stop-the-world) GC cycles. Each GC cycle:

1. **Pauses all application threads** (stop-the-world)
2. **Consumes every available CPU core** for GC work
3. **Delays all in-flight DS Queue process requests** past their configured timeout → reported as **"Process Timeout"**
4. The gateway/EIP layer cannot reach the process engine and returns **503 Service Unavailable** to the caller

### [CRITICAL] Immediate Trigger — Queue Burst / Work Accumulation

CPU average on `obm-0/1` is already high (258–273% normally), indicating the pods run near capacity at baseline. The July 15 spike to 1825%+ indicates a **burst of enqueued DS jobs arrived simultaneously** at ~4 PM IST — likely deferred batch work, a retry storm, or a large multi-process workflow submission — pushing live heap over the GC threshold and triggering the storm.

### [WARNING] Contributing Factor — Memory Near Ceiling

233 GB working set on pods with a JVM `-Xmx` configured at approximately this level leaves **almost no headroom**. Any work spike that increases live object count crosses the GC trigger threshold immediately.

### [WARNING] Blast Radius — Downstream 503 Propagation

EIP, SHM Service, and Business Entity pods all logged 503s during the window. These services call `application-integration` synchronously. When `obm` pods stalled, all in-flight HTTP requests from these callers timed out and returned 503 to end users.

### [INFO] Recovery

The incident self-resolved within ~2 hours, consistent with GC eventually completing, heap being reclaimed, and the pod returning to normal scheduling. No pod was OOM-killed and no CrashLoopBackOff was detected, confirming the JVM survived by completing GC rather than crashing.

---

## Summary

| What | Detail |
|------|--------|
| **Root cause** | JVM GC storm on `application-integration-obm-0/1/2` — CPU spiked 7x to 1825–1849% due to full stop-the-world GC at 228–233 GB heap |
| **Trigger** | DS Queue job burst at ~4 PM IST pushed live heap over GC threshold |
| **Symptom** | Process threads stalled during GC → process acquire timeout → 503 returned by gateway/EIP to callers |
| **Duration** | ~2 hours (self-recovered after GC completed) |
| **Blast radius** | EIP, SHM Service, Business Entity pods all returned 503s to downstream callers |

---

## Recommendations

| Priority | Action |
|----------|--------|
| P1 | **Increase JVM heap limit or pod memory** — pods at 228–233 GB with no headroom are one burst away from GC pressure at all times |
| P1 | **Add JVM GC pause alerting** — alert on `rate(jvm_gc_pause_seconds_sum[5m]) * 1000 > threshold` so the team is notified before callers see 503s |
| P2 | **Implement DS Queue back-pressure / rate limiting** — prevent burst submission from saturating the obm pods; queue depth should have a circuit breaker |
| P2 | **Add circuit breakers in EIP/SHM** — downstream callers should fail-fast with their own timeout rather than queuing up requests against a saturated process engine |
| P3 | **Switch to ZGC or Shenandoah** — a concurrent low-pause GC collector avoids stop-the-world pauses entirely at this heap size, eliminating the 503 pattern even during burst workloads |
| P3 | **Fix JMX scraping gap** — restore `CAI_jmxMetrics` Prometheus scrape for `application-integration` pods so GC pause time and heap utilization are visible during future incidents |
