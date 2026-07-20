"""
Composite scenario tools — each investigates a specific symptom by running
multiple PromQL queries in parallel and returning one consolidated report
in the six-banner format:

    ## * HEADER        identity: scenario, namespace, datasource, range, timestamp
    ## * TIMELINE      trend: escalating / stable / recovering with primary metric
    ## * INFRASTRUCTURE  pod/k8s health: restarts, crashes, OOM, ready status
    ## * SERVICE       HTTP layer: error rate, latency, request rate
    ## * METRICS       resource usage: CPU, memory, GC, JVM, threads
    ## * ANOMALIES     all detected threshold/spike breaches
    ## FINDINGS        numbered [CRITICAL] / [WARNING] / [INFO] conclusions

Every banner is always present. Banners with no applicable data for a given
scenario contain an N/A line explaining why, so consuming LLMs always know
exactly where to look.
"""
import asyncio
import time
from typing import List, Optional, Tuple

from grafana_mcp.grafana.api import query_metrics
from grafana_mcp.parser.metrics import (
    Anomaly, parse_query_result, detect_anomalies, format_metrics_table,
)


# ── query helpers ─────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _run(datasource_uid: str, ref_id: str, expr: str, range_minutes: int) -> dict:
    to_ms = _now_ms()
    from_ms = to_ms - range_minutes * 60 * 1000
    return await query_metrics(
        datasource_uid, "prometheus",
        [{"refId": ref_id, "expr": expr, "intervalMs": 60000, "maxDataPoints": 300}],
        from_ms, to_ms,
    )


def _lbl(*parts: str) -> str:
    joined = ", ".join(p for p in parts if p)
    return "{" + joined + "}" if joined else ""


def _ns(namespace: Optional[str]) -> str:
    return 'namespace=~"' + namespace + '"' if namespace else ""


# ── banner building helpers ───────────────────────────────────────────────────

def _metric_block(title: str, result: dict, metric_type: str = "auto") -> Tuple[str, List[Anomaly]]:
    """
    Parse a query result into a formatted block for a banner section.
    Returns (formatted_string, list_of_anomalies).
    """
    parsed = parse_query_result(result)
    report = detect_anomalies(parsed, metric_type)
    table  = format_metrics_table(parsed)

    if report.has_anomalies:
        worst   = max(report.anomalies, key=lambda a: 0 if a.severity == "warning" else 1)
        status  = "[" + worst.severity.upper() + "]"
    else:
        status  = "[OK]"

    block = "  " + title.ljust(50) + status + "\n" + _indent(table, 4)
    return block, report.anomalies


def _trend_line(title: str, result: dict) -> str:
    """Compute ESCALATING / STABLE / RECOVERING from current vs avg across all series."""
    parsed = parse_query_result(result)
    if not parsed or not parsed[0].series:
        return "TREND: UNKNOWN -- no data returned"

    all_series = [s for m in parsed for s in m.series]
    avg_current = sum(s.current for s in all_series) / len(all_series)
    avg_mean    = sum(s.avg    for s in all_series) / len(all_series)

    if avg_mean == 0:
        direction = "STABLE"
        ratio_str = ""
    elif avg_current > avg_mean * 1.5:
        ratio = avg_current / avg_mean
        direction = "ESCALATING"
        ratio_str = " -- {:.1f}x above average ({:.3f} vs avg {:.3f})".format(ratio, avg_current, avg_mean)
    elif avg_current < avg_mean * 0.7:
        direction = "RECOVERING"
        ratio_str = " -- current {:.3f} below average {:.3f}".format(avg_current, avg_mean)
    else:
        direction = "STABLE"
        ratio_str = " -- current {:.3f} near average {:.3f}".format(avg_current, avg_mean)

    lines = ["TREND: " + direction + ratio_str, "", "  Primary metric: " + title]
    lines.append(_indent(format_metrics_table(parsed), 4))
    return "\n".join(lines)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


def _na(reason: str) -> str:
    return "N/A -- " + reason


def _auto_findings(all_anomalies: List[Anomaly]) -> str:
    if not all_anomalies:
        return "1. [INFO] All metrics within normal thresholds. No anomalies detected."

    findings = []
    idx = 1
    criticals = [a for a in all_anomalies if a.severity == "critical"]
    warnings  = [a for a in all_anomalies if a.severity == "warning"]

    for a in criticals:
        label_str = ", ".join(k + "=" + v for k, v in a.labels.items()) if a.labels else "all pods"
        findings.append(str(idx) + ". [CRITICAL] " + a.message + " (" + label_str + ")")
        idx += 1

    for a in warnings:
        label_str = ", ".join(k + "=" + v for k, v in a.labels.items()) if a.labels else "all pods"
        findings.append(str(idx) + ". [WARNING] " + a.message + " (" + label_str + ")")
        idx += 1

    total = len(criticals) + len(warnings)
    findings.append(
        str(idx) + ". [INFO] " + str(total) + " anomal" + ("y" if total == 1 else "ies") +
        " detected: " + str(len(criticals)) + " critical, " + str(len(warnings)) + " warning."
    )
    return "\n".join(findings)


def _six_banner_report(
    scenario:       str,
    namespace:      Optional[str],
    datasource_uid: str,
    range_minutes:  int,
    generated:      str,
    timeline:       str,
    infrastructure: str,
    service:        str,
    metrics:        str,
    all_anomalies:  List[Anomaly],
) -> str:
    anomaly_block = ""
    if all_anomalies:
        for i, a in enumerate(all_anomalies, 1):
            label_str = ", ".join(k + "=" + v for k, v in a.labels.items()) if a.labels else "all pods"
            anomaly_block += (
                "  " + str(i) + ". [" + a.severity.upper() + "] " + a.message + "\n"
                + "     Labels: " + label_str + "\n"
            )
    else:
        anomaly_block = "  None detected.\n"

    findings = _auto_findings(all_anomalies)

    sep = "\n---\n"
    return (
        "# Grafana Analysis: " + scenario + sep
        + "## * HEADER\n\n"
        + "```\n"
        + "Scenario:        " + scenario + "\n"
        + "Namespace:       " + (namespace or "all") + "\n"
        + "Datasource:      " + datasource_uid + "\n"
        + "Range:           last " + str(range_minutes) + " minutes\n"
        + "Generated:       " + generated + "\n"
        + "```\n"
        + sep
        + "## * TIMELINE\n\n"
        + "```\n" + timeline + "\n```\n"
        + sep
        + "## * INFRASTRUCTURE\n\n"
        + "```\n" + infrastructure + "\n```\n"
        + sep
        + "## * SERVICE\n\n"
        + "```\n" + service + "\n```\n"
        + sep
        + "## * METRICS\n\n"
        + "```\n" + metrics + "\n```\n"
        + sep
        + "## * ANOMALIES\n\n"
        + "```\n" + anomaly_block + "```\n"
        + sep
        + "## FINDINGS\n\n"
        + findings + "\n"
    )


# ── scenario 1: latency spike ─────────────────────────────────────────────────

async def investigate_latency_spike(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
    service_filter: str = "",
) -> str:
    ns    = _ns(namespace)
    lbl   = _lbl(ns, service_filter)
    lbl_c = _lbl(ns, service_filter, 'container!="POD"', 'container!=""')

    gc_expr     = "sum by (pod) (rate(jvm_gc_pause_seconds_sum" + lbl + "[5m])) * 1000"
    thr_expr    = ("sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total" + lbl_c + "[5m]))"
                   " / sum by (pod) (rate(container_cpu_cfs_periods_total" + lbl_c + "[5m]))")
    thread_expr = "jvm_threads_live_threads" + lbl
    lat_expr    = ("histogram_quantile(0.99, sum by (pod, le)"
                   " (rate(http_server_requests_seconds_bucket" + lbl + "[5m]))) * 1000")
    rps_expr    = "sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"

    r_gc, r_thr, r_threads, r_lat, r_rps = await asyncio.gather(
        _run(datasource_uid, "GC",      gc_expr,     range_minutes),
        _run(datasource_uid, "CPUTHR",  thr_expr,    range_minutes),
        _run(datasource_uid, "THREADS", thread_expr, range_minutes),
        _run(datasource_uid, "LAT",     lat_expr,    range_minutes),
        _run(datasource_uid, "RPS",     rps_expr,    range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    # TIMELINE — primary: latency
    timeline = _safe_trend("HTTP p99 latency (ms)", r_lat)

    # INFRASTRUCTURE — N/A for this scenario
    infrastructure = _na(
        "Latency spike investigation focuses on JVM and HTTP metrics.\n"
        "       Use investigate_pod_instability for restart/crash analysis."
    )

    # SERVICE — latency + RPS
    svc_lines = []
    for title, res, mtype in [
        ("HTTP p99 latency (ms)",     r_lat, "response_time"),
        ("HTTP request rate (req/s)", r_rps, "auto"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        svc_lines.append(block)
        all_anomalies.extend(anomalies)
    service = "\n\n".join(svc_lines)

    # METRICS — GC + CPU throttle + threads
    met_lines = []
    for title, res, mtype in [
        ("GC pause rate (ms/s)",      r_gc,      "auto"),
        ("CPU throttle ratio (0-1)",  r_thr,     "cpu"),
        ("JVM live thread count",     r_threads, "threads"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        met_lines.append(block)
        all_anomalies.extend(anomalies)
    metrics = "\n\n".join(met_lines)

    return _six_banner_report(
        "Latency Spike Investigation", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 2: memory pressure ───────────────────────────────────────────────

async def investigate_memory_pressure(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns    = _ns(namespace)
    lbl_c = _lbl(ns, 'container!="POD"', 'container!=""')
    lbl   = _lbl(ns)
    oom   = 'reason="OOMKilled"'
    mem_r = 'resource="memory"'

    heap_expr  = ('sum by (pod) (jvm_memory_used_bytes{area="heap"})'
                  ' / sum by (pod) (jvm_memory_max_bytes{area="heap"}) * 100')
    ws_expr    = "sum by (pod) (container_memory_working_set_bytes" + lbl_c + ") / 1024 / 1024"
    oom_expr   = "kube_pod_container_status_last_terminated_reason" + _lbl(ns, oom)
    gc_expr    = "sum by (pod) (rate(jvm_gc_pause_seconds_count" + lbl + "[5m]))"
    limit_expr = ("sum by (pod) (container_memory_working_set_bytes" + lbl_c + ")"
                  " / sum by (pod) (kube_pod_container_resource_limits" + _lbl(ns, mem_r) + ")")

    r_heap, r_ws, r_oom, r_gc, r_limit = await asyncio.gather(
        _run(datasource_uid, "HEAP",  heap_expr,  range_minutes),
        _run(datasource_uid, "WS",    ws_expr,    range_minutes),
        _run(datasource_uid, "OOM",   oom_expr,   range_minutes),
        _run(datasource_uid, "GC",    gc_expr,    range_minutes),
        _run(datasource_uid, "LIMIT", limit_expr, range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    timeline = _safe_trend("Working set memory (MB)", r_ws)

    # INFRASTRUCTURE — OOM kills
    infra_lines = []
    block, anomalies = _safe_metric_block("OOM kill events", r_oom, "auto")
    infra_lines.append(block)
    all_anomalies.extend(anomalies)
    infrastructure = "\n\n".join(infra_lines)

    service = _na(
        "Memory pressure investigation focuses on JVM and container resource metrics.\n"
        "       Use investigate_error_spike for HTTP-layer analysis."
    )

    met_lines = []
    for title, res, mtype in [
        ("JVM heap usage (%)",        r_heap,  "memory"),
        ("Working set memory (MB)",   r_ws,    "memory"),
        ("GC collections per second", r_gc,    "auto"),
        ("Memory % of limit",         r_limit, "memory"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        met_lines.append(block)
        all_anomalies.extend(anomalies)
    metrics = "\n\n".join(met_lines)

    return _six_banner_report(
        "Memory Pressure Investigation", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 3: pod instability ───────────────────────────────────────────────

async def investigate_pod_instability(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns      = _ns(namespace)
    lbl     = _lbl(ns)
    crash   = 'reason="CrashLoopBackOff"'
    oom     = 'reason="OOMKilled"'
    not_run = 'phase!="Running"'
    not_suc = 'phase!="Succeeded"'

    restart_expr  = "increase(kube_pod_container_status_restarts_total" + lbl + "[" + str(range_minutes) + "m])"
    crash_expr    = "kube_pod_container_status_waiting_reason" + _lbl(ns, crash)
    notready_expr = "kube_pod_container_status_ready" + lbl + " == 0"
    oom_expr      = "kube_pod_container_status_last_terminated_reason" + _lbl(ns, oom)
    phase_expr    = "kube_pod_status_phase" + _lbl(ns, not_run, not_suc)

    r_restart, r_crash, r_notready, r_oom, r_phase = await asyncio.gather(
        _run(datasource_uid, "RESTART",  restart_expr,  range_minutes),
        _run(datasource_uid, "CRASH",    crash_expr,    range_minutes),
        _run(datasource_uid, "NOTREADY", notready_expr, range_minutes),
        _run(datasource_uid, "OOM",      oom_expr,      range_minutes),
        _run(datasource_uid, "PHASE",    phase_expr,    range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    timeline = _safe_trend("Container restarts", r_restart)

    infra_lines = []
    for title, res, mtype in [
        ("Container restarts",      r_restart,  "auto"),
        ("CrashLoopBackOff pods",   r_crash,    "auto"),
        ("Not-ready containers",    r_notready, "auto"),
        ("OOM-killed containers",   r_oom,      "auto"),
        ("Non-running pod phases",  r_phase,    "auto"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        infra_lines.append(block)
        all_anomalies.extend(anomalies)
    infrastructure = "\n\n".join(infra_lines)

    service = _na(
        "Pod instability investigation focuses on k8s pod lifecycle metrics.\n"
        "       Use investigate_error_spike for HTTP-layer analysis."
    )
    metrics = _na(
        "Pod instability investigation focuses on k8s pod lifecycle metrics.\n"
        "       Use investigate_cpu_spike or investigate_memory_pressure for resource analysis."
    )

    return _six_banner_report(
        "Pod Instability Investigation", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 4: error spike ───────────────────────────────────────────────────

async def investigate_error_spike(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns    = _ns(namespace)
    lbl   = _lbl(ns)
    lbl_c = _lbl(ns, 'container!="POD"')
    err_l = _lbl(ns, 'status=~"4..|5.."')

    err_rate_expr = ("sum by (pod) (rate(http_server_requests_seconds_count" + err_l + "[5m]))"
                     " / sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m])) * 100")
    lat_expr      = ("histogram_quantile(0.99, sum by (pod, le)"
                     " (rate(http_server_requests_seconds_bucket" + lbl + "[5m]))) * 1000")
    restart_expr  = "increase(kube_pod_container_status_restarts_total" + lbl + "[" + str(range_minutes) + "m])"
    thr_expr      = ("sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total" + lbl_c + "[5m]))"
                     " / sum by (pod) (rate(container_cpu_cfs_periods_total" + lbl_c + "[5m]))")
    rps_expr      = "sum by (pod, status) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"

    r_err, r_lat, r_restart, r_thr, r_rps = await asyncio.gather(
        _run(datasource_uid, "ERRRATE", err_rate_expr, range_minutes),
        _run(datasource_uid, "LAT",     lat_expr,      range_minutes),
        _run(datasource_uid, "RESTART", restart_expr,  range_minutes),
        _run(datasource_uid, "CPUTHR",  thr_expr,      range_minutes),
        _run(datasource_uid, "RPS",     rps_expr,      range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    timeline = _safe_trend("HTTP error rate (%)", r_err)

    infra_lines = []
    block, anomalies = _safe_metric_block("Container restarts", r_restart, "auto")
    infra_lines.append(block)
    all_anomalies.extend(anomalies)
    infrastructure = "\n\n".join(infra_lines)

    svc_lines = []
    for title, res, mtype in [
        ("HTTP error rate (%)",       r_err, "error_rate"),
        ("HTTP p99 latency (ms)",     r_lat, "response_time"),
        ("Request rate by status",    r_rps, "auto"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        svc_lines.append(block)
        all_anomalies.extend(anomalies)
    service = "\n\n".join(svc_lines)

    met_lines = []
    block, anomalies = _safe_metric_block("CPU throttle ratio (0-1)", r_thr, "cpu")
    met_lines.append(block)
    all_anomalies.extend(anomalies)
    metrics = "\n\n".join(met_lines)

    return _six_banner_report(
        "Error Spike Investigation", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 5: cpu spike ─────────────────────────────────────────────────────

async def investigate_cpu_spike(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns    = _ns(namespace)
    lbl_c = _lbl(ns, 'container!="POD"', 'container!=""')
    lbl   = _lbl(ns)

    cpu_expr    = "sum by (pod) (rate(container_cpu_usage_seconds_total" + lbl_c + "[5m])) * 100"
    thr_expr    = ("sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total" + lbl_c + "[5m]))"
                   " / sum by (pod) (rate(container_cpu_cfs_periods_total" + lbl_c + "[5m]))")
    gc_expr     = "sum by (pod) (rate(jvm_gc_pause_seconds_sum" + lbl + "[5m])) * 1000"
    thread_expr = "jvm_threads_live_threads" + lbl
    rps_expr    = "sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"

    r_cpu, r_thr, r_gc, r_threads, r_rps = await asyncio.gather(
        _run(datasource_uid, "CPU",     cpu_expr,    range_minutes),
        _run(datasource_uid, "THR",     thr_expr,    range_minutes),
        _run(datasource_uid, "GC",      gc_expr,     range_minutes),
        _run(datasource_uid, "THREADS", thread_expr, range_minutes),
        _run(datasource_uid, "RPS",     rps_expr,    range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    timeline = _safe_trend("CPU usage (% of cores)", r_cpu)

    infrastructure = _na(
        "CPU spike investigation focuses on resource metrics.\n"
        "       Use investigate_pod_instability for restart/crash analysis."
    )

    svc_lines = []
    block, anomalies = _safe_metric_block("HTTP request rate (req/s)", r_rps, "auto")
    svc_lines.append(block)
    all_anomalies.extend(anomalies)
    service = "\n\n".join(svc_lines)

    met_lines = []
    for title, res, mtype in [
        ("CPU usage (% of cores)",    r_cpu,     "cpu"),
        ("CPU throttle ratio (0-1)",  r_thr,     "cpu"),
        ("GC pause rate (ms/s)",      r_gc,      "auto"),
        ("JVM live thread count",     r_threads, "threads"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        met_lines.append(block)
        all_anomalies.extend(anomalies)
    metrics = "\n\n".join(met_lines)

    return _six_banner_report(
        "CPU Spike Investigation", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 6: traffic drop ──────────────────────────────────────────────────

async def investigate_traffic_drop(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns          = _ns(namespace)
    lbl         = _lbl(ns)
    sched_false = 'condition="false"'

    rps_expr     = "sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"
    ready_expr   = "kube_pod_container_status_ready" + lbl
    sched_expr   = "kube_pod_status_scheduled" + _lbl(ns, sched_false)
    net_err_expr = ("sum by (pod) (rate(container_network_receive_errors_total" + lbl + "[5m]))"
                    " + sum by (pod) (rate(container_network_transmit_errors_total" + lbl + "[5m]))")
    restart_expr = "increase(kube_pod_container_status_restarts_total" + lbl + "[" + str(range_minutes) + "m])"

    r_rps, r_ready, r_sched, r_neterr, r_restart = await asyncio.gather(
        _run(datasource_uid, "RPS",     rps_expr,     range_minutes),
        _run(datasource_uid, "READY",   ready_expr,   range_minutes),
        _run(datasource_uid, "SCHED",   sched_expr,   range_minutes),
        _run(datasource_uid, "NETERR",  net_err_expr, range_minutes),
        _run(datasource_uid, "RESTART", restart_expr, range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    timeline = _safe_trend("HTTP request rate (req/s)", r_rps)

    infra_lines = []
    for title, res, mtype in [
        ("Container ready status",  r_ready,   "auto"),
        ("Unscheduled pods",        r_sched,   "auto"),
        ("Container restarts",      r_restart, "auto"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        infra_lines.append(block)
        all_anomalies.extend(anomalies)
    infrastructure = "\n\n".join(infra_lines)

    svc_lines = []
    block, anomalies = _safe_metric_block("HTTP request rate (req/s)", r_rps, "auto")
    svc_lines.append(block)
    all_anomalies.extend(anomalies)
    service = "\n\n".join(svc_lines)

    met_lines = []
    block, anomalies = _safe_metric_block("Network errors (rx+tx/s)", r_neterr, "auto")
    met_lines.append(block)
    all_anomalies.extend(anomalies)
    metrics = "\n\n".join(met_lines)

    return _six_banner_report(
        "Traffic Drop Investigation", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 7: jvm health deep dive ─────────────────────────────────────────

async def investigate_jvm_health(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
    job: Optional[str] = None,
) -> str:
    ns       = _ns(namespace)
    job_flt  = ('job="' + job + '"') if job else ""
    lbl      = _lbl(ns, job_flt)
    heap_lbl = _lbl('area="heap"', job_flt)
    nohp_lbl = _lbl('area="nonheap"', job_flt)

    heap_expr    = ("sum by (pod) (jvm_memory_used_bytes" + heap_lbl + ")"
                    " / sum by (pod) (jvm_memory_max_bytes" + heap_lbl + ") * 100")
    nonheap_expr = "sum by (pod) (jvm_memory_used_bytes" + nohp_lbl + ") / 1024 / 1024"
    gc_time_expr = "sum by (pod) (rate(jvm_gc_pause_seconds_sum" + lbl + "[5m])) * 1000"
    gc_cnt_expr  = "sum by (pod, cause) (rate(jvm_gc_pause_seconds_count" + lbl + "[5m]))"
    thread_expr  = "jvm_threads_live_threads" + lbl
    tstate_expr  = "jvm_threads_states_threads" + lbl

    r_heap, r_nonheap, r_gctime, r_gccnt, r_threads, r_tstates = await asyncio.gather(
        _run(datasource_uid, "HEAP",    heap_expr,    range_minutes),
        _run(datasource_uid, "NONHEAP", nonheap_expr, range_minutes),
        _run(datasource_uid, "GCTIME",  gc_time_expr, range_minutes),
        _run(datasource_uid, "GCCNT",   gc_cnt_expr,  range_minutes),
        _run(datasource_uid, "THREADS", thread_expr,  range_minutes),
        _run(datasource_uid, "TSTATES", tstate_expr,  range_minutes),
        return_exceptions=True,
    )

    all_anomalies = []

    timeline = _safe_trend("JVM heap usage (%)", r_heap)

    infrastructure = _na(
        "JVM health investigation focuses on in-process JVM metrics.\n"
        "       Use investigate_pod_instability for pod lifecycle analysis."
    )
    service = _na(
        "JVM health investigation focuses on JVM internals.\n"
        "       Use investigate_error_spike or investigate_latency_spike for HTTP-layer analysis."
    )

    met_lines = []
    for title, res, mtype in [
        ("Heap usage (% of max)",     r_heap,    "memory"),
        ("Non-heap / metaspace (MB)", r_nonheap, "memory"),
        ("GC pause time (ms/s)",      r_gctime,  "auto"),
        ("GC collections per second", r_gccnt,   "auto"),
        ("Live thread count",         r_threads, "threads"),
        ("Thread states breakdown",   r_tstates, "auto"),
    ]:
        block, anomalies = _safe_metric_block(title, res, mtype)
        met_lines.append(block)
        all_anomalies.extend(anomalies)
    metrics = "\n\n".join(met_lines)

    return _six_banner_report(
        "JVM Health Deep Dive", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure, service, metrics, all_anomalies,
    )


# ── scenario 8: compare regions ───────────────────────────────────────────────

async def compare_regions(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
    regions: Optional[List[str]] = None,
) -> str:
    regions = regions or ["usw1", "usw3", "usw5"]
    base_ns = namespace or ".*"

    async def _region_data(region: str):
        ns_r  = 'namespace=~"' + base_ns + '"'
        pod_r = 'pod=~".*' + region + '.*"'
        lbl_c = _lbl(ns_r, pod_r, 'container!="POD"', 'container!=""')
        lbl   = _lbl(ns_r, pod_r)
        err_l = _lbl(ns_r, pod_r, 'status=~"4..|5.."')
        return await asyncio.gather(
            _run(datasource_uid, "CPU",
                 "sum by (pod) (rate(container_cpu_usage_seconds_total" + lbl_c + "[5m])) * 100",
                 range_minutes),
            _run(datasource_uid, "MEM",
                 "sum by (pod) (container_memory_working_set_bytes" + lbl_c + ") / 1024 / 1024",
                 range_minutes),
            _run(datasource_uid, "ERR",
                 ("sum by (pod) (rate(http_server_requests_seconds_count" + err_l + "[5m]))"
                  " / sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m])) * 100"),
                 range_minutes),
            _run(datasource_uid, "LAT",
                 ("histogram_quantile(0.99, sum by (pod, le)"
                  " (rate(http_server_requests_seconds_bucket" + lbl + "[5m]))) * 1000"),
                 range_minutes),
            return_exceptions=True,
        )

    region_results = await asyncio.gather(*[_region_data(r) for r in regions])

    all_anomalies = []
    svc_lines  = []
    met_lines  = []

    for region, (r_cpu, r_mem, r_err, r_lat) in zip(regions, region_results):
        svc_lines.append("  Region: " + region.upper())
        met_lines.append("  Region: " + region.upper())

        for title, res, mtype, banner in [
            ("Error rate (%)",   r_err, "error_rate",    "svc"),
            ("p99 latency (ms)", r_lat, "response_time", "svc"),
            ("CPU %",            r_cpu, "cpu",            "met"),
            ("Memory (MB)",      r_mem, "memory",         "met"),
        ]:
            block, anomalies = _safe_metric_block(title, res, mtype)
            all_anomalies.extend(anomalies)
            if banner == "svc":
                svc_lines.append(block)
            else:
                met_lines.append(block)

    # TIMELINE — identify outlier region by total anomaly count per region
    region_anomaly_counts = []
    offset = 0
    anomalies_per_region = []
    for region, (r_cpu, r_mem, r_err, r_lat) in zip(regions, region_results):
        count = 0
        for res, mtype in [(r_err, "error_rate"), (r_lat, "response_time"),
                           (r_cpu, "cpu"), (r_mem, "memory")]:
            if not isinstance(res, Exception):
                parsed = parse_query_result(res)
                count += len(detect_anomalies(parsed, mtype).anomalies)
        region_anomaly_counts.append((region, count))

    region_anomaly_counts.sort(key=lambda x: x[1], reverse=True)
    if region_anomaly_counts[0][1] > 0:
        outlier = region_anomaly_counts[0][0]
        timeline = ("TREND: OUTLIER DETECTED\n\n"
                    + "  Region " + outlier.upper() + " has the most anomalies ("
                    + str(region_anomaly_counts[0][1]) + ").\n"
                    + "  Anomalies by region: "
                    + ", ".join(r + "=" + str(c) for r, c in region_anomaly_counts))
    else:
        timeline = ("TREND: STABLE\n\n"
                    + "  All regions within normal thresholds.\n"
                    + "  Regions checked: " + ", ".join(r.upper() for r in regions))

    infrastructure = _na(
        "Region comparison focuses on service and resource metrics.\n"
        "       Use investigate_pod_instability scoped to a specific namespace for pod health."
    )

    return _six_banner_report(
        "Region Comparison", namespace, datasource_uid, range_minutes, _now_iso(),
        timeline, infrastructure,
        "\n\n".join(svc_lines),
        "\n\n".join(met_lines),
        all_anomalies,
    )


# ── safe wrappers (handle exceptions from _run) ───────────────────────────────

def _safe_metric_block(title: str, result, metric_type: str) -> Tuple[str, List[Anomaly]]:
    if isinstance(result, Exception):
        return "  " + title.ljust(50) + "[ERROR]\n    query failed: " + str(result), []
    return _metric_block(title, result, metric_type)


def _safe_trend(title: str, result) -> str:
    if isinstance(result, Exception):
        return "TREND: UNKNOWN -- query failed: " + str(result)
    return _trend_line(title, result)
