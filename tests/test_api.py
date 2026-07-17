"""Tests for grafana_mcp.grafana.api — all HTTP calls are mocked.

api.py imports grafana_get/grafana_post via `from client import ...`, so we must
patch the names in the grafana_mcp.grafana.api module namespace, not in client.
"""
import pytest
import grafana_mcp.grafana.api as _api


# ── helpers ──────────────────────────────────────────────────────────────────

def patch_get(monkeypatch, return_value):
    async def mock(path, params=None):
        return return_value
    monkeypatch.setattr(_api, "grafana_get", mock)


def patch_post(monkeypatch, return_value):
    async def mock(path, body):
        return return_value
    monkeypatch.setattr(_api, "grafana_post", mock)


# ── list_folders ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_folders_single_page(monkeypatch):
    from grafana_mcp.grafana.api import list_folders
    patch_get(monkeypatch, [{"uid": "f1", "title": "Folder1"}])
    result = await list_folders()
    assert len(result) == 1
    assert result[0]["uid"] == "f1"


@pytest.mark.asyncio
async def test_list_folders_empty(monkeypatch):
    from grafana_mcp.grafana.api import list_folders
    patch_get(monkeypatch, [])
    result = await list_folders()
    assert result == []


# ── list_dashboards ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_dashboards(monkeypatch):
    from grafana_mcp.grafana.api import list_dashboards
    patch_get(monkeypatch, [{"uid": "d1", "title": "CPU"}, {"uid": "d2", "title": "Memory"}])
    result = await list_dashboards("folder1")
    assert len(result) == 2
    assert result[0]["uid"] == "d1"


# ── get_dashboard ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_dashboard_new_api(monkeypatch):
    from grafana_mcp.grafana.api import get_dashboard
    patch_get(monkeypatch, {"spec": {"body": {"title": "My Dashboard", "panels": []}}})
    result = await get_dashboard("uid1")
    assert result["title"] == "My Dashboard"


@pytest.mark.asyncio
async def test_get_dashboard_fallback_to_classic(monkeypatch):
    from grafana_mcp.grafana.api import get_dashboard
    call_count = [0]

    async def mock_get(path, params=None):
        call_count[0] += 1
        if "apis/dashboard" in path:
            raise RuntimeError("404 Not Found")
        return {"dashboard": {"title": "Classic Dashboard", "panels": []}}

    monkeypatch.setattr(_api, "grafana_get", mock_get)
    result = await get_dashboard("uid1")
    assert result["title"] == "Classic Dashboard"
    assert call_count[0] == 2  # tried k8s API, then classic


# ── search_dashboards ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_dashboards_by_query(monkeypatch):
    from grafana_mcp.grafana.api import search_dashboards
    patch_get(monkeypatch, [{"uid": "d1", "title": "CPU Metrics", "folderTitle": "Infra"}])
    result = await search_dashboards(query="cpu")
    assert len(result) == 1
    assert result[0]["title"] == "CPU Metrics"


@pytest.mark.asyncio
async def test_search_dashboards_empty(monkeypatch):
    from grafana_mcp.grafana.api import search_dashboards
    patch_get(monkeypatch, [])
    result = await search_dashboards(query="nonexistent")
    assert result == []


# ── list_datasources ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_datasources(monkeypatch):
    from grafana_mcp.grafana.api import list_datasources
    patch_get(monkeypatch, [
        {"uid": "prom1", "type": "prometheus", "name": "Prometheus"},
        {"uid": "loki1", "type": "loki", "name": "Loki"},
    ])
    result = await list_datasources()
    assert len(result) == 2
    types = [d["type"] for d in result]
    assert "prometheus" in types
    assert "loki" in types


# ── get_label_values ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_label_values(monkeypatch):
    from grafana_mcp.grafana.api import get_label_values
    patch_get(monkeypatch, {"status": "success", "data": ["prod", "staging", "dev"]})
    result = await get_label_values("prom1", "namespace")
    assert "prod" in result
    assert len(result) == 3


# ── query_metrics ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_metrics(monkeypatch):
    from grafana_mcp.grafana.api import query_metrics
    fake_response = {
        "results": {
            "A": {
                "frames": [{
                    "schema": {"fields": [
                        {"name": "Time", "type": "time"},
                        {"name": "Value", "type": "number", "labels": {"job": "api"}},
                    ]},
                    "data": {"values": [[1000, 2000], [0.5, 0.8]]},
                }]
            }
        }
    }
    patch_post(monkeypatch, fake_response)
    result = await query_metrics("prom1", "prometheus", [{"refId": "A", "expr": "up"}], 0, 3600000)
    assert "results" in result
    assert "A" in result["results"]


# ── get_alert_rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_alert_rules(monkeypatch):
    from grafana_mcp.grafana.api import get_alert_rules
    patch_get(monkeypatch, {
        "status": "success",
        "data": {"groups": [{"name": "grp1", "rules": [{"name": "HighCPU", "state": "firing", "alerts": []}]}]},
    })
    result = await get_alert_rules("dash1")
    assert result["status"] == "success"
    assert result["data"]["groups"][0]["name"] == "grp1"


# ── get_firing_alerts ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_firing_alerts(monkeypatch):
    from grafana_mcp.grafana.api import get_firing_alerts
    patch_get(monkeypatch, [
        {
            "labels": {"alertname": "HighCPU", "severity": "critical"},
            "annotations": {"summary": "CPU above 95%"},
            "status": {"state": "active"},
            "startsAt": "2024-01-15T10:30:00Z",
        }
    ])
    result = await get_firing_alerts()
    assert len(result) == 1
    assert result[0]["labels"]["alertname"] == "HighCPU"


@pytest.mark.asyncio
async def test_get_firing_alerts_empty(monkeypatch):
    from grafana_mcp.grafana.api import get_firing_alerts
    patch_get(monkeypatch, [])
    result = await get_firing_alerts()
    assert result == []


# ── create_annotation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_annotation(monkeypatch):
    from grafana_mcp.grafana.api import create_annotation
    posted = {}

    async def mock_post(path, body):
        posted.update(body)
        posted["_path"] = path
        return {"id": 42, "message": "Annotation added"}

    monkeypatch.setattr(_api, "grafana_post", mock_post)
    result = await create_annotation("Deploy v1.0", tags=["deploy"], dashboard_uid="dash1")
    assert result["id"] == 42
    assert posted["_path"] == "/api/annotations"
    assert posted["text"] == "Deploy v1.0"
    assert posted["dashboardUID"] == "dash1"
    assert "deploy" in posted["tags"]


@pytest.mark.asyncio
async def test_create_annotation_minimal(monkeypatch):
    from grafana_mcp.grafana.api import create_annotation
    posted = {}

    async def mock_post(path, body):
        posted.update(body)
        return {"id": 1}

    monkeypatch.setattr(_api, "grafana_post", mock_post)
    await create_annotation("Incident started")
    assert posted["text"] == "Incident started"
    assert "dashboardUID" not in posted


# ── create_snapshot ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_snapshot(monkeypatch):
    from grafana_mcp.grafana.api import create_snapshot

    async def mock_get(path, params=None):
        return {"spec": {"body": {"title": "My Dashboard", "panels": []}}}

    async def mock_post(path, body):
        return {"key": "abc123", "url": "/dashboard/snapshot/abc123", "deleteUrl": "/api/snapshots/abc123"}

    monkeypatch.setattr(_api, "grafana_get", mock_get)
    monkeypatch.setattr(_api, "grafana_post", mock_post)

    result = await create_snapshot("dash1", name="Test Snapshot", expires_seconds=7200)
    assert result["key"] == "abc123"
    assert "url" in result


# ── query_logs ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_logs(monkeypatch):
    from grafana_mcp.grafana.api import query_logs
    patch_get(monkeypatch, {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"app": "api", "env": "prod"},
                    "values": [
                        ["1705316000000000000", "ERROR: connection refused"],
                        ["1705316060000000000", "INFO: retry successful"],
                    ],
                }
            ],
        },
    })
    result = await query_logs("loki1", '{app="api"}', 0, 3600000)
    streams = result["data"]["result"]
    assert len(streams) == 1
    assert len(streams[0]["values"]) == 2
    assert streams[0]["stream"]["app"] == "api"


@pytest.mark.asyncio
async def test_query_logs_empty(monkeypatch):
    from grafana_mcp.grafana.api import query_logs
    patch_get(monkeypatch, {"status": "success", "data": {"resultType": "streams", "result": []}})
    result = await query_logs("loki1", '{app="missing"}', 0, 3600000)
    assert result["data"]["result"] == []
