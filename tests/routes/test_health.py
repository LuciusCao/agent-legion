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
