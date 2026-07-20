"""
Composite scenario tools — each investigates a specific symptom by running
multiple PromQL queries in parallel and returning one consolidated report.

All public functions share the same signature:
    async def investigate_*(datasource_uid, namespace, range_minutes, ...) -> str
"""
import asyncio
import time
from typing import List, Optional

from grafana_mcp.grafana.api import query_metrics
from grafana_mcp.parser.metrics import parse_query_result, detect_anomalies, format_metrics_table


# ── internal helpers ──────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


async def _run(datasource_uid: str, ref_id: str, expr: str, range_minutes: int) -> dict:
    to_ms = _now_ms()
    from_ms = to_ms - range_minutes * 60 * 1000
    return await query_metrics(
        datasource_uid, "prometheus",
        [{"refId": ref_id, "expr": expr, "intervalMs": 60000, "maxDataPoints": 300}],
        from_ms, to_ms,
    )


def _lbl(*parts: str) -> str:
    """Build a PromQL label selector from non-empty parts."""
    joined = ", ".join(p for p in parts if p)
    return "{" + joined + "}" if joined else ""


def _ns(namespace: Optional[str]) -> str:
    return 'namespace=~"' + namespace + '"' if namespace else ""


def _format_section(title: str, result: dict, metric_type: str = "auto") -> str:
    parsed = parse_query_result(result)
    report = detect_anomalies(parsed, metric_type)
    table = format_metrics_table(parsed)
    anomaly_lines = ""
    if report.has_anomalies:
        anomaly_lines = "\n" + "\n".join(
            "    [" + a.severity.upper() + "] " + a.message for a in report.anomalies
        )
    return "  " + title + "\n  Status: " + report.summary + anomaly_lines + "\n" + _indent(table, 4)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


def _report(title: str, namespace: Optional[str], range_minutes: int, sections: List[str]) -> str:
    header = (
        title + "\n"
        + "Namespace: " + (namespace or "all") + "\n"
        + "Range: last " + str(range_minutes) + " minutes\n\n"
    )
    return header + "\n\n".join(sections)


# ── scenario 1: latency spike ─────────────────────────────────────────────────

async def investigate_latency_spike(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
    service_filter: str = "",
) -> str:
    ns = _ns(namespace)
    lbl    = _lbl(ns, service_filter)
    lbl_c  = _lbl(ns, service_filter, 'container!="POD"', 'container!=""')
    no_pod = 'container!="POD"'
    err_lbl = _lbl(ns, service_filter, 'status=~"4..|5.."')

    gc_expr      = "sum by (pod) (rate(jvm_gc_pause_seconds_sum" + lbl + "[5m])) * 1000"
    thr_expr     = ("sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total" + lbl_c + "[5m]))"
                    " / sum by (pod) (rate(container_cpu_cfs_periods_total" + lbl_c + "[5m]))")
    thread_expr  = "jvm_threads_live_threads" + lbl
    lat_expr     = ("histogram_quantile(0.99, sum by (pod, le)"
                    " (rate(http_server_requests_seconds_bucket" + lbl + "[5m]))) * 1000")
    rps_expr     = "sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"

    results = await asyncio.gather(
        _run(datasource_uid, "GC",      gc_expr,     range_minutes),
        _run(datasource_uid, "CPUTHR",  thr_expr,    range_minutes),
        _run(datasource_uid, "THREADS", thread_expr, range_minutes),
        _run(datasource_uid, "LAT",     lat_expr,    range_minutes),
        _run(datasource_uid, "RPS",     rps_expr,    range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("GC pause rate (ms/s)",       results[0], "auto"),
        ("CPU throttle ratio (0-1)",   results[1], "cpu"),
        ("JVM live thread count",      results[2], "threads"),
        ("HTTP p99 latency (ms)",      results[3], "response_time"),
        ("HTTP request rate (req/s)",  results[4], "auto"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("Latency Spike Investigation", namespace, range_minutes, sections)


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

    results = await asyncio.gather(
        _run(datasource_uid, "HEAP",  heap_expr,  range_minutes),
        _run(datasource_uid, "WS",    ws_expr,    range_minutes),
        _run(datasource_uid, "OOM",   oom_expr,   range_minutes),
        _run(datasource_uid, "GC",    gc_expr,    range_minutes),
        _run(datasource_uid, "LIMIT", limit_expr, range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("JVM heap usage (%)",         results[0], "memory"),
        ("Working set memory (MB)",    results[1], "memory"),
        ("OOM kill events",            results[2], "auto"),
        ("GC collections per second",  results[3], "auto"),
        ("Memory % of limit",          results[4], "memory"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("Memory Pressure Investigation", namespace, range_minutes, sections)


# ── scenario 3: pod instability ───────────────────────────────────────────────

async def investigate_pod_instability(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns    = _ns(namespace)
    lbl   = _lbl(ns)
    crash = 'reason="CrashLoopBackOff"'
    oom   = 'reason="OOMKilled"'
    not_run = 'phase!="Running"'
    not_suc = 'phase!="Succeeded"'

    restart_expr  = "increase(kube_pod_container_status_restarts_total" + lbl + "[" + str(range_minutes) + "m])"
    crash_expr    = "kube_pod_container_status_waiting_reason" + _lbl(ns, crash)
    notready_expr = "kube_pod_container_status_ready" + lbl + " == 0"
    oom_expr      = "kube_pod_container_status_last_terminated_reason" + _lbl(ns, oom)
    phase_expr    = "kube_pod_status_phase" + _lbl(ns, not_run, not_suc)

    results = await asyncio.gather(
        _run(datasource_uid, "RESTART",  restart_expr,  range_minutes),
        _run(datasource_uid, "CRASH",    crash_expr,    range_minutes),
        _run(datasource_uid, "NOTREADY", notready_expr, range_minutes),
        _run(datasource_uid, "OOM",      oom_expr,      range_minutes),
        _run(datasource_uid, "PHASE",    phase_expr,    range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("Container restarts",       results[0], "auto"),
        ("CrashLoopBackOff pods",    results[1], "auto"),
        ("Not-ready containers",     results[2], "auto"),
        ("OOM-killed containers",    results[3], "auto"),
        ("Non-running pod phases",   results[4], "auto"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("Pod Instability Investigation", namespace, range_minutes, sections)


# ── scenario 4: error spike ───────────────────────────────────────────────────

async def investigate_error_spike(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns     = _ns(namespace)
    lbl    = _lbl(ns)
    lbl_c  = _lbl(ns, 'container!="POD"')
    err_st = 'status=~"4..|5.."'

    err_lbl      = _lbl(ns, err_st)
    err_rate_expr = ("sum by (pod) (rate(http_server_requests_seconds_count" + err_lbl + "[5m]))"
                     " / sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m])) * 100")
    lat_expr      = ("histogram_quantile(0.99, sum by (pod, le)"
                     " (rate(http_server_requests_seconds_bucket" + lbl + "[5m]))) * 1000")
    restart_expr  = "increase(kube_pod_container_status_restarts_total" + lbl + "[" + str(range_minutes) + "m])"
    thr_expr      = ("sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total" + lbl_c + "[5m]))"
                     " / sum by (pod) (rate(container_cpu_cfs_periods_total" + lbl_c + "[5m]))")
    rps_expr      = "sum by (pod, status) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"

    results = await asyncio.gather(
        _run(datasource_uid, "ERRRATE", err_rate_expr, range_minutes),
        _run(datasource_uid, "LAT",     lat_expr,      range_minutes),
        _run(datasource_uid, "RESTART", restart_expr,  range_minutes),
        _run(datasource_uid, "CPUTHR",  thr_expr,      range_minutes),
        _run(datasource_uid, "RPS",     rps_expr,      range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("HTTP error rate (%)",       results[0], "error_rate"),
        ("HTTP p99 latency (ms)",     results[1], "response_time"),
        ("Container restarts",        results[2], "auto"),
        ("CPU throttle ratio",        results[3], "cpu"),
        ("Request rate by status",    results[4], "auto"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("Error Spike Investigation", namespace, range_minutes, sections)


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

    results = await asyncio.gather(
        _run(datasource_uid, "CPU",     cpu_expr,    range_minutes),
        _run(datasource_uid, "THR",     thr_expr,    range_minutes),
        _run(datasource_uid, "GC",      gc_expr,     range_minutes),
        _run(datasource_uid, "THREADS", thread_expr, range_minutes),
        _run(datasource_uid, "RPS",     rps_expr,    range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("CPU usage (% of cores)",     results[0], "cpu"),
        ("CPU throttle ratio (0-1)",   results[1], "cpu"),
        ("GC pause rate (ms/s)",       results[2], "auto"),
        ("JVM live thread count",      results[3], "threads"),
        ("HTTP request rate (req/s)",  results[4], "auto"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("CPU Spike Investigation", namespace, range_minutes, sections)


# ── scenario 6: traffic drop ──────────────────────────────────────────────────

async def investigate_traffic_drop(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
) -> str:
    ns   = _ns(namespace)
    lbl  = _lbl(ns)
    sched_false = 'condition="false"'

    rps_expr     = "sum by (pod) (rate(http_server_requests_seconds_count" + lbl + "[5m]))"
    ready_expr   = "kube_pod_container_status_ready" + lbl
    sched_expr   = "kube_pod_status_scheduled" + _lbl(ns, sched_false)
    net_err_expr = ("sum by (pod) (rate(container_network_receive_errors_total" + lbl + "[5m]))"
                    " + sum by (pod) (rate(container_network_transmit_errors_total" + lbl + "[5m]))")
    restart_expr = "increase(kube_pod_container_status_restarts_total" + lbl + "[" + str(range_minutes) + "m])"

    results = await asyncio.gather(
        _run(datasource_uid, "RPS",     rps_expr,     range_minutes),
        _run(datasource_uid, "READY",   ready_expr,   range_minutes),
        _run(datasource_uid, "SCHED",   sched_expr,   range_minutes),
        _run(datasource_uid, "NETERR",  net_err_expr, range_minutes),
        _run(datasource_uid, "RESTART", restart_expr, range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("HTTP request rate (req/s)",   results[0], "auto"),
        ("Container ready status",      results[1], "auto"),
        ("Unscheduled pods",            results[2], "auto"),
        ("Network errors (rx+tx/s)",    results[3], "auto"),
        ("Container restarts",          results[4], "auto"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("Traffic Drop Investigation", namespace, range_minutes, sections)


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

    results = await asyncio.gather(
        _run(datasource_uid, "HEAP",    heap_expr,    range_minutes),
        _run(datasource_uid, "NONHEAP", nonheap_expr, range_minutes),
        _run(datasource_uid, "GCTIME",  gc_time_expr, range_minutes),
        _run(datasource_uid, "GCCNT",   gc_cnt_expr,  range_minutes),
        _run(datasource_uid, "THREADS", thread_expr,  range_minutes),
        _run(datasource_uid, "TSTATES", tstate_expr,  range_minutes),
        return_exceptions=True,
    )

    sections = []
    for title, res, mtype in [
        ("Heap usage (% of max)",      results[0], "memory"),
        ("Non-heap / metaspace (MB)",  results[1], "memory"),
        ("GC pause time (ms/s)",       results[2], "auto"),
        ("GC collections per second",  results[3], "auto"),
        ("Live thread count",          results[4], "threads"),
        ("Thread states breakdown",    results[5], "auto"),
    ]:
        if isinstance(res, Exception):
            sections.append("  " + title + "\n    query failed: " + str(res))
        else:
            sections.append(_format_section(title, res, mtype))

    return _report("JVM Health Deep Dive", namespace, range_minutes, sections)


# ── scenario 8: compare regions ───────────────────────────────────────────────

async def compare_regions(
    datasource_uid: str,
    namespace: Optional[str],
    range_minutes: int,
    regions: Optional[List[str]] = None,
) -> str:
    regions = regions or ["usw1", "usw3", "usw5"]
    base_ns = namespace or ".*"

    async def _region_summary(region: str) -> str:
        ns_r  = 'namespace=~"' + base_ns + '"'
        pod_r = 'pod=~".*' + region + '.*"'
        lbl_c = _lbl(ns_r, pod_r, 'container!="POD"', 'container!=""')
        lbl   = _lbl(ns_r, pod_r)
        err_l = _lbl(ns_r, pod_r, 'status=~"4..|5.."')

        r_cpu, r_mem, r_err, r_lat = await asyncio.gather(
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

        lines = ["\n  Region: " + region.upper()]
        for title, res, mtype in [
            ("CPU %",            r_cpu, "cpu"),
            ("Memory (MB)",      r_mem, "memory"),
            ("Error rate (%)",   r_err, "error_rate"),
            ("p99 latency (ms)", r_lat, "response_time"),
        ]:
            if isinstance(res, Exception):
                lines.append("    " + title + ": query failed: " + str(res))
            else:
                parsed = parse_query_result(res)
                report = detect_anomalies(parsed, mtype)
                flag = " [ANOMALY]" if report.has_anomalies else " [OK]"
                lines.append("    " + title + flag)
                lines.append(_indent(format_metrics_table(parsed), 6))
        return "\n".join(lines)

    region_reports = await asyncio.gather(*[_region_summary(r) for r in regions])

    header = (
        "Region Comparison\n"
        + "Namespace filter: " + (namespace or "all") + "\n"
        + "Range: last " + str(range_minutes) + " minutes\n"
    )
    return header + "".join(region_reports)
