"""Unit tests for workspace_libs/code_loader.py (the node module loader).

Since #96 the only loader is source-based (DB-published code text); the
repo-file loader retired with the capability ``path`` binding.
"""

from __future__ import annotations

import textwrap

import pytest

from workspace_libs.code_loader import _load_run_from_source

pytestmark = pytest.mark.no_db


def test_load_run_from_source_returns_run() -> None:
    source = textwrap.dedent(
        """
        def run(job, job_dir, runtime):
            return (job, job_dir, runtime)
        """
    )
    run = _load_run_from_source(source)
    assert callable(run)
    assert run({"id": "j"}, "dir", {"k": 1}) == ({"id": "j"}, "dir", {"k": 1})


def test_load_run_from_source_rejects_missing_run() -> None:
    with pytest.raises(ValueError, match="does not expose a callable 'run'"):
        _load_run_from_source("x = 1\n")


def test_load_run_from_source_rejects_non_callable_run() -> None:
    with pytest.raises(ValueError, match="does not expose a callable 'run'"):
        _load_run_from_source("run = 42\n")


def test_load_run_from_source_propagates_compile_errors() -> None:
    with pytest.raises(SyntaxError):
        _load_run_from_source("def run(:\n")
