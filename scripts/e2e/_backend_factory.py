"""Uvicorn factory for the browser smoke backend (Phase 4B main flow).

``server.app.main:create_app`` defaults to ``start_worker=False``, which is
why the original smoke left jobs queued forever. The main-flow spec needs
nodes to really execute, so this factory flips the workflow worker/sweeper
threads on (Host-side code pool + agent dispatch enqueue; the Agent itself
is claimed by the standalone Worker process the runner starts).
"""

from __future__ import annotations

from fastapi import FastAPI

from server.app.main import create_app as _create_app


def create_app() -> FastAPI:
    return _create_app(start_worker=True)
