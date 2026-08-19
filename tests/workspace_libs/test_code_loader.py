"""Unit tests for workspace_libs/code_loader.py (the node module loader).

Since #96 the only loader is source-based (DB-published code text); the
repo-file loader retired with the capability ``path`` binding.
"""

from __future__ import annotations

import sys
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


def test_load_run_from_source_supports_dataclass_with_future_annotations() -> None:
    """Node code with ``from __future__ import annotations`` + ``@dataclass``.

    ``dataclasses`` resolves string annotations through
    ``sys.modules[cls.__module__]``; without registration this crashes with
    ``AttributeError: 'NoneType' object has no attribute '__dict__'`` (#94).
    """
    source = textwrap.dedent(
        """
        from __future__ import annotations

        from dataclasses import dataclass, field


        @dataclass
        class Inner:
            value: int = 0


        @dataclass
        class Outer:
            inner: Inner = field(default_factory=Inner)
            items: list[Inner] = field(default_factory=list)


        def run(job, job_dir, runtime):
            outer = Outer(inner=Inner(value=3), items=[Inner(value=4)])
            return outer.inner.value + outer.items[0].value
        """
    )
    run = _load_run_from_source(source)
    assert run({}, "", {}) == 7


def test_load_run_from_source_does_not_inherit_loader_future_flags() -> None:
    # code_loader.py itself carries ``from __future__ import annotations``;
    # without dont_inherit=True the flag would leak into node code, deferring
    # annotation evaluation that the node source never asked for (#94).
    with pytest.raises(NameError):
        _load_run_from_source("def f(x: Undefined) -> None:\n    pass\n")


def test_load_run_from_source_cleans_sys_modules_on_failure() -> None:
    with pytest.raises(SyntaxError):
        _load_run_from_source("def run(:\n")
    assert "_code_node_custom" not in sys.modules

    with pytest.raises(ValueError, match="does not expose a callable 'run'"):
        _load_run_from_source("x = 1\n")
    assert "_code_node_custom" not in sys.modules
