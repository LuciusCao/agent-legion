"""Route-registration equivalence pins for the runs intake helper split."""

import pytest

from server.app.auth.api_intake import require_workspace_api_intake
from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_TAG
from server.app.routes.run_contracts import RunBatchResponse
from server.app.routes.runs import create_runs_router

pytestmark = pytest.mark.no_db


def test_runs_helper_split_preserves_route_surface() -> None:
    routes = list(create_runs_router(None).routes)  # type: ignore[arg-type]

    assert [(route.name, route.path) for route in routes] == [
        ("create_run", "/workspaces/{workspace_id}/runs"),
        ("list_runs", "/workspaces/{workspace_id}/runs"),
        ("get_run", "/workspaces/{workspace_id}/runs/{run_id}"),
        ("pause_run_batch", "/workspaces/{workspace_id}/runs/{run_id}/pause"),
        ("resume_run_batch", "/workspaces/{workspace_id}/runs/{run_id}/resume"),
        ("cancel_run_batch", "/workspaces/{workspace_id}/runs/{run_id}/cancel"),
    ]
    assert all(API_SCOPE_INTAKE_TAG in route.tags for route in routes)


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_batch_controls_keep_post_guard_and_response_model(action: str) -> None:
    routes = {route.name: route for route in create_runs_router(None).routes}  # type: ignore[arg-type]
    route = routes[f"{action}_run_batch"]

    assert route.methods == {"POST"}
    assert route.response_model is RunBatchResponse
    assert any(
        dependency.call is require_workspace_api_intake
        for dependency in route.dependant.dependencies
    )
