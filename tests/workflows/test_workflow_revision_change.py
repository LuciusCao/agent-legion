"""Revision classification for the optional agent capability label (#936)."""

from __future__ import annotations

import pytest

from server.app.services.workflow_revision_change import revision_structurally_changed
from server.app.workflows.definition import workflow_definition_from_mapping

pytestmark = pytest.mark.no_db


def _definition(capability: str | None = None):
    node = {
        "type": "agent",
        "execution": {"runtime": "velites"},
        "skill": {"key": "capability_optional/draft"},
    }
    if capability is not None:
        node["capability"] = capability
    return workflow_definition_from_mapping(
        {"key": "ws", "label": "Workspace", "nodes": {"draft": node}}
    )


def test_omitted_and_explicit_empty_capability_do_not_create_ghost_revision() -> None:
    assert revision_structurally_changed(_definition(), _definition("")) is False


def test_changing_a_real_capability_label_remains_structural() -> None:
    assert revision_structurally_changed(_definition(""), _definition("generate")) is True
    assert revision_structurally_changed(_definition("generate"), _definition("")) is True
