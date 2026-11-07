"""Read-side publish advisory contracts after the service-helper split."""

import pytest

from server.app.services.studio_publish_request_advisories import (
    draft_warnings,
    with_current_draft_warnings,
)

pytestmark = pytest.mark.no_db


class _JobDB:
    def __init__(self, draft_yaml: str | None) -> None:
        self._draft_yaml = draft_yaml

    def get_workspace_workflow_draft(self, workspace_id: str):
        assert workspace_id == "ws-1"
        return None if self._draft_yaml is None else {"definition_yaml": self._draft_yaml}


def test_draft_warnings_pass_through_advisory_text_unchanged(monkeypatch) -> None:
    expected = ["Node review binds skill 'group/plain' without a machine-readable contract"]
    monkeypatch.setattr(
        "server.app.services.studio_publish_request_advisories.skill_repo_publish_warnings",
        lambda definition, workspace_id, job_db: expected,
    )

    result = draft_warnings(
        _JobDB(None),
        "ws-1",
        "key: ws-1\n"
        "label: Publish Flow\n"
        "nodes:\n"
        "  review:\n"
        "    type: agent\n"
        "    capability: review\n"
        "    execution: {runtime: velites}\n",
    )

    assert result == expected


def test_current_draft_advisories_preserve_payload_and_missing_draft_shape() -> None:
    payload = {"id": "request-1", "status": "pending"}
    result = with_current_draft_warnings(_JobDB(None), "ws-1", payload)

    assert result == {**payload, "warnings": []}
    assert result is not payload
    assert "warnings" not in payload


def test_unparseable_current_draft_degrades_to_no_advisories() -> None:
    assert draft_warnings(_JobDB(None), "ws-1", "nodes: [") == []
