"""Agent session config surface (#368): capture, whitelist, switching, updates.

Covers the session/new modes+configOptions capture, the server-side whitelist
(out-of-list modeId/configId/value never reaches the agent process), the
set_mode / set_config_option round-trips through the fake agent, and the
notification-driven mirror rewrites (current_mode_update / config_option_update).
Shared scripts, the RecordingBus and the ``chat`` fixture follow the sibling
convention of the studio chat service test split (#207).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.session_config import set_session_config_option, set_session_mode
from tests.helpers import wait_for_predicate
from tests.postgres_support import TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"

# kimi-shaped fixture (issue #368 survey): modes double-channel + a
# thought_level select with a gapped ladder (low/high/max, no medium).
KIMI_MODES = {
    "currentModeId": "default",
    "availableModes": [
        {"id": "default", "name": "Default"},
        {"id": "plan", "name": "Plan"},
        {"id": "yolo", "name": "Yolo"},
    ],
}
KIMI_CONFIG_OPTIONS = [
    {
        "id": "model",
        "name": "Model",
        "category": "model",
        "type": "select",
        "currentValue": "k3",
        "options": [
            {"value": "k3", "name": "K3"},
            {"value": "k3-256k", "name": "K3 256k"},
        ],
    },
    {
        "id": "thinking",
        "name": "Thinking",
        "category": "thought_level",
        "type": "select",
        "currentValue": "high",
        "options": [
            {"value": "low", "name": "Low"},
            {"value": "high", "name": "High"},
            {"value": "max", "name": "Max"},
        ],
    },
]

CONFIG_SCRIPT = {
    "capabilities": {"loadSession": False},
    "modes": KIMI_MODES,
    "config_options": KIMI_CONFIG_OPTIONS,
    "on_prompt": [],
}

BARE_SCRIPT = {"capabilities": {"loadSession": False}, "on_prompt": []}


class RecordingBus:
    """EventBus stand-in capturing published (channel, payload) pairs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def attach_loop(self, loop) -> None:
        del loop

    def publish(self, channel: str, payload: str) -> None:
        self.events.append((channel, json.loads(payload)))

    def subscribe(self, channel: str):
        raise NotImplementedError

    def unsubscribe(self, channel: str, queue) -> None:
        del channel, queue


@pytest.fixture
def chat(job_db, settings, tmp_path):
    bus = RecordingBus()
    service = StudioChatService(job_db, settings, bus)
    store = StudioAgentRegistryStore(TEST_DATABASE_URL)

    def register(script: dict, agent_id: str = "fake-agent") -> Path:
        script_path = tmp_path / f"{agent_id}-script.json"
        script_path.write_text(json.dumps(script), encoding="utf-8")
        store.put(
            {
                "api_base": "http://127.0.0.1:8000",
                "agents": [
                    {
                        "id": agent_id,
                        "label": "Fake Agent",
                        "command": sys.executable,
                        "args": [str(FAKE_AGENT), str(script_path)],
                    }
                ],
            }
        )
        return script_path

    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="Chat WS")[
        "id"
    ]
    user_id = str(job_db.create_user("chat-user", password_hash=None)["id"])
    yield service, bus, register, workspace_id, user_id
    service.shutdown()


def _read_sink(script_path: Path) -> list[dict]:
    sink = Path(str(script_path) + ".sink.jsonl")
    if not sink.exists():
        return []
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]


def _sunk_requests(script_path: Path, method: str) -> list[dict]:
    return [
        entry["received"]
        for entry in _read_sink(script_path)
        if entry.get("received", {}).get("method") == method
    ]


# -- capture at session establishment ------------------------------------


def test_session_new_captures_modes_and_config_options(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    assert session["session_modes"] == KIMI_MODES
    assert session["config_options"] == KIMI_CONFIG_OPTIONS
    assert session["capability_snapshot"]["sessionModes"] is True
    assert session["capability_snapshot"]["sessionConfigOptions"] is True


def test_non_advertising_agent_leaves_mirrors_null(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(BARE_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    assert session["session_modes"] is None
    assert session["config_options"] is None
    assert session["capability_snapshot"]["sessionModes"] is False
    assert session["capability_snapshot"]["sessionConfigOptions"] is False


def test_initialize_declares_session_config_option_capability(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(BARE_SCRIPT)
    service.create_session(workspace_id, user_id, "fake-agent")

    initialize_params = next(
        entry["initialize_params"]
        for entry in _read_sink(script_path)
        if "initialize_params" in entry
    )
    session_caps = initialize_params["clientCapabilities"]["session"]
    # select support is advertised by the (empty) configOptions object;
    # boolean stays undeclared (one-phase scope, #368).
    assert session_caps["configOptions"] is not None
    assert not session_caps["configOptions"].get("boolean")


# -- set_session_mode ------------------------------------------------------


def test_set_session_mode_switches_and_mirrors(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    updated = set_session_mode(service, session["id"], workspace_id, "plan")

    assert updated["session_modes"]["currentModeId"] == "plan"
    wait_for_predicate(lambda: len(_sunk_requests(script_path, "session/set_mode")) == 1)
    request = _sunk_requests(script_path, "session/set_mode")[0]
    assert request["params"]["modeId"] == "plan"
    # The mirror is durably in the row, not only in the return value.
    assert service.get_session(session["id"])["session_modes"]["currentModeId"] == "plan"


def test_set_session_mode_rejects_out_of_list_id(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    with pytest.raises(InvalidOperationError):
        set_session_mode(service, session["id"], workspace_id, "rm -rf")

    # The whitelist rejection happens before the agent boundary: no
    # session/set_mode request may reach the subprocess.
    assert _sunk_requests(script_path, "session/set_mode") == []


def test_set_session_mode_without_advertised_modes_rejects(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(BARE_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    with pytest.raises(InvalidOperationError):
        set_session_mode(service, session["id"], workspace_id, "default")


def test_set_session_mode_agent_refusal_maps_to_conflict(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register({**CONFIG_SCRIPT, "set_mode_error": True})
    session = service.create_session(workspace_id, user_id, "fake-agent")

    with pytest.raises(ConflictError):
        set_session_mode(service, session["id"], workspace_id, "plan")

    # A refused switch must not move the mirror.
    current = service.get_session(session["id"])
    assert current["session_modes"]["currentModeId"] == "default"


def test_set_session_mode_on_closed_session_conflicts(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.close_session(session["id"], workspace_id)

    with pytest.raises(ConflictError):
        set_session_mode(service, session["id"], workspace_id, "plan")


# -- set_session_config_option --------------------------------------------


def test_set_config_option_switches_and_mirrors_full_state(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    updated = set_session_config_option(service, session["id"], workspace_id, "thinking", "max")

    by_id = {option["id"]: option for option in updated["config_options"]}
    assert by_id["thinking"]["currentValue"] == "max"
    # The response carries the FULL state: untouched options survive verbatim.
    assert by_id["model"]["currentValue"] == "k3"
    request = _sunk_requests(script_path, "session/set_config_option")[0]
    assert request["params"]["configId"] == "thinking"
    assert request["params"]["value"] == "max"


def test_set_config_option_bare_response_conflicts_and_keeps_mirror(chat) -> None:
    # configOptions is a REQUIRED response field: an agent that omits it
    # (protocol violation) fails SDK validation, which must surface as a 409
    # and leave the mirror untouched — never a silent local fold-in.
    service, _bus, register, workspace_id, user_id = chat
    register({**CONFIG_SCRIPT, "bare_set_config_response": True})
    session = service.create_session(workspace_id, user_id, "fake-agent")

    with pytest.raises(ConflictError):
        set_session_config_option(service, session["id"], workspace_id, "thinking", "max")

    refreshed = service.get_session(session["id"])
    by_id = {option["id"]: option for option in refreshed["config_options"]}
    assert by_id["thinking"]["currentValue"] == "high"


def test_set_config_option_rejects_unknown_id_and_value(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    with pytest.raises(InvalidOperationError):
        set_session_config_option(service, session["id"], workspace_id, "nope", "max")
    with pytest.raises(InvalidOperationError):
        set_session_config_option(service, session["id"], workspace_id, "thinking", "medium")

    assert _sunk_requests(script_path, "session/set_config_option") == []


def test_set_config_option_rejects_boolean_entries(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    boolean_options = [
        {"id": "verbose", "name": "Verbose", "type": "boolean", "currentValue": False}
    ]
    register({**BARE_SCRIPT, "config_options": boolean_options})
    session = service.create_session(workspace_id, user_id, "fake-agent")

    with pytest.raises(InvalidOperationError):
        set_session_config_option(service, session["id"], workspace_id, "verbose", "true")


# -- notification-driven mirror rewrites -----------------------------------


def test_current_mode_update_notification_rewrites_mirror(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    service._on_update(
        session["id"], {"sessionUpdate": "current_mode_update", "currentModeId": "yolo"}
    )

    current = service.get_session(session["id"])
    assert current["session_modes"]["currentModeId"] == "yolo"
    # The available list is preserved — the notification only moves the cursor.
    assert current["session_modes"]["availableModes"] == KIMI_MODES["availableModes"]


def test_config_option_update_notification_replaces_full_state(chat) -> None:
    """codex scenario: a model switch shifts the supported thought levels, so
    the notification's FULL state replaces the mirror wholesale."""
    service, bus, register, workspace_id, user_id = chat
    register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    shifted = [
        {
            "id": "thinking",
            "name": "Thinking",
            "category": "thought_level",
            "type": "select",
            "currentValue": "medium",
            "options": [{"value": "medium", "name": "Medium"}, {"value": "high", "name": "High"}],
        }
    ]
    service._on_update(
        session["id"], {"sessionUpdate": "config_option_update", "configOptions": shifted}
    )

    current = service.get_session(session["id"])
    assert current["config_options"] == shifted
    # The rewrite is pushed to SSE subscribers so controls recalculate.
    session_events = [
        payload
        for _channel, payload in bus.events
        if payload.get("type") == "session" and payload.get("session_id") == session["id"]
    ]
    assert session_events, "config_option_update must publish the session"


def test_mode_update_for_non_advertising_agent_is_dropped(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(BARE_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    service._on_update(
        session["id"], {"sessionUpdate": "current_mode_update", "currentModeId": "ghost"}
    )

    assert service.get_session(session["id"])["session_modes"] is None


# -- PR #393 review batch ---------------------------------------------------

GROUPED_SCRIPT = {
    "capabilities": {"loadSession": False},
    "config_options": [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": "k3",
            "options": [
                {
                    "group": "kimi",
                    "name": "Kimi",
                    "options": [
                        {"value": "k3", "name": "K3"},
                        {"value": "k3-256k", "name": "K3 256k"},
                    ],
                }
            ],
        }
    ],
    "on_prompt": [],
}


def test_grouped_options_whitelist_accepts_nested_values(chat) -> None:
    # ``options`` is a protocol union: flat values OR groups. Grouped entries
    # must contribute their nested values to the whitelist — and never a
    # bogus top-level "None" (PR #393 review, P1).
    service, _bus, register, workspace_id, user_id = chat
    register(GROUPED_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    updated = set_session_config_option(service, session["id"], workspace_id, "model", "k3-256k")

    by_id = {option["id"]: option for option in updated["config_options"]}
    assert by_id["model"]["currentValue"] == "k3-256k"
    with pytest.raises(InvalidOperationError):
        set_session_config_option(service, session["id"], workspace_id, "model", "None")


def test_set_config_option_mirror_takes_agent_response_not_request(chat) -> None:
    # The agent has the last word on values (clamp / linked options): the
    # mirror takes the RESPONSE's currentValue, never echoes the request.
    clamped = [
        KIMI_CONFIG_OPTIONS[0],
        {**KIMI_CONFIG_OPTIONS[1], "currentValue": "high"},
    ]
    service, _bus, register, workspace_id, user_id = chat
    register({**CONFIG_SCRIPT, "set_config_response": clamped})
    session = service.create_session(workspace_id, user_id, "fake-agent")

    updated = set_session_config_option(service, session["id"], workspace_id, "thinking", "max")

    by_id = {option["id"]: option for option in updated["config_options"]}
    assert by_id["thinking"]["currentValue"] == "high"


def test_notifications_over_the_wire_rewrite_mirrors(chat) -> None:
    # Full wire path (PR #393 review): fake agent notification → SDK schema
    # validation → _ClientImpl dump → apply_config_update. Pins that the
    # SDK's CurrentModeUpdate / ConfigOptionUpdate classes exist in the
    # locked SDK version and their dumped key names (currentModeId /
    # configOptions) match what the mirror rewrites read.
    shifted = [
        {
            "id": "thinking",
            "name": "Thinking",
            "category": "thought_level",
            "type": "select",
            "currentValue": "medium",
            "options": [{"value": "medium", "name": "Medium"}, {"value": "high", "name": "High"}],
        }
    ]
    script = {
        **CONFIG_SCRIPT,
        "on_prompt": [
            {"notify": {"sessionUpdate": "current_mode_update", "currentModeId": "plan"}},
            {"notify": {"sessionUpdate": "config_option_update", "configOptions": shifted}},
        ],
    }
    service, _bus, register, workspace_id, user_id = chat
    register(script)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    service.send_message(session["id"], workspace_id, "go")

    wait_for_predicate(lambda: service.get_session(session["id"])["config_options"] == shifted)
    assert service.get_session(session["id"])["session_modes"]["currentModeId"] == "plan"


def test_mode_notification_outside_advertised_list_is_dropped(chat) -> None:
    # The mirror doubles as the whitelist data source: a currentModeId the
    # agent never advertised must not be persisted (PR #393 review, P2) —
    # logged and dropped instead.
    service, _bus, register, workspace_id, user_id = chat
    register(CONFIG_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")

    service._on_update(
        session["id"], {"sessionUpdate": "current_mode_update", "currentModeId": "rm -rf"}
    )

    assert service.get_session(session["id"])["session_modes"]["currentModeId"] == "default"
