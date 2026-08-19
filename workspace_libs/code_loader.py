"""Code node module loader shared by the Host executor and the sandboxed child.

The single contract: DB-published node code text builds a Python module
exposing a module-level ``run(job, job_dir, runtime)`` callable (EXEC-CODE-002;
the legacy repo-file ``path`` loading was retired in #96). Lives in
``workspace_libs`` (zero ``server.app`` imports) so the sandboxed code child
can load node code without pulling in Host-only modules.
"""

from __future__ import annotations

import importlib.util
import sys


def _load_run_from_source(source: str):
    """Build a module from node code text and return its ``run`` callable."""
    spec = importlib.util.spec_from_loader("_code_node_custom", loader=None)
    if spec is None:
        raise ValueError("Cannot build module spec for custom node code")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so self-references resolve: ``@dataclass`` code
    # with ``from __future__ import annotations`` looks the defining module
    # up via ``sys.modules[cls.__module__]`` and crashes when it is absent.
    # Each load builds a fresh module object, so a reload simply replaces the
    # entry; no caller depends on the module being absent from sys.modules.
    sys.modules[module.__name__] = module
    try:
        # dont_inherit=True: this loader's own ``from __future__`` flags must
        # not leak into node code; the node's own future imports still apply.
        exec(compile(source, "<custom_node>", "exec", dont_inherit=True), module.__dict__)
        run = getattr(module, "run", None)
        if not callable(run):
            raise ValueError("Custom node code does not expose a callable 'run'")
    except BaseException:
        # No half-registered state: drop the failed module from sys.modules.
        if sys.modules.get(module.__name__) is module:
            del sys.modules[module.__name__]
        raise
    return run
