"""Admin registry routes for Studio chat ACP agents (phase 3 chunk 4).

Detection (#332) is patched at the AgentCatalogDetector class level: the
shared test app's detector instance must never depend on the host PATH, so
these tests stay identical on machines with or without the catalog CLIs.
"""

from __future__ import annotations

import logging

import pytest

from server.app.studio_chat.agent_catalog import AgentCatalogDetector, CatalogDetection

CSRF = {"x-agent-legion-request": "1"}
REGISTRY_URL = "/api/admin/studio-agents"
REDETECT_URL = "/api/admin/studio-agents/redetect"


@pytest.fixture(autouse=True)
def _no_host_detection(monkeypatch):
    """Default: nothing detected, regardless of the host running the test."""
    monkeypatch.setattr(AgentCatalogDetector, "detect", lambda self, *, force=False: {})


def _stub_detection(monkeypatch, statuses: dict[str, CatalogDetection]) -> None:
    monkeypatch.setattr(AgentCatalogDetector, "detect", lambda self, *, force=False: statuses)


def _member_client(client, username="registry-member", password="pw1"):
    response = client.post("/api/users", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _document() -> dict:
    return {
        "api_base": "http://127.0.0.1:8000",
        "agents": [
            {
                "id": "kimi-acp",
                "label": "Kimi Code (ACP)",
                "command": "kimi",
                "args": ["acp"],
            }
        ],
    }


def test_anonymous_and_member_are_rejected(client, anon_client) -> None:
    assert anon_client.get(REGISTRY_URL).status_code == 401
    assert anon_client.put(REGISTRY_URL, json=_document()).status_code == 401
    assert anon_client.post(REDETECT_URL).status_code == 401
    member = _member_client(client)
    assert member.get(REGISTRY_URL).status_code == 403
    assert member.put(REGISTRY_URL, json=_document()).status_code == 403
    assert member.post(REDETECT_URL).status_code == 403


def _strip_probes(payload: dict) -> dict[str, bool]:
    availability = payload.pop("availability")
    assert all(isinstance(value, bool) for value in availability.values())
    detection = payload.pop("detection")
    assert all(set(item) == {"detected", "path", "version"} for item in detection.values())
    return availability


def test_default_document_and_roundtrip(client) -> None:
    response = client.get(REGISTRY_URL)
    assert response.status_code == 200
    assert response.json() == {
        "api_base": "http://127.0.0.1:8000",
        "agents": [],
        "availability": {},
        "detection": {},
    }

    document = _document()
    response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    expected = _document()
    # source is server-managed provenance (#332): admin-written rows are manual.
    expected["agents"][0]["source"] = "manual"
    payload = response.json()
    availability = _strip_probes(payload)
    assert set(availability) == {"kimi-acp"}
    assert payload == expected
    payload = client.get(REGISTRY_URL).json()
    availability = _strip_probes(payload)
    assert set(availability) == {"kimi-acp"}
    assert payload == expected


def test_validation_rejects_bad_documents(client) -> None:
    document = _document()
    document["agents"].append(document["agents"][0].copy())
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["id"] = "BAD ID"
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["command"] = ""
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["unknown"] = 1
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["unknown"] = 1
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["source"] = "bogus"
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["api_base"] = ""
    assert client.put(REGISTRY_URL, json=document).status_code == 422


def test_api_base_must_be_a_plain_http_url(client) -> None:
    """#158: api_base is the scoped-token egress target — only plain absolute
    http(s) URLs (no credentials, query, or fragment) are accepted."""
    for bad in (
        "ftp://example.com",
        "not-a-url",
        "//example.com/path",
        "https://user:pw@example.com",
        "http://example.com/?x=1",
        "http://example.com/#frag",
    ):
        document = _document()
        document["api_base"] = bad
        assert client.put(REGISTRY_URL, json=document).status_code == 422, bad


def test_external_api_base_accepted_but_logged(client, caplog) -> None:
    """#158: an external api_base is allowed (remote deployments) but loud."""
    document = _document()
    document["api_base"] = "https://studio.example.com"
    with caplog.at_level(logging.WARNING):
        response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    assert any("api_base" in record.message for record in caplog.records)

    caplog.clear()
    document["api_base"] = "http://192.168.1.20:8000"
    with caplog.at_level(logging.WARNING):
        response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    assert not any("api_base" in record.message for record in caplog.records)


def test_get_reports_per_agent_detection_status(client, monkeypatch) -> None:
    _stub_detection(
        monkeypatch,
        {
            "kimi": CatalogDetection(True, "/usr/local/bin/kimi", "kimi, version 0.55.0"),
            "goose": CatalogDetection(False),
        },
    )
    payload = client.get(REGISTRY_URL).json()
    assert payload["detection"] == {
        "kimi": {
            "detected": True,
            "path": "/usr/local/bin/kimi",
            "version": "kimi, version 0.55.0",
        },
        "goose": {"detected": False, "path": None, "version": None},
    }


def test_redetect_merges_detected_catalog_entries(client, monkeypatch) -> None:
    _stub_detection(
        monkeypatch,
        {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", "kimi 0.55.0")},
    )
    response = client.post(REDETECT_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [agent["id"] for agent in payload["agents"]] == ["kimi"]
    kimi = payload["agents"][0]
    assert kimi["source"] == "detected"
    assert kimi["command"] == "kimi" and kimi["args"] == ["acp"]
    # The merge is persisted, not just response sugar.
    persisted = client.get(REGISTRY_URL).json()
    assert [agent["id"] for agent in persisted["agents"]] == ["kimi"]


def test_redetect_never_overrides_manual_entries(client, monkeypatch) -> None:
    document = {
        "api_base": "http://127.0.0.1:8000",
        "agents": [
            {"id": "kimi", "label": "My kimi", "command": "/opt/kimi", "args": ["acp"]},
            {"id": "mine", "label": "Mine", "command": "mine", "args": []},
        ],
    }
    assert client.put(REGISTRY_URL, json=document).status_code == 200
    _stub_detection(
        monkeypatch,
        {
            "kimi": CatalogDetection(True, "/usr/bin/kimi", None),
            "goose": CatalogDetection(True, "/usr/bin/goose", "goose 1.0"),
        },
    )
    payload = client.post(REDETECT_URL).json()
    agents = {agent["id"]: agent for agent in payload["agents"]}
    # Manual kimi row untouched (same id wins over the detected template);
    # catalog goose appended as detected.
    assert agents["kimi"]["command"] == "/opt/kimi"
    assert agents["kimi"]["source"] == "manual"
    assert agents["mine"]["source"] == "manual"
    assert agents["goose"]["source"] == "detected"


def test_put_preserves_detected_source_for_unchanged_rows(client, monkeypatch) -> None:
    _stub_detection(monkeypatch, {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", None)})
    detected = client.post(REDETECT_URL).json()["agents"]
    assert detected[0]["source"] == "detected"
    # An old client re-saves the document without the source field: the
    # unchanged detected row keeps its provenance instead of flipping manual.
    legacy_row = {k: v for k, v in detected[0].items() if k != "source"}
    payload = {"api_base": "http://127.0.0.1:8000", "agents": [legacy_row]}
    saved = client.put(REGISTRY_URL, json=payload).json()
    assert saved["agents"][0]["source"] == "detected"
    # Editing the detected row makes it manual — detection never reclaims it.
    payload["agents"][0]["label"] = "Customized"
    saved = client.put(REGISTRY_URL, json=payload).json()
    assert saved["agents"][0]["source"] == "manual"
    redetected = client.post(REDETECT_URL).json()
    rows = {agent["id"]: agent for agent in redetected["agents"]}
    assert rows["kimi"]["label"] == "Customized"
    assert rows["kimi"]["source"] == "manual"
