"""GET /api/health: storage readiness field (configured/reachable only)."""

from __future__ import annotations


def test_health_reports_storage_status(anon_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "server.app.routes.common.cached_storage_status",
        lambda app_state: {"configured": True, "reachable": True},
    )
    response = anon_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["storage"] == {"configured": True, "reachable": True}


def test_health_reports_unconfigured_storage(anon_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "server.app.routes.common.cached_storage_status",
        lambda app_state: {"configured": False, "reachable": False},
    )
    response = anon_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["storage"] == {"configured": False, "reachable": False}


def test_health_reports_host_role(client) -> None:
    """#521 方案 B: the process's host role is surfaced so the native
    launcher can verify deployment-shape consistency before starting a
    dedicated scheduler (stale combined backend + new scheduler would
    double-schedule)."""
    response = client.get("/api/health")
    assert response.status_code == 200
    # The test client's app is built without a role → combined default.
    assert response.json()["role"] == "combined"
