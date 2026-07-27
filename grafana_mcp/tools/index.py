import time
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.types import TextContent, Tool

from grafana_mcp.auth.manager import get_session, init_session, inject_session, SessionExpiredError
from grafana_mcp.auth.session import load_session
from grafana_mcp.grafana.api import (
    list_folders, list_dashboards, get_dashboard,
    get_label_values, query_metrics, get_alert_rules, list_datasources,
    search_dashboards, get_firing_alerts, create_annotation, create_snapshot,
    query_logs,
)
from grafana_mcp.parser.metrics import parse_query_result, detect_anomalies, format_metrics_table
from grafana_mcp.tools.scenarios import (
    investigate_latency_spike, investigate_memory_pressure,
    investigate_pod_instability, investigate_error_spike,
    investigate_cpu_spike, investigate_traffic_drop,
    investigate_jvm_health, compare_regions,
)


def _text(content: str) -> list:
    return [TextContent(type="text", text=content)]


async def _resolve_datasource(ds_ref: Optional[Dict], template_vars: List[Dict], all_ds: List[Dict]) -> Optional[Dict]:
    if not ds_ref or not ds_ref.get("uid"):
        return None
    uid = ds_ref["uid"]

    if uid.startswith("$"):
        var_name = uid.lstrip("$").strip("{}")
        var = next((v for v in template_vars if v.get("name") == var_name), None)
        resolved_name = var.get("current", {}).get("value") if var else None
        if resolved_name and isinstance(resolved_name, str):
            found = next((d for d in all_ds if d.get("name") == resolved_name or d.get("uid") == resolved_name), None)
            if found:
                return {"uid": found["uid"], "type": found["type"]}
        fallback = next((d for d in all_ds if d.get("type") == "prometheus"), None)
        return {"uid": fallback["uid"], "type": fallback["type"]} if fallback else None

    found = next((d for d in all_ds if d.get("uid") == uid), None)
    if found:
        return {"uid": found["uid"], "type": found["type"]}
    by_type = next((d for d in all_ds if d.get("type") == ds_ref.get("type", "prometheus")), None)
    return {"uid": by_type["uid"], "type": by_type["type"]} if by_type else None


def register_tools(server: Server) -> None:

    # ── Auth tools ────────────────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> list:
        try:
            return await _dispatch(name, arguments)
        except SessionExpiredError:
            return _text("⚠️ Could not establish a Grafana session — browser login may have been closed or timed out. Call the login tool to try again.")
        except RuntimeError as e:
            return _text(f"Grafana API error: {e}")
        except Exception as e:
            return _text(f"Error executing {name}: {type(e).__name__}: {e}")

    async def _dispatch(name: str, arguments: Dict[str, Any]) -> list:
        if name == "login":
            session = await init_session()
            from datetime import datetime, timezone
            exp = datetime.fromtimestamp(session.expires_at / 1000, tz=timezone.utc).isoformat()
            return _text(f"✅ Logged in successfully. Session expires at {exp}.")

        elif name == "inject_session":
            s = inject_session(arguments["grafana_session"], arguments["expires_at_unix_seconds"] * 1000)
            from datetime import datetime, timezone
            exp = datetime.fromtimestamp(s.expires_at / 1000, tz=timezone.utc).isoformat()
            return _text(f"✅ Session injected. Expires at {exp}")

        elif name == "auth_status":
            session = load_session()
            if not session:
                return _text("❌ No active session. Call login tool first.")
            expires_in = round((session.expires_at - int(time.time() * 1000)) / 60000)
            if expires_in > 0:
                return _text(f"✅ Session active. Expires in {expires_in} minutes.")
            return _text(f"❌ Session expired {abs(expires_in)} minutes ago. Call login tool.")

        # ── Discovery tools ───────────────────────────────────────────────────

        elif name == "list_folders":
            folders = await list_folders()
            lines = [f"  {f['uid'].ljust(22)} {f['title']}" for f in folders]
            header = f"Found {len(folders)} folders:\n\nUID                    Title\n{'─' * 60}"
            return _text(f"{header}\n" + "\n".join(lines))

        elif name == "list_dashboards":
            dashboards = await list_dashboards(arguments["folder_uid"])
            lines = [f"  {d['uid'].ljust(22)} {d['title']}" for d in dashboards]
            header = f"Found {len(dashboards)} dashboards in folder {arguments['folder_uid']}:\n\nUID                    Title\n{'─' * 60}"
            return _text(f"{header}\n" + "\n".join(lines))

        elif name == "get_dashboard_info":
            dashboard = await get_dashboard(arguments["dashboard_uid"])
            panels = dashboard.get("panels", [])
            vars_ = dashboard.get("templating", {}).get("list", [])
            panel_lines = []
            for p in panels:
                targets = p.get("targets", [])
                line = f"  [id:{p.get('id')}] \"{p.get('title')}\" (type: {p.get('type')})"
                if targets:
                    line += f"\n    queries: {', '.join(t.get('refId','') for t in targets)}"
                panel_lines.append(line)
            var_lines = [
                f"  {v.get('name')} ({v.get('type')})"
                + (f" = {v['current']['value']}" if v.get('current', {}).get('value') else "")
                for v in vars_
            ]
            parts = [
                f"Dashboard: {dashboard.get('title')}",
                f"Tags: {', '.join(dashboard.get('tags', [])) or 'none'}",
                f"Refresh: {dashboard.get('refresh', '')}",
                "",
                f"Template Variables ({len(var_lines)}):",
                "\n".join(var_lines) or "  (none)",
                "",
                f"Panels ({len(panels)}):",
                "\n".join(panel_lines),
            ]
            return _text("\n".join(p for p in parts if p is not None))

        elif name == "get_label_values":
            now_ms = int(time.time() * 1000)
            values = await get_label_values(
                arguments["datasource_uid"],
                arguments["label_name"],
                arguments.get("matchers", []),
                now_ms - 3 * 60 * 60 * 1000,
                now_ms,
            )
            return _text(f"Label \"{arguments['label_name']}\" has {len(values)} values:\n" + "\n".join(f"  {v}" for v in values))

        # ── Metrics tools ─────────────────────────────────────────────────────

        elif name == "query_metrics":
            range_min = arguments.get("range_minutes", 60)
            to_ms = int(time.time() * 1000)
            from_ms = to_ms - range_min * 60 * 1000
            result = await query_metrics(
                arguments["datasource_uid"],
                arguments.get("datasource_type", "prometheus"),
                [{"refId": "A", "expr": arguments["expr"],
                  "legendFormat": arguments.get("legend_format", ""),
                  "intervalMs": 60000, "maxDataPoints": 300}],
                from_ms, to_ms,
            )
            parsed = parse_query_result(result)
            return _text(
                f"Query: {arguments['expr']}\nRange: last {range_min} minutes\n\nResults:\n"
                + format_metrics_table(parsed)
            )

        elif name == "detect_anomalies":
            range_min = arguments.get("range_minutes", 60)
            to_ms = int(time.time() * 1000)
            from_ms = to_ms - range_min * 60 * 1000
            result = await query_metrics(
                arguments["datasource_uid"],
                arguments.get("datasource_type", "prometheus"),
                [{"refId": "A", "expr": arguments["expr"], "intervalMs": 60000, "maxDataPoints": 300}],
                from_ms, to_ms,
            )
            parsed = parse_query_result(result)
            report = detect_anomalies(parsed, arguments.get("metric_type", "auto"))
            lines = [
                f"Anomaly Detection — last {range_min} minutes",
                f"Query: {arguments['expr']}",
                "",
                report.summary,
            ]
            if report.has_anomalies:
                lines.append(f"\nAnomalies ({len(report.anomalies)}):")
                for a in report.anomalies:
                    lines.append(f"  [{a.severity.upper()}] {a.message}\n  Labels: {a.labels}")
            lines.append("\nMetric values:")
            lines.append(format_metrics_table(parsed))
            return _text("\n".join(lines))

        elif name == "check_dashboard_health":
            range_min = arguments.get("range_minutes", 60)
            filters = arguments.get("filters", {})
            dashboard, all_ds = await _fetch_dashboard_and_ds(arguments["dashboard_uid"])
            to_ms = int(time.time() * 1000)
            from_ms = to_ms - range_min * 60 * 1000
            lines = [f"Health Check: {dashboard.get('title')}", f"Range: last {range_min} minutes", ""]
            anomaly_count = 0
            template_vars = dashboard.get("templating", {}).get("list", [])
            for panel in dashboard.get("panels", []):
                if not panel.get("title") or panel.get("type") == "row":
                    continue
                targets = [t for t in panel.get("targets", []) if t.get("expr")]
                if not targets:
                    continue
                ds_ref = panel.get("datasource") or (targets[0].get("datasource") if targets else None)
                ds = await _resolve_datasource(ds_ref, template_vars, all_ds)
                if not ds:
                    lines.append(f"Panel: {panel['title']} — ⚠️ could not resolve datasource")
                    lines.append("")
                    continue
                try:
                    queries = []
                    for t in targets:
                        expr = t.get("expr", "")
                        if filters:
                            filter_str = ",".join(f'{k}="{v}"' for k, v in filters.items())
                            if "{" in expr:
                                expr = expr.replace("{", "{" + filter_str + ",", 1)
                            else:
                                # Bare metric name — append label selector
                                expr = f"{expr}{{{filter_str}}}"
                        queries.append({"refId": t["refId"], "expr": expr, "legendFormat": t.get("legendFormat", "")})
                    result = await query_metrics(ds["uid"], ds["type"], queries, from_ms, to_ms)
                    parsed = parse_query_result(result)
                    report = detect_anomalies(parsed, "auto")
                    lines.append(f"Panel: {panel['title']}")
                    lines.append(f"  {report.summary}")
                    if report.has_anomalies:
                        anomaly_count += len(report.anomalies)
                    lines.append("")
                except Exception as e:
                    lines.append(f"Panel: {panel['title']} — ⚠️ query failed: {e}")
                    lines.append("")
            status = (f"🔴 {anomaly_count} anomalies detected" if anomaly_count else "✅ All panels healthy — no anomalies detected")
            lines.insert(0, status)
            return _text("\n".join(lines))

        elif name == "get_alert_rules":
            result = await get_alert_rules(arguments["dashboard_uid"])
            if result.get("status") != "success" or not result.get("data", {}).get("groups"):
                return _text("No alert rules found for this dashboard.")
            lines = []
            for group in result["data"]["groups"]:
                lines.append(f"Group: {group['name']}")
                for rule in group.get("rules", []):
                    lines.append(f"  Rule: {rule['name']} [{rule['state']}]")
                    for alert in rule.get("alerts", []):
                        lines.append(f"    Alert: {alert['state']} — {alert['labels']}")
            return _text("\n".join(lines))

        elif name == "query_logs":
            range_min = arguments.get("range_minutes", 60)
            to_ms = int(time.time() * 1000)
            from_ms = to_ms - range_min * 60 * 1000
            result = await query_logs(
                datasource_uid=arguments["datasource_uid"],
                expr=arguments["expr"],
                from_ms=from_ms,
                to_ms=to_ms,
                limit=arguments.get("limit", 100),
                direction=arguments.get("direction", "backward"),
            )
            streams = result.get("data", {}).get("result", [])
            if not streams:
                return _text(f"No logs found for query: {arguments['expr']}")
            lines = [f"Logs — last {range_min} minutes", f"Query: {arguments['expr']}", ""]
            total = 0
            for stream in streams:
                stream_labels = stream.get("stream", {})
                label_str = " ".join(f'{k}={v}' for k, v in stream_labels.items())
                lines.append(f"Stream: {label_str}")
                for ts_ns, log_line in stream.get("values", []):
                    from datetime import datetime, timezone
                    ts_sec = int(ts_ns) / 1e9
                    ts_str = datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%H:%M:%S")
                    lines.append(f"  [{ts_str}] {log_line}")
                    total += 1
            lines.append(f"\nTotal: {total} log lines across {len(streams)} stream(s)")
            return _text("\n".join(lines))

        elif name == "create_snapshot":
            result = await create_snapshot(
                dashboard_uid=arguments["dashboard_uid"],
                name=arguments.get("name"),
                expires_seconds=arguments.get("expires_seconds", 3600),
            )
            url = result.get("url") or result.get("externalUrl", "")
            delete_url = result.get("deleteUrl", "")
            key = result.get("key", "")
            lines = [f"Snapshot created successfully.", f"Key: {key}"]
            if url:
                lines.append(f"View: {url}")
            if delete_url:
                lines.append(f"Delete: {delete_url}")
            return _text("\n".join(lines))

        elif name == "create_annotation":
            result = await create_annotation(
                text=arguments["text"],
                tags=arguments.get("tags", []),
                dashboard_uid=arguments.get("dashboard_uid"),
                panel_id=arguments.get("panel_id"),
                time_ms=arguments.get("time_ms"),
                time_end_ms=arguments.get("time_end_ms"),
            )
            annotation_id = result.get("id", result.get("message", "unknown"))
            return _text(f"Annotation created (id: {annotation_id}): {arguments['text']}")

        elif name == "get_firing_alerts":
            alerts = await get_firing_alerts(state=arguments.get("state"))
            if not alerts:
                return _text("No firing alerts found.")
            lines = [f"Found {len(alerts)} alert(s):"]
            for a in alerts:
                labels = a.get("labels", {})
                annotations = a.get("annotations", {})
                status = a.get("status", {})
                name_str = labels.get("alertname", "(unknown)")
                state_str = status.get("state", "unknown")
                severity = labels.get("severity", "")
                summary = annotations.get("summary", annotations.get("message", ""))
                lines.append(f"\n  [{state_str.upper()}] {name_str}" + (f" (severity: {severity})" if severity else ""))
                if summary:
                    lines.append(f"    {summary}")
                starts_at = a.get("startsAt", "")
                if starts_at:
                    lines.append(f"    Firing since: {starts_at}")
                dashboard_uid = labels.get("grafana_folder", "") or annotations.get("__dashboardUid__", "")
                if dashboard_uid:
                    lines.append(f"    Dashboard: {dashboard_uid}")
            return _text("\n".join(lines))

        elif name == "search_dashboards":
            results = await search_dashboards(
                query=arguments.get("query", ""),
                tags=arguments.get("tags", []),
            )
            lines = [f"  {d.get('uid', '').ljust(22)} {d.get('folderTitle', 'General').ljust(20)} {d.get('title', '')}" for d in results]
            header = f"Found {len(results)} dashboards:\n\nUID                    Folder               Title\n{'─' * 70}"
            return _text(f"{header}\n" + "\n".join(lines))

        elif name == "list_datasources":
            datasources = await list_datasources()
            lines = [f"  {d.get('uid', '').ljust(22)} {d.get('type', '').ljust(16)} {d.get('name', '')}" for d in datasources]
            header = f"Found {len(datasources)} datasources:\n\nUID                    Type             Name\n{'─' * 70}"
            return _text(f"{header}\n" + "\n".join(lines))

        # ── Scenario / investigation tools ────────────────────────────────────

        elif name == "investigate_latency_spike":
            report = await investigate_latency_spike(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
                service_filter=arguments.get("service_filter", ""),
            )
            return _text(report)

        elif name == "investigate_memory_pressure":
            report = await investigate_memory_pressure(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
            )
            return _text(report)

        elif name == "investigate_pod_instability":
            report = await investigate_pod_instability(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
            )
            return _text(report)

        elif name == "investigate_error_spike":
            report = await investigate_error_spike(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
            )
            return _text(report)

        elif name == "investigate_cpu_spike":
            report = await investigate_cpu_spike(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
            )
            return _text(report)

        elif name == "investigate_traffic_drop":
            report = await investigate_traffic_drop(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
            )
            return _text(report)

        elif name == "investigate_jvm_health":
            report = await investigate_jvm_health(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
                job=arguments.get("job"),
            )
            return _text(report)

        elif name == "compare_regions":
            report = await compare_regions(
                datasource_uid=arguments["datasource_uid"],
                namespace=arguments.get("namespace"),
                range_minutes=arguments.get("range_minutes", 60),
                regions=arguments.get("regions"),
            )
            return _text(report)

        else:
            return _text(f"Unknown tool: {name}")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [
            Tool(name="login", description="Authenticate with Grafana. Opens a browser window — sign in with Okta and approve the push. No credentials needed.",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="inject_session", description="Manually inject a Grafana session cookie from browser DevTools.",
                 inputSchema={"type": "object", "properties": {
                     "grafana_session": {"type": "string"},
                     "expires_at_unix_seconds": {"type": "number"},
                 }, "required": ["grafana_session", "expires_at_unix_seconds"]}),
            Tool(name="auth_status", description="Check current authentication status and session expiry.",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="list_folders", description="List all Grafana dashboard folders.",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="list_dashboards", description="List all dashboards inside a specific folder.",
                 inputSchema={"type": "object", "properties": {
                     "folder_uid": {"type": "string", "description": "Folder UID from list_folders"},
                 }, "required": ["folder_uid"]}),
            Tool(name="get_dashboard_info", description="Get full dashboard definition including panels, variables, and datasource info.",
                 inputSchema={"type": "object", "properties": {
                     "dashboard_uid": {"type": "string"},
                 }, "required": ["dashboard_uid"]}),
            Tool(name="get_label_values", description="Get available values for a Prometheus label.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "label_name": {"type": "string"},
                     "matchers": {"type": "array", "items": {"type": "string"}},
                 }, "required": ["datasource_uid", "label_name"]}),
            Tool(name="query_metrics", description="Run a PromQL query against a Grafana datasource.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "datasource_type": {"type": "string", "default": "prometheus"},
                     "expr": {"type": "string"},
                     "legend_format": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid", "expr"]}),
            Tool(name="detect_anomalies", description="Query a PromQL expression and detect spikes or threshold breaches.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "datasource_type": {"type": "string", "default": "prometheus"},
                     "expr": {"type": "string"},
                     "metric_type": {"type": "string", "enum": ["cpu", "memory", "threads", "error_rate", "response_time", "auto"], "default": "auto"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid", "expr"]}),
            Tool(name="check_dashboard_health", description="Run a full health check on a dashboard — queries all panels and reports anomalies.",
                 inputSchema={"type": "object", "properties": {
                     "dashboard_uid": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                     "filters": {"type": "object", "additionalProperties": {"type": "string"}},
                 }, "required": ["dashboard_uid"]}),
            Tool(name="get_alert_rules", description="Get active Grafana alert rules for a dashboard.",
                 inputSchema={"type": "object", "properties": {
                     "dashboard_uid": {"type": "string"},
                 }, "required": ["dashboard_uid"]}),
            Tool(name="list_datasources", description="List all configured Grafana datasources with their UID, type, and name.",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="query_logs", description="Run a LogQL query against a Loki datasource and return log lines.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string", "description": "UID of the Loki datasource"},
                     "expr": {"type": "string", "description": "LogQL query expression"},
                     "range_minutes": {"type": "number", "default": 60, "description": "How many minutes back to query"},
                     "limit": {"type": "number", "default": 100, "description": "Max number of log lines to return"},
                     "direction": {"type": "string", "enum": ["backward", "forward"], "default": "backward"},
                 }, "required": ["datasource_uid", "expr"]}),
            Tool(name="create_snapshot", description="Create a shareable snapshot of a dashboard's current state and return its URL.",
                 inputSchema={"type": "object", "properties": {
                     "dashboard_uid": {"type": "string", "description": "UID of the dashboard to snapshot"},
                     "name": {"type": "string", "description": "Optional name for the snapshot"},
                     "expires_seconds": {"type": "number", "description": "Seconds until snapshot expires (default 3600)", "default": 3600},
                 }, "required": ["dashboard_uid"]}),
            Tool(name="create_annotation", description="Create a Grafana annotation to mark an event (deploy, incident, etc.) on dashboards.",
                 inputSchema={"type": "object", "properties": {
                     "text": {"type": "string", "description": "Annotation text/description"},
                     "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to categorize the annotation"},
                     "dashboard_uid": {"type": "string", "description": "Scope annotation to a specific dashboard"},
                     "panel_id": {"type": "number", "description": "Scope annotation to a specific panel"},
                     "time_ms": {"type": "number", "description": "Start time in Unix milliseconds (defaults to now)"},
                     "time_end_ms": {"type": "number", "description": "End time in Unix milliseconds (for range annotations)"},
                 }, "required": ["text"]}),
            Tool(name="get_firing_alerts", description="Get all currently firing alerts across all dashboards from Alertmanager.",
                 inputSchema={"type": "object", "properties": {
                     "state": {"type": "string", "enum": ["active", "suppressed", "unprocessed"], "description": "Filter by alert state (omit for all)"},
                 }}),
            Tool(name="search_dashboards", description="Search dashboards across all folders by title or tag.",
                 inputSchema={"type": "object", "properties": {
                     "query": {"type": "string", "description": "Title search string (partial match)"},
                     "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by dashboard tags"},
                 }}),

            # ── Scenario tools ────────────────────────────────────────────────
            Tool(name="investigate_latency_spike",
                 description="Scenario: service is slow or latency spiked. Checks GC pause rate, CPU throttling, thread count, HTTP p99 latency, and request rate in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string", "description": "Kubernetes namespace filter (supports regex, e.g. '.*taskflow.*')"},
                     "range_minutes": {"type": "number", "default": 60},
                     "service_filter": {"type": "string", "description": "Extra PromQL label filter string e.g. 'job=\"my-service\"'"},
                 }, "required": ["datasource_uid"]}),

            Tool(name="investigate_memory_pressure",
                 description="Scenario: memory concern or OOM risk. Checks JVM heap %, working set memory, OOM kills, GC frequency, and memory vs limit in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid"]}),

            Tool(name="investigate_pod_instability",
                 description="Scenario: pods crashing or restarting. Checks restart count, CrashLoopBackOff, not-ready containers, OOM kills, and non-running phases in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid"]}),

            Tool(name="investigate_error_spike",
                 description="Scenario: error rate jumped. Checks HTTP 4xx/5xx rate, p99 latency, pod restarts, CPU throttling, and request rate by status in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid"]}),

            Tool(name="investigate_cpu_spike",
                 description="Scenario: CPU suddenly high. Checks CPU usage, throttle ratio, GC pressure, active thread count, and request rate in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid"]}),

            Tool(name="investigate_traffic_drop",
                 description="Scenario: requests dropped to zero or near-zero. Checks request rate, pod ready status, unscheduled pods, network errors, and restarts in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                 }, "required": ["datasource_uid"]}),

            Tool(name="investigate_jvm_health",
                 description="Scenario: JVM deep dive. Checks heap %, metaspace, GC pause time, GC collections/sec, live thread count, and thread states in one call.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                     "job": {"type": "string", "description": "Prometheus job label filter e.g. 'CAI_jmxMetrics'"},
                 }, "required": ["datasource_uid"]}),

            Tool(name="compare_regions",
                 description="Scenario: multi-region sanity check. Runs CPU, memory, error rate, and p99 latency for each region in parallel and flags which region is the outlier.",
                 inputSchema={"type": "object", "properties": {
                     "datasource_uid": {"type": "string"},
                     "namespace": {"type": "string"},
                     "range_minutes": {"type": "number", "default": 60},
                     "regions": {"type": "array", "items": {"type": "string"}, "description": "Region name substrings to match in pod names (default: ['usw1','usw3','usw5'])"},
                 }, "required": ["datasource_uid"]}),
        ]


async def _fetch_dashboard_and_ds(dashboard_uid: str):
    import asyncio
    dashboard, all_ds = await asyncio.gather(
        get_dashboard(dashboard_uid),
        list_datasources(),
    )
    return dashboard, all_ds
