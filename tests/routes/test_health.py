"""GET /api/health: storage readiness field (configured/reachable only)."""

from __future__ import annotations

from server.app.studio_chat.instance_probe import instance_proof


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


def test_health_body_is_unchanged_without_probe(anon_client, monkeypatch) -> None:
    """#915: the api_base self-check rides /api/health; a plain call (or an
    invalid nonce) must return exactly the pre-#915 body shape."""
    monkeypatch.setattr(
        "server.app.routes.common.cached_storage_status",
        lambda app_state: {"configured": True, "reachable": True},
    )
    plain = anon_client.get("/api/health").json()
    assert set(plain) == {"ok", "workers", "storage"}
    for bad in ("ZZ", "abc", "a" * 129, "AB" * 16):
        response = anon_client.get("/api/health", params={"instance_probe": bad})
        assert response.status_code == 200
        assert response.json() == plain


def test_health_answers_the_instance_probe_without_credentials(anon_client) -> None:
    nonce = "0123456789abcdef" * 2
    body = anon_client.get("/api/health", params={"instance_probe": nonce}).json()
    assert body["ok"] is True
    assert body["instance_proof"] == instance_proof(nonce)
