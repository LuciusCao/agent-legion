"""Workflow catalog service (DB-backed since schema v40).

The implementation lives in ``workflow_catalog_store``; this module keeps the
historic import path stable for routes, services, and tests.
"""

from server.app.services.workflow_catalog_store import (
    WorkflowCatalogService,
    seed_builtin_workflow_catalog,
)

__all__ = ["WorkflowCatalogService", "seed_builtin_workflow_catalog"]
