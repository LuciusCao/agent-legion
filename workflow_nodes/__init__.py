"""Git-reviewed seed sources for the demo workflow's two code nodes.

Since #96 retired the capability ``path`` binding (EXEC-CODE-001 legacy),
these files are no longer executed from the repo: at startup they are
published as global node_code versions (EXEC-CODE-002) and run from the DB
text inside the velites sandbox. Each module exposes a module-level ``run``
(a ``def run(ctx)`` business function decorated with the node SDK's
``@entrypoint``). Changes land exclusively through git review + CI; no
runtime API may create, modify, or delete files here.
"""
