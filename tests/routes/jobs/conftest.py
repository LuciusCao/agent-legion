"""Shared fixtures for the external artifact-access route tests (#631),
split from test_external_artifacts.py (#779 codex train review P1-3).
"""

from __future__ import annotations

import pytest

from tests.fakes.storage import FakeObjectStorage
from tests.routes.jobs.external_artifact_testlib import _create_job, _seed_workspace


@pytest.fixture
def two_workspaces(client_factory, monkeypatch):
    """ws-a and ws-b with one job each, object storage enabled via the
    shared store instance — the cross-workspace probe setup."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", FakeObjectStorage())
        _seed_workspace(c, "ws-a")
        _seed_workspace(c, "ws-b")
        job_a = _create_job(c, "ws-a")
        job_b = _create_job(c, "ws-b")
        yield c, job_a, job_b
