"""Repo-tracked code nodes executed by the ``code`` executor kind.

Each module exposes a module-level ``run(job, job_dir, runtime)`` with the
same contract as local handlers. Files here are referenced by repo-relative
``path`` entries in ``config/workflow.yaml`` executor capabilities and are
loaded by path inside an isolated child process (EXEC-CODE-001): node code is
always git-reviewed and CI-gated.
"""
