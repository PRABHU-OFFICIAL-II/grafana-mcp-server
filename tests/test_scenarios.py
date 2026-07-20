"""Tests for the 8 scenario/investigation tools in grafana_mcp.tools.scenarios."""
import pytest
import grafana_mcp.tools.scenarios as _sc

# ── shared fake query result ──────────────────────────────────────────────────

EMPTY_RESULT = {"results": {}}

def _make_result(ref_id: str, current: float = 0.5, labels: dict = None):
    return {
        "results": {
            ref_id: {
                "frames": [{
                    "schema": {"fields": [
                        {"name": "Time", "type": "time"},
                        {"name": "Value", "type": "number", "labels": labels or {}},
                    ]},
                    "data": {"values": [[1000, 2000, 3000], [current * 0.8, current * 0.9, current]]},
                }]
            }
        }
    }


def patch_run(monkeypatch, return_value=None):
    """Replace _run so no real HTTP calls happen. Returns empty result by default."""
    async def mock_run(datasource_uid, ref_id, expr, range_minutes):
        return return_value if return_value is not None else EMPTY_RESULT
    monkeypatch.setattr(_sc, "_run", mock_run)


# ── investigate_latency_spike ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_latency_spike_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_latency_spike("ds1", ".*taskflow.*", 60)
    assert "Latency Spike Investigation" in report
    assert "GC pause rate" in report
    assert "CPU throttle ratio" in report
    assert "HTTP p99 latency" in report
    assert "JVM live thread count" in report
    assert "HTTP request rate" in report


@pytest.mark.asyncio
async def test_investigate_latency_spike_with_service_filter(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_latency_spike("ds1", "my-ns", 30, service_filter='job="api"')
    assert "Latency Spike Investigation" in report
    assert "my-ns" in report
    assert "last 30 minutes" in report


@pytest.mark.asyncio
async def test_investigate_latency_spike_handles_query_errors(monkeypatch):
    async def failing_run(datasource_uid, ref_id, expr, range_minutes):
        raise RuntimeError("upstream timeout")
    monkeypatch.setattr(_sc, "_run", failing_run)
    report = await _sc.investigate_latency_spike("ds1", None, 60)
    assert "query failed" in report


# ── investigate_memory_pressure ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_memory_pressure_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_memory_pressure("ds1", ".*taskflow.*", 60)
    assert "Memory Pressure Investigation" in report
    assert "JVM heap usage" in report
    assert "Working set memory" in report
    assert "OOM kill events" in report
    assert "GC collections" in report
    assert "Memory % of limit" in report


# ── investigate_pod_instability ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_pod_instability_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_pod_instability("ds1", "my-ns", 60)
    assert "Pod Instability Investigation" in report
    assert "Container restarts" in report
    assert "CrashLoopBackOff" in report
    assert "Not-ready containers" in report
    assert "OOM-killed" in report
    assert "Non-running pod phases" in report


# ── investigate_error_spike ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_error_spike_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_error_spike("ds1", ".*taskflow.*", 60)
    assert "Error Spike Investigation" in report
    assert "HTTP error rate" in report
    assert "HTTP p99 latency" in report
    assert "Container restarts" in report
    assert "CPU throttle ratio" in report
    assert "Request rate by status" in report


@pytest.mark.asyncio
async def test_investigate_error_spike_detects_anomaly(monkeypatch):
    """When error rate is above threshold the report should flag it."""
    async def mock_run(datasource_uid, ref_id, expr, range_minutes):
        if ref_id == "ERRRATE":
            # 8% error rate — above default 5% threshold
            return _make_result("ERRRATE", current=0.08)
        return EMPTY_RESULT
    monkeypatch.setattr(_sc, "_run", mock_run)
    report = await _sc.investigate_error_spike("ds1", None, 60)
    assert "Error Spike Investigation" in report
    # The anomaly should be reflected in the summary
    assert "WARNING" in report or "CRITICAL" in report or "error_rate" in report.lower()


# ── investigate_cpu_spike ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_cpu_spike_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_cpu_spike("ds1", ".*taskflow.*", 60)
    assert "CPU Spike Investigation" in report
    assert "CPU usage" in report
    assert "CPU throttle ratio" in report
    assert "GC pause rate" in report
    assert "JVM live thread count" in report
    assert "HTTP request rate" in report


@pytest.mark.asyncio
async def test_investigate_cpu_spike_detects_high_cpu(monkeypatch):
    async def mock_run(datasource_uid, ref_id, expr, range_minutes):
        if ref_id == "CPU":
            return _make_result("CPU", current=92.0)  # above 85% threshold
        return EMPTY_RESULT
    monkeypatch.setattr(_sc, "_run", mock_run)
    report = await _sc.investigate_cpu_spike("ds1", None, 60)
    assert "WARNING" in report or "CRITICAL" in report


# ── investigate_traffic_drop ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_traffic_drop_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_traffic_drop("ds1", "my-ns", 60)
    assert "Traffic Drop Investigation" in report
    assert "HTTP request rate" in report
    assert "Container ready status" in report
    assert "Unscheduled pods" in report
    assert "Network errors" in report
    assert "Container restarts" in report


# ── investigate_jvm_health ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investigate_jvm_health_returns_report(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_jvm_health("ds1", "my-ns", 60, job="CAI_jmxMetrics")
    assert "JVM Health Deep Dive" in report
    assert "Heap usage" in report
    assert "Non-heap" in report
    assert "GC pause time" in report
    assert "GC collections" in report
    assert "Live thread count" in report
    assert "Thread states" in report


@pytest.mark.asyncio
async def test_investigate_jvm_health_without_job(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.investigate_jvm_health("ds1", None, 60)
    assert "JVM Health Deep Dive" in report


# ── compare_regions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compare_regions_default_regions(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.compare_regions("ds1", ".*taskflow.*", 60)
    assert "Region Comparison" in report
    assert "USW1" in report
    assert "USW3" in report
    assert "USW5" in report


@pytest.mark.asyncio
async def test_compare_regions_custom_regions(monkeypatch):
    patch_run(monkeypatch)
    report = await _sc.compare_regions("ds1", None, 30, regions=["eu-west1", "ap-south1"])
    assert "EU-WEST1" in report
    assert "AP-SOUTH1" in report
    assert "USW1" not in report


@pytest.mark.asyncio
async def test_compare_regions_flags_anomaly(monkeypatch):
    async def mock_run(datasource_uid, ref_id, expr, range_minutes):
        if ref_id == "CPU":
            return _make_result("CPU", current=92.0)  # critical CPU
        return EMPTY_RESULT
    monkeypatch.setattr(_sc, "_run", mock_run)
    report = await _sc.compare_regions("ds1", None, 60, regions=["usw1"])
    assert "ANOMALY" in report


# ── helper unit tests ─────────────────────────────────────────────────────────

def test_lbl_no_parts():
    assert _sc._lbl() == ""


def test_lbl_single():
    assert _sc._lbl('namespace="prod"') == '{namespace="prod"}'


def test_lbl_multiple():
    result = _sc._lbl('namespace="prod"', 'pod=~".*api.*"')
    assert result == '{namespace="prod", pod=~".*api.*"}'


def test_lbl_filters_empty():
    result = _sc._lbl('namespace="prod"', "", 'job="api"')
    assert result == '{namespace="prod", job="api"}'


def test_ns_with_value():
    assert _sc._ns(".*taskflow.*") == 'namespace=~".*taskflow.*"'


def test_ns_none():
    assert _sc._ns(None) == ""
