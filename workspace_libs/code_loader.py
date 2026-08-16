"""Code node module loader shared by the Host executor and the sandboxed child.

The single contract: DB-published node code text builds a Python module
exposing a module-level ``run(job, job_dir, runtime)`` callable (EXEC-CODE-002;
the legacy repo-file ``path`` loading was retired in #96). Lives in
``workspace_libs`` (zero ``server.app`` imports) so the sandboxed code child
can load node code without pulling in Host-only modules.
"""

from __future__ import annotations

import importlib.util


def _load_run_from_source(source: str):
    """Build a module from node code text and return its ``run`` callable."""
    spec = importlib.util.spec_from_loader("_code_node_custom", loader=None)
    if spec is None:
        raise ValueError("Cannot build module spec for custom node code")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, "<custom_node>", "exec"), module.__dict__)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError("Custom node code does not expose a callable 'run'")
    return run
