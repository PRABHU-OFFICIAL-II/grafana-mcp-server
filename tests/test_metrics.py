"""Tests for grafana_mcp.parser.metrics — parse, detect, format."""
import pytest
from grafana_mcp.parser.metrics import (
    parse_query_result, detect_anomalies, format_metrics_table,
    MetricSeries, ParsedMetric,
)


def _make_series(current, avg=None, max_val=None, min_val=None, labels=None):
    avg = avg if avg is not None else current
    max_val = max_val if max_val is not None else current
    min_val = min_val if min_val is not None else current
    return MetricSeries(
        labels=labels or {},
        points=[],
        current=current,
        avg=avg,
        max=max_val,
        min=min_val,
    )


def _make_metric(ref_id, series_list):
    return ParsedMetric(ref_id=ref_id, series=series_list)


# ── parse_query_result ────────────────────────────────────────────────────────

def test_parse_query_result_basic():
    raw = {
        "results": {
            "A": {
                "frames": [{
                    "schema": {"fields": [
                        {"name": "Time", "type": "time"},
                        {"name": "Value", "type": "number", "labels": {"job": "api"}},
                    ]},
                    "data": {"values": [[1000, 2000, 3000], [0.2, 0.5, 0.8]]},
                }]
            }
        }
    }
    result = parse_query_result(raw)
    assert len(result) == 1
    assert result[0].ref_id == "A"
    assert len(result[0].series) == 1
    s = result[0].series[0]
    assert s.current == pytest.approx(0.8)
    assert s.avg == pytest.approx(0.5)
    assert s.max == pytest.approx(0.8)
    assert s.min == pytest.approx(0.2)
    assert s.labels == {"job": "api"}


def test_parse_query_result_empty_frames():
    raw = {"results": {"A": {"frames": []}}}
    result = parse_query_result(raw)
    assert result == []


def test_parse_query_result_none_values():
    raw = {
        "results": {
            "A": {
                "frames": [{
                    "schema": {"fields": [
                        {"name": "Time", "type": "time"},
                        {"name": "Value", "type": "number", "labels": {}},
                    ]},
                    "data": {"values": [[1000, 2000], [None, 0.5]]},
                }]
            }
        }
    }
    result = parse_query_result(raw)
    s = result[0].series[0]
    assert s.current == pytest.approx(0.5)
    assert s.min == pytest.approx(0.5)  # None is filtered out, only 0.5 contributes


# ── detect_anomalies — cpu ────────────────────────────────────────────────────

def test_detect_anomalies_cpu_warning():
    metric = _make_metric("A", [_make_series(0.88)])  # 88%, above 85% threshold
    report = detect_anomalies([metric], "cpu")
    assert report.has_anomalies
    cpu_anomalies = [a for a in report.anomalies if a.type == "cpu"]
    assert len(cpu_anomalies) == 1
    assert cpu_anomalies[0].severity == "warning"


def test_detect_anomalies_cpu_critical():
    metric = _make_metric("A", [_make_series(0.97)])  # 97%, critical
    report = detect_anomalies([metric], "cpu")
    cpu_anomalies = [a for a in report.anomalies if a.type == "cpu"]
    assert cpu_anomalies[0].severity == "critical"


def test_detect_anomalies_cpu_ok():
    metric = _make_metric("A", [_make_series(0.5)])  # 50%, fine
    report = detect_anomalies([metric], "cpu")
    cpu_anomalies = [a for a in report.anomalies if a.type == "cpu"]
    assert len(cpu_anomalies) == 0


# ── detect_anomalies — memory ─────────────────────────────────────────────────

def test_detect_anomalies_memory_warning():
    metric = _make_metric("A", [_make_series(0.92)])  # 92%, above 90% threshold
    report = detect_anomalies([metric], "memory")
    memory_anomalies = [a for a in report.anomalies if a.type == "memory"]
    assert len(memory_anomalies) == 1
    assert memory_anomalies[0].severity == "warning"


# ── detect_anomalies — error_rate ─────────────────────────────────────────────

def test_detect_anomalies_error_rate_warning():
    metric = _make_metric("A", [_make_series(0.08)])  # 8%, above 5% threshold
    report = detect_anomalies([metric], "error_rate")
    err_anomalies = [a for a in report.anomalies if a.type == "error_rate"]
    assert len(err_anomalies) == 1
    assert err_anomalies[0].severity == "warning"
    assert "8.00%" in err_anomalies[0].message


def test_detect_anomalies_error_rate_critical():
    metric = _make_metric("A", [_make_series(0.15)])  # 15%, >= 2x threshold
    report = detect_anomalies([metric], "error_rate")
    err_anomalies = [a for a in report.anomalies if a.type == "error_rate"]
    assert err_anomalies[0].severity == "critical"


def test_detect_anomalies_error_rate_ok():
    metric = _make_metric("A", [_make_series(0.02)])  # 2%, fine
    report = detect_anomalies([metric], "error_rate")
    err_anomalies = [a for a in report.anomalies if a.type == "error_rate"]
    assert len(err_anomalies) == 0


def test_detect_anomalies_error_rate_auto_mode():
    metric = _make_metric("A", [_make_series(0.08)])
    report = detect_anomalies([metric], "auto")
    err_anomalies = [a for a in report.anomalies if a.type == "error_rate"]
    assert len(err_anomalies) == 1


# ── detect_anomalies — response_time ──────────────────────────────────────────

def test_detect_anomalies_response_time():
    metric = _make_metric("A", [_make_series(4000)])  # 4000ms, above 3000ms threshold
    report = detect_anomalies([metric], "response_time")
    rt_anomalies = [a for a in report.anomalies if a.type == "response_time"]
    assert len(rt_anomalies) == 1


# ── detect_anomalies — spike ──────────────────────────────────────────────────

def test_detect_anomalies_spike():
    series = _make_series(current=10.0, avg=2.0, max_val=10.5)
    metric = _make_metric("A", [series])
    report = detect_anomalies([metric], "auto")
    spike_anomalies = [a for a in report.anomalies if a.type == "spike"]
    assert len(spike_anomalies) == 1
    assert "5.0x" in spike_anomalies[0].message


def test_detect_anomalies_no_spike_at_normal_level():
    series = _make_series(current=3.0, avg=2.0, max_val=5.0)
    metric = _make_metric("A", [series])
    report = detect_anomalies([metric], "auto")
    spike_anomalies = [a for a in report.anomalies if a.type == "spike"]
    assert len(spike_anomalies) == 0


# ── detect_anomalies — no data ────────────────────────────────────────────────

def test_detect_anomalies_empty():
    report = detect_anomalies([], "auto")
    assert not report.has_anomalies
    assert report.anomalies == []


# ── format_metrics_table ──────────────────────────────────────────────────────

def test_format_metrics_table():
    series = _make_series(current=0.75, avg=0.5, max_val=0.9, min_val=0.1, labels={"job": "api"})
    metric = _make_metric("A", [series])
    table = format_metrics_table([metric])
    assert "current=0.750" in table
    assert "avg=0.500" in table
    assert 'job="api"' in table


def test_format_metrics_table_empty():
    table = format_metrics_table([])
    assert "(no data)" in table
