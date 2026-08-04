import json

from server.app.events.dashboard import (
    broadcast_workspace_stats_batch,
    build_workspace_stats_batch_payload,
)


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str) -> None:
        self.published.append((topic, payload))


def test_build_workspace_stats_batch_payload_structure():
    payload = build_workspace_stats_batch_payload(7, [{"workspace_id": "ws-1"}])
    assert json.loads(payload) == {
        "type": "workspace_stats_batch",
        "latest_revision": 7,
        "workspaces": [{"workspace_id": "ws-1"}],
    }


def test_broadcast_publishes_to_dashboard_topic():
    bus = _FakeBus()
    broadcast_workspace_stats_batch(bus, 3, [{"workspace_id": "ws-1"}])
    assert len(bus.published) == 1
    topic, payload = bus.published[0]
    assert topic == "dashboard"
    assert json.loads(payload)["latest_revision"] == 3


def test_broadcast_skips_empty_stats():
    bus = _FakeBus()
    broadcast_workspace_stats_batch(bus, 3, [])
    assert bus.published == []
