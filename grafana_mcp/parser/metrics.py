from dataclasses import dataclass, field
from typing import Dict, List, Optional

from grafana_mcp.config import config


@dataclass
class MetricPoint:
    timestamp: int
    value: float
    labels: Dict[str, str]


@dataclass
class MetricSeries:
    labels: Dict[str, str]
    points: List[MetricPoint]
    current: float
    avg: float
    max: float
    min: float


@dataclass
class ParsedMetric:
    ref_id: str
    series: List[MetricSeries]


@dataclass
class Anomaly:
    type: str  # cpu | memory | threads | error_rate | response_time | spike
    severity: str  # warning | critical
    metric: str
    current: float
    threshold: float
    labels: Dict[str, str]
    message: str


@dataclass
class AnomalyReport:
    has_anomalies: bool
    anomalies: List[Anomaly]
    summary: str


def parse_query_result(result: dict) -> List[ParsedMetric]:
    parsed = []
    for ref_id, result_data in result.get("results", {}).items():
        frames = result_data.get("frames", [])
        if not frames:
            continue

        series_list = []
        for frame in frames:
            schema_fields = frame.get("schema", {}).get("fields", [])
            data_values = frame.get("data", {}).get("values", [])

            time_idx = next(
                (i for i, f in enumerate(schema_fields) if f.get("name") == "Time" or f.get("type") == "time"), 0
            )
            val_idx = next(
                (i for i, f in enumerate(schema_fields) if f.get("name") != "Time" and f.get("type") != "time"),
                1 if len(schema_fields) > 1 else 0,
            )

            labels = schema_fields[val_idx].get("labels", {}) if val_idx < len(schema_fields) else {}
            times = data_values[time_idx] if time_idx < len(data_values) else []
            values = data_values[val_idx] if val_idx < len(data_values) else []

            points = [
                MetricPoint(timestamp=t, value=v if v is not None else 0.0, labels=labels)
                for t, v in zip(times, values)
            ]

            numeric = [v for v in values if v is not None and not (isinstance(v, float) and v != v)]
            current = numeric[-1] if numeric else 0.0
            avg = sum(numeric) / len(numeric) if numeric else 0.0
            max_val = max(numeric) if numeric else 0.0
            min_val = min(numeric) if numeric else 0.0

            series_list.append(MetricSeries(
                labels=labels, points=points,
                current=current, avg=avg, max=max_val, min=min_val,
            ))

        parsed.append(ParsedMetric(ref_id=ref_id, series=series_list))
    return parsed


def detect_anomalies(metrics: List[ParsedMetric], metric_type: str) -> AnomalyReport:
    anomalies: List[Anomaly] = []
    t = config.thresholds

    for metric in metrics:
        for series in metric.series:
            current = series.current
            labels = series.labels

            # Normalize 0-1 range to 0-100
            pct = current * 100 if current <= 1 else current

            if metric_type in ("cpu", "auto") and pct >= t.cpu_percent:
                anomalies.append(Anomaly(
                    type="cpu", severity="critical" if pct >= 95 else "warning",
                    metric=metric_type, current=pct, threshold=t.cpu_percent, labels=labels,
                    message=f"CPU at {pct:.1f}% (threshold: {t.cpu_percent}%)",
                ))

            if metric_type in ("memory", "auto") and pct >= t.memory_percent:
                anomalies.append(Anomaly(
                    type="memory", severity="critical" if pct >= 95 else "warning",
                    metric=metric_type, current=pct, threshold=t.memory_percent, labels=labels,
                    message=f"Memory at {pct:.1f}% (threshold: {t.memory_percent}%)",
                ))

            if metric_type in ("threads", "auto") and current >= t.thread_count:
                anomalies.append(Anomaly(
                    type="threads",
                    severity="critical" if current >= t.thread_count * 1.5 else "warning",
                    metric=metric_type, current=current, threshold=t.thread_count, labels=labels,
                    message=f"Thread count {current:.0f} exceeds threshold {t.thread_count:.0f}",
                ))

            if metric_type in ("response_time", "auto") and current >= t.response_time_ms:
                anomalies.append(Anomaly(
                    type="response_time",
                    severity="critical" if current >= t.response_time_ms * 2 else "warning",
                    metric=metric_type, current=current, threshold=t.response_time_ms, labels=labels,
                    message=f"Response time {current:.0f}ms exceeds {t.response_time_ms:.0f}ms",
                ))

            # error_rate: value expected as a ratio (0–1) or percentage (0–100)
            error_pct = current * 100 if current <= 1 else current
            if metric_type in ("error_rate", "auto") and error_pct >= t.error_rate:
                anomalies.append(Anomaly(
                    type="error_rate",
                    severity="critical" if error_pct >= t.error_rate * 2 else "warning",
                    metric=metric_type, current=error_pct, threshold=t.error_rate, labels=labels,
                    message=f"Error rate {error_pct:.2f}% exceeds threshold {t.error_rate:.1f}%",
                ))

            if series.avg > 0 and current > series.avg * 2 and current > series.max * 0.9:
                anomalies.append(Anomaly(
                    type="spike", severity="warning",
                    metric=metric_type, current=current, threshold=series.avg * 2, labels=labels,
                    message=f"Spike: current {current:.2f} is {current / series.avg:.1f}x the average {series.avg:.2f}",
                ))

    return AnomalyReport(
        has_anomalies=bool(anomalies),
        anomalies=anomalies,
        summary=_build_summary(anomalies),
    )


def _build_summary(anomalies: List[Anomaly]) -> str:
    if not anomalies:
        return "✅ All metrics within normal thresholds"
    criticals = [a for a in anomalies if a.severity == "critical"]
    warnings = [a for a in anomalies if a.severity == "warning"]
    lines = []
    if criticals:
        lines.append(f"🔴 CRITICAL ({len(criticals)}): {' | '.join(a.message for a in criticals)}")
    if warnings:
        lines.append(f"🟡 WARNING ({len(warnings)}): {' | '.join(a.message for a in warnings)}")
    return "\n".join(lines)


def format_metrics_table(metrics: List[ParsedMetric]) -> str:
    lines = []
    for metric in metrics:
        for series in metric.series:
            label_str = ", ".join(f'{k}="{v}"' for k, v in series.labels.items())
            lines.append(
                f"  [{metric.ref_id}] {label_str or '(no labels)'}\n"
                f"    current={series.current:.3f}  avg={series.avg:.3f}  "
                f"max={series.max:.3f}  min={series.min:.3f}"
            )
    return "\n".join(lines) or "  (no data)"
