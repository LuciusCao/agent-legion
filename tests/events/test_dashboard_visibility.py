"""#881: per-connection visibility on the broadcast dashboard SSE channel."""

from __future__ import annotations

import asyncio
import json

import pytest

from server.app.events import sse
from server.app.events.bus import InProcessEventBus
from server.app.events.dashboard import build_workspace_stats_batch_payload
from server.app.events.dashboard_visibility import DashboardStatsFilter
from server.app.events.sse import JobEventManager

pytestmark = pytest.mark.no_db


def _batch(*ids: str, revision: int = 5) -> str:
    return build_workspace_stats_batch_payload(
        revision, [{"id": ws, "job_stats": {"done": 1}} for ws in ids]
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_filter_keeps_only_visible_entries():
    stats_filter = DashboardStatsFilter(lambda: frozenset(), frozenset({"joined"}))
    out = asyncio.run(stats_filter(_batch("joined", "other")))
    assert out is not None
    assert json.loads(out) == {
        "type": "workspace_stats_batch",
        "latest_revision": 5,
        "workspaces": [{"id": "joined", "job_stats": {"done": 1}}],
    }


def test_filter_drops_batches_without_visible_entries():
    """No empty events: a batch that is entirely invisible is not sent."""
    stats_filter = DashboardStatsFilter(lambda: frozenset(), frozenset({"joined"}))
    assert asyncio.run(stats_filter(_batch("other"))) is None
    nothing_visible = DashboardStatsFilter(lambda: frozenset(), frozenset())
    assert asyncio.run(nothing_visible(_batch("joined", "other"))) is None


def test_filter_fails_closed_on_unknown_payloads():
    stats_filter = DashboardStatsFilter(lambda: frozenset(), frozenset({"joined"}))
    assert asyncio.run(stats_filter(json.dumps({"type": "other", "id": "joined"}))) is None
    assert asyncio.run(stats_filter("not json")) is None
    assert asyncio.run(stats_filter("[]")) is None


def test_filter_caches_visible_set_and_refreshes_after_ttl():
    """Membership is read once per refresh window, never per broadcast; a
    membership change is picked up after the window elapses."""
    clock = _Clock()
    members = {"joined"}
    calls: list[int] = []

    def resolve() -> frozenset[str]:
        calls.append(1)
        return frozenset(members)

    stats_filter = DashboardStatsFilter(
        resolve, frozenset(members), refresh_seconds=30.0, clock=clock
    )
    members.add("other")  # joined mid-connection
    members.discard("joined")  # and removed from the first workspace
    for _ in range(5):
        out = asyncio.run(stats_filter(_batch("joined", "other")))
        assert out is not None and [w["id"] for w in json.loads(out)["workspaces"]] == ["joined"]
    assert calls == []

    clock.now += 30.0
    out = asyncio.run(stats_filter(_batch("joined", "other")))
    assert out is not None and [w["id"] for w in json.loads(out)["workspaces"]] == ["other"]
    assert len(calls) == 1
    asyncio.run(stats_filter(_batch("joined", "other")))
    assert len(calls) == 1


async def _next_frame(body) -> str:
    return await asyncio.wait_for(anext(body), timeout=2.0)


def test_connect_applies_payload_filter_per_connection():
    async def scenario() -> tuple[list[str], list[str]]:
        bus = InProcessEventBus()
        bus.attach_loop(asyncio.get_running_loop())
        manager = JobEventManager(bus)
        restricted = (
            await manager.connect(
                None,  # type: ignore[arg-type]
                "dashboard",
                payload_filter=DashboardStatsFilter(lambda: frozenset(), frozenset({"joined"})),
            )
        ).body_iterator
        unrestricted = (await manager.connect(None, "dashboard")).body_iterator  # type: ignore[arg-type]
        assert await _next_frame(restricted) == ":ok\n\n"
        assert await _next_frame(unrestricted) == ":ok\n\n"
        bus.publish("dashboard", _batch("other", revision=1))
        bus.publish("dashboard", _batch("joined", "other", revision=2))
        got_restricted = [await _next_frame(restricted)]
        got_unrestricted = [await _next_frame(unrestricted), await _next_frame(unrestricted)]
        await restricted.aclose()
        await unrestricted.aclose()
        return got_restricted, got_unrestricted

    got_restricted, got_unrestricted = asyncio.run(scenario())
    payload = json.loads(got_restricted[0].removeprefix("data: "))
    assert payload["latest_revision"] == 2
    assert [w["id"] for w in payload["workspaces"]] == ["joined"]
    assert [
        [w["id"] for w in json.loads(frame.removeprefix("data: "))["workspaces"]]
        for frame in got_unrestricted
    ] == [["other"], ["joined", "other"]]


def test_filtered_out_events_do_not_starve_heartbeat(monkeypatch):
    """The keep-alive clock runs from the last frame sent: a steady stream of
    invisible batches must still produce heartbeats."""
    monkeypatch.setattr(sse, "HEARTBEAT_SECONDS", 0.2)

    async def scenario() -> str:
        bus = InProcessEventBus()
        bus.attach_loop(asyncio.get_running_loop())
        manager = JobEventManager(bus)
        body = (
            await manager.connect(
                None,  # type: ignore[arg-type]
                "dashboard",
                payload_filter=DashboardStatsFilter(lambda: frozenset(), frozenset()),
            )
        ).body_iterator
        assert await _next_frame(body) == ":ok\n\n"

        async def flood() -> None:
            while True:
                bus.publish("dashboard", _batch("other"))
                await asyncio.sleep(0.02)

        flooder = asyncio.create_task(flood())
        try:
            return await _next_frame(body)
        finally:
            flooder.cancel()
            await body.aclose()

    assert asyncio.run(scenario()) == ":heartbeat\n\n"
