from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from grafana_mcp.grafana.client import grafana_get, grafana_post


async def list_folders() -> List[Dict]:
    page, all_folders = 1, []
    while True:
        batch = await grafana_get(f"/api/folders?page={page}&limit=100")
        all_folders.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return all_folders


async def list_dashboards(folder_uid: str) -> List[Dict]:
    page, all_dashboards = 1, []
    while True:
        batch = await grafana_get(f"/api/search?limit=100&page={page}&type=dash-db&folderUIDs={folder_uid}")
        all_dashboards.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return all_dashboards


async def get_dashboard(dashboard_uid: str) -> Dict:
    try:
        res = await grafana_get(
            f"/apis/dashboard.grafana.app/v1beta1/namespaces/default/dashboards/{dashboard_uid}/dto"
        )
        return res.get("spec", {}).get("body", res.get("spec", res))
    except RuntimeError:
        # Fallback for Grafana < 10.3
        res = await grafana_get(f"/api/dashboards/uid/{dashboard_uid}")
        return res.get("dashboard", res)


async def get_label_values(
    datasource_uid: str,
    label_name: str,
    matchers: Optional[List[str]] = None,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
) -> List[str]:
    params: Dict[str, Any] = {"limit": 40000}
    if from_ms:
        params["start"] = from_ms // 1000
    if to_ms:
        params["end"] = to_ms // 1000
    if matchers:
        # httpx doesn't support repeated keys via dict — build manually
        qs = urlencode(params)
        for m in matchers:
            qs += f"&match[]={m}"
        res = await grafana_get(
            f"/api/datasources/uid/{datasource_uid}/resources/api/v1/label/{label_name}/values?{qs}"
        )
    else:
        res = await grafana_get(
            f"/api/datasources/uid/{datasource_uid}/resources/api/v1/label/{label_name}/values",
            params=params,
        )
    return res.get("data", [])


async def query_metrics(
    datasource_uid: str,
    datasource_type: str,
    queries: List[Dict],
    from_ms: int,
    to_ms: int,
) -> Dict:
    body = {
        "queries": [
            {
                "datasource": {"type": datasource_type, "uid": datasource_uid},
                "refId": q["refId"],
                "expr": q["expr"],
                "format": "time_series",
                "legendFormat": q.get("legendFormat", ""),
                "intervalMs": q.get("intervalMs", 60000),
                "maxDataPoints": q.get("maxDataPoints", 300),
                "utcOffsetSec": 0,
            }
            for q in queries
        ],
        "from": str(from_ms),
        "to": str(to_ms),
    }
    return await grafana_post(f"/api/ds/query?ds_type={datasource_type}", body)


async def get_alert_rules(dashboard_uid: str) -> Dict:
    return await grafana_get(f"/api/prometheus/grafana/api/v1/rules?dashboard_uid={dashboard_uid}")


async def search_dashboards(query: str = "", tags: Optional[List[str]] = None, limit: int = 100) -> List[Dict]:
    page, all_results = 1, []
    while True:
        params: Dict[str, Any] = {"type": "dash-db", "limit": limit, "page": page}
        if query:
            params["query"] = query
        if tags:
            # Build manually — repeated keys not supported in dict
            from urllib.parse import urlencode
            qs = urlencode(params)
            for tag in tags:
                qs += f"&tag={tag}"
            batch = await grafana_get(f"/api/search?{qs}")
        else:
            batch = await grafana_get("/api/search", params=params)
        all_results.extend(batch)
        if len(batch) < limit:
            break
        page += 1
    return all_results


async def query_logs(
    datasource_uid: str,
    expr: str,
    from_ms: int,
    to_ms: int,
    limit: int = 100,
    direction: str = "backward",
) -> Dict:
    params: Dict[str, Any] = {
        "query": expr,
        "start": from_ms * 1_000_000,  # Loki uses nanoseconds
        "end": to_ms * 1_000_000,
        "limit": limit,
        "direction": direction,
    }
    return await grafana_get(
        f"/api/datasources/uid/{datasource_uid}/resources/loki/api/v1/query_range",
        params=params,
    )


async def create_snapshot(
    dashboard_uid: str,
    name: Optional[str] = None,
    expires_seconds: int = 3600,
) -> Dict:
    dashboard = await get_dashboard(dashboard_uid)
    body: Dict[str, Any] = {
        "dashboard": dashboard,
        "expires": expires_seconds,
    }
    if name:
        body["name"] = name
    return await grafana_post("/api/snapshots", body)


async def create_annotation(
    text: str,
    tags: Optional[List[str]] = None,
    dashboard_uid: Optional[str] = None,
    panel_id: Optional[int] = None,
    time_ms: Optional[int] = None,
    time_end_ms: Optional[int] = None,
) -> Dict:
    body: Dict[str, Any] = {"text": text, "tags": tags or []}
    if dashboard_uid:
        body["dashboardUID"] = dashboard_uid
    if panel_id is not None:
        body["panelId"] = panel_id
    if time_ms:
        body["time"] = time_ms
    if time_end_ms:
        body["timeEnd"] = time_end_ms
    return await grafana_post("/api/annotations", body)


async def get_firing_alerts(state: Optional[str] = None) -> List[Dict]:
    params: Dict[str, Any] = {}
    if state:
        params["filter"] = f"state={state}"
    return await grafana_get("/api/alertmanager/grafana/api/v2/alerts", params=params or None)


async def list_datasources() -> List[Dict]:
    return await grafana_get("/api/datasources")


