"""terminal_grant_required instance-setting reader (#1136)."""

from __future__ import annotations

from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.studio_chat.terminal_policy import terminal_grant_required


def test_terminal_grant_required_defaults_on_and_degrades_closed(job_db) -> None:
    # No document / missing key / malformed values all fail closed (fence on).
    assert terminal_grant_required(job_db) is True
    for value in (0, "false", 1, None):
        InstanceSettingsStore(job_db).put({"studio_chat_terminal_grant_required": value})
        assert terminal_grant_required(job_db) is True


def test_terminal_grant_required_honors_explicit_off(job_db) -> None:
    InstanceSettingsStore(job_db).put({"studio_chat_terminal_grant_required": False})
    assert terminal_grant_required(job_db) is False
    InstanceSettingsStore(job_db).put({"studio_chat_terminal_grant_required": True})
    assert terminal_grant_required(job_db) is True
