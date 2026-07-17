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
    res = await grafana_get(
        f"/apis/dashboard.grafana.app/v1beta1/namespaces/default/dashboards/{dashboard_uid}/dto"
    )
    return res.get("spec", {}).get("body", res.get("spec", res))


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


async def list_datasources() -> List[Dict]:
    return await grafana_get("/api/datasources")


