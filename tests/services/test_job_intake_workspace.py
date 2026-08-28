import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.job_intake_workspace import (
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
