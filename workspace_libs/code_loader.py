"""Code node module loaders shared by Host executor and sandboxed child.

Both loaders enforce the same contract: a Python module exposing a
module-level ``run(job, job_dir, runtime)`` callable. Lives in
``workspace_libs`` (zero ``server.app`` imports) so the sandboxed custom
code child can load node code without pulling in Host-only modules.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_run_callable(file_path: str, repo_root: str):
    """Import a code file by path and return its module-level ``run`` callable."""
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    path = Path(file_path)
    module_name = f"_code_node_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load code node module from {file_path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError(f"Code node {file_path!r} does not expose a callable 'run'")
    return run


def _load_run_from_source(source: str):
    """Build a module from custom code text and return its ``run`` callable."""
    spec = importlib.util.spec_from_loader("_code_node_custom", loader=None)
    if spec is None:
        raise ValueError("Cannot build module spec for custom node code")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, "<custom_node>", "exec"), module.__dict__)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError("Custom node code does not expose a callable 'run'")
    return run
