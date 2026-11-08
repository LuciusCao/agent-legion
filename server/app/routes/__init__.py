from __future__ import annotations

from fastapi import APIRouter

from .assembly import assemble_router
from .deps import RouterDeps


def create_router(deps: RouterDeps) -> APIRouter:
    return assemble_router(deps)
