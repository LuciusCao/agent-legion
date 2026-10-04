"""#914/#885: the SSE keep-alive is a data-carrying ``heartbeat`` event."""

from __future__ import annotations

import asyncio
import json

import pytest

from server.app.events import sse
from server.app.events.bus import InProcessEventBus, workspace_channel
from server.app.events.sse import JobEventManager

pytestmark = pytest.mark.no_db


def _parse_frame(frame: str) -> tuple[str | None, str]:
    """Parse one SSE frame the way EventSource does (event name + data)."""
    assert frame.endswith("\n\n")
    event_type: str | None = None
    data_lines: list[str] = []
    for line in frame[:-2].split("\n"):
        assert not line.startswith(":"), "heartbeat must not be an SSE comment"
        field, _, value = line.partition(": ")
        if field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)
    return event_type, "\n".join(data_lines)


def test_heartbeat_frame_is_named_event_advertising_interval(monkeypatch):
    monkeypatch.setattr(sse, "HEARTBEAT_SECONDS", 15.0)
    event_type, data = _parse_frame(sse.heartbeat_frame())
    # Named event: EventSource's onmessage (old frontends) never sees it.
    assert event_type == "heartbeat"
    payload = json.loads(data)
    assert payload == {"interval_ms": 15000}
    # Line-based clients (scripts/) dispatch on ``type``; heartbeat has none.
    assert "type" not in payload


def test_workspace_stream_sends_heartbeat_on_open_and_periodically(monkeypatch):
    monkeypatch.setattr(sse, "HEARTBEAT_SECONDS", 0.1)

    async def scenario() -> list[str]:
        bus = InProcessEventBus()
        bus.attach_loop(asyncio.get_running_loop())
        manager = JobEventManager(bus)
        response = await manager.connect(None, workspace_channel("ws1"))  # type: ignore[arg-type]
        body = response.body_iterator
        frames = [await asyncio.wait_for(anext(body), timeout=2.0) for _ in range(3)]
        bus.publish(workspace_channel("ws1"), '{"type":"job_updated"}')
        frames.append(await asyncio.wait_for(anext(body), timeout=2.0))
        await body.aclose()
        return frames

    frames = asyncio.run(scenario())
    assert frames[0] == ":ok\n\n"
    assert frames[1] == sse.heartbeat_frame()  # arms the client watchdog at once
    assert frames[2] == sse.heartbeat_frame()  # periodic beat on a quiet stream
    # Ordinary payloads stay unnamed ``message`` events (onmessage contract).
    assert frames[3] == 'data: {"type":"job_updated"}\n\n'
