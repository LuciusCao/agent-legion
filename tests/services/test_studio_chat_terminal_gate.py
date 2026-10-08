"""terminal_grant_required instance-setting reader (#1136)."""

from __future__ import annotations

from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.studio_chat.terminal_policy import terminal_grant_required


def test_terminal_grant_required_defaults_off_and_degrades_off(job_db) -> None:
    # No document / missing key / malformed values all degrade to off — the
    # fence is opt-in (#1136: with it on, engines in auto permission mode can
    # never mint a grant and Bash is dead).
    assert terminal_grant_required(job_db) is False
    for value in (0, "true", 1, None):
        InstanceSettingsStore(job_db).put({"studio_chat_terminal_grant_required": value})
        assert terminal_grant_required(job_db) is False


def test_terminal_grant_required_honors_explicit_on(job_db) -> None:
    InstanceSettingsStore(job_db).put({"studio_chat_terminal_grant_required": True})
    assert terminal_grant_required(job_db) is True
    InstanceSettingsStore(job_db).put({"studio_chat_terminal_grant_required": False})
    assert terminal_grant_required(job_db) is False
