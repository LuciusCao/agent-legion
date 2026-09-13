"""Static guards for the Studio chat authoring bootstrap text.

The bootstrap (``authoring_bootstrap.md``) is prepended to every session's
first prompt, so its wording IS the agent's capability boundary. #593 adds
the external-service-connection rule: the agent has no tool to read, list,
or modify instance-level external connections (STUDIO-AGENT-001 scoped-token
boundary stays), and it must point the human at the admin settings page
instead of claiming connections live in Studio. These tests are pure text
assertions over the shipped resource — no database, no fake agent.
"""

from __future__ import annotations

import re

import pytest

from server.app.studio_chat.prompts import STUDIO_AUTHORING_BOOTSTRAP


def _folded(text: str) -> str:
    """Collapse markdown line wraps so phrase assertions survive rewrapping."""
    return re.sub(r"\s+", " ", text)


@pytest.mark.no_db
def test_bootstrap_states_external_connection_boundary() -> None:
    """The connection rule pins the admin-only location and the no-tool fact."""
    rule_start = STUDIO_AUTHORING_BOOTSTRAP.find(
        "External service connections (what a node config `connection` key"
    )
    assert rule_start != -1, "external-connection rule missing from bootstrap"
    rule = _folded(STUDIO_AUTHORING_BOOTSTRAP[rule_start : rule_start + 900])

    # Admin-only location, both as prose and as the route the Studio UI links.
    assert "Global Settings" in rule
    assert "/admin/settings#connections" in rule
    # The agent itself has no read/list/modify surface for connections.
    assert "no tool to read, list, or modify" in rule
    # The drafting escape hatches: reference an existing key, or send the
    # human to the administrator.
    assert "reference an existing connection key" in rule
    assert "ask an administrator" in rule


@pytest.mark.no_db
def test_bootstrap_forbids_claiming_connections_live_in_studio() -> None:
    """The anti-hallucination clause survives future rewordings."""
    assert "Never claim external service connections are configured in Studio" in _folded(
        STUDIO_AUTHORING_BOOTSTRAP
    )
