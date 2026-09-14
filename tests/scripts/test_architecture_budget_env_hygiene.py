"""Guard: the budget-anchor env vars must not leak into the pytest session.

#641: exporting ``AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN=1`` to
simulate a release train and then running pytest silently gutted the
HEAD^-dependent monotonicity self-tests (the anchors collapse to HEAD-only
under the flag). ``tests/conftest.py`` clears the three anchor env vars in
``pytest_configure``; tests that need them set their own via monkeypatch.
This test fails if that cleanup is removed.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.no_db

_ANCHOR_ENV_KEYS = (
    "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN",
    "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW",
    "AGENT_LEGION_BUDGET_BASE",
)


@pytest.mark.parametrize("key", _ANCHOR_ENV_KEYS)
def test_budget_anchor_env_cleared_for_session(key: str) -> None:
    # pytest_configure runs before collection, so by the time any test
    # executes a leaked value is already gone; a present value here means
    # the conftest cleanup was dropped or bypassed.
    assert key not in os.environ


def test_release_train_flag_actually_neutralized(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mechanism check: conftest's cleanup happens once at configure
    # time, so simulate the leak order — set the env, re-import the
    # configure hook, verify it clears the key (not just that the key is
    # absent in this test process).
    monkeypatch.setenv("AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN", "1")
    from tests import conftest

    conftest.pytest_configure()
    assert os.environ.get("AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN") is None
