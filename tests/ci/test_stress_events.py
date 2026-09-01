import os

import pytest


@pytest.mark.skipif(
    os.environ.get("AGENT_LEGION_ENABLE_STRESS_EVENTS") == "1",
    reason="stress endpoints already enabled",
)
def test_stress_event_route_not_registered_without_env(client_factory):
    with client_factory(workflows_enabled=True) as client:
        response = client.post(
            "/api/workspaces/ws-stress/events/stress",
            json={"events": [{"job_id": "job1", "kind": "updated"}]},
        )
    assert response.status_code == 404


def test_stress_event_route_records_events_with_env(monkeypatch, client_factory):
    monkeypatch.setenv("AGENT_LEGION_ENABLE_STRESS_EVENTS", "1")
    with client_factory(workflows_enabled=True) as client:
        response = client.post(
            "/api/workspaces/ws-stress/events/stress",
            json={
                "events": [
                    {"job_id": "job1", "kind": "updated"},
                    {"job_id": "job2", "kind": "created"},
                ]
            },
        )
    assert response.status_code == 200
    assert response.json()["recorded"] == 2
