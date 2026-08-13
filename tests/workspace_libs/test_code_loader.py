"""Unit tests for workspace_libs/code_loader.py (the node module loaders)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from workspace_libs.code_loader import _load_run_callable, _load_run_from_source

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


def test_load_run_callable_loads_file(tmp_path: Path) -> None:
    node = tmp_path / "node_ok.py"
    node.write_text(
        "def run(job, job_dir, runtime):\n    (job_dir / 'out.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    run = _load_run_callable(str(node), str(tmp_path))
    assert callable(run)
    run({}, tmp_path, {})
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"


def test_load_run_callable_rejects_missing_run(tmp_path: Path) -> None:
    node = tmp_path / "node_bad.py"
    node.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not expose a callable 'run'"):
        _load_run_callable(str(node), str(tmp_path))
