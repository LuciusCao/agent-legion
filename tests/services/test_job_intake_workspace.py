from types import SimpleNamespace

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.job_intake_workspace import (
    effective_cms_config,
    enabled_intake_modes,
    get_workspace,
    singular_field_name,
)


class _FakeJobDb:
    def __init__(self, workspace: dict | None) -> None:
        self._workspace = workspace

    def get_workspace(self, workspace_id: str) -> dict | None:
        return self._workspace


def test_get_workspace_returns_workspace():
    workspace = {"id": "ws-1", "name": "Test"}
    assert get_workspace(_FakeJobDb(workspace), "ws-1") is workspace


def test_get_workspace_raises_not_found():
    with pytest.raises(NotFoundError):
        get_workspace(_FakeJobDb(None), "missing")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("entities", "entity"),
        ("videos", "video"),
        ("batches", "batche"),
        ("box", "box"),
    ],
)
def test_singular_field_name(value, expected):
    assert singular_field_name(value) == expected


def test_effective_cms_config_merges_workspace_overrides():
    settings = SimpleNamespace(config={"cms": {"api_url": "https://base", "limit": 10}})
    workspace = {"cms_config": {"limit": 5}}
    assert effective_cms_config(settings, workspace) == {
        "api_url": "https://base",
        "limit": 5,
    }


def test_effective_cms_config_tolerates_non_dict_base():
    settings = SimpleNamespace(config={"cms": "not-a-dict"})
    assert effective_cms_config(settings, {}) == {}


def test_enabled_intake_modes_returns_none_without_config():
    assert enabled_intake_modes({}) is None
    assert enabled_intake_modes({"intake_config": "not-a-dict"}) is None
    assert enabled_intake_modes({"intake_config": {"enabled_modes": "not-a-list"}}) is None


def test_enabled_intake_modes_returns_mode_set():
    workspace = {"intake_config": {"enabled_modes": ["single", "batch"]}}
    assert enabled_intake_modes(workspace) == {"single", "batch"}
