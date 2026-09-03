"""The stateless publish pipeline for one workflow revision (#287).

Why a separate module: publishing is a fixed sequence — serialize the
definition, freeze the node_code_pins snapshot (EXEC-CODE-002), embed it
beside the definition, allocate the next version, derive Agent routes, and
hand the atomic revision + projection write to the JobQueries facade. None
of it touches service state, so it lives as a free function next to the
shared route derivation (workflow_revision_routes.py);
``WorkflowRevisionService`` stays the constructor-holding facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.agent_service import published_agent_definitions
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.node_config import prune_workspace_node_overrides
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workflow_revision_routes import derive_agent_routes
from server.app.services.workflow_revision_runtime import embed_node_code_pins
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def publish_workflow_revision(
    job_db: JobQueries,
    custom_nodes_enabled: bool,
    workspace_id: str,
    definition: WorkflowDefinition,
) -> dict:
    definition_json = serialize_definition(definition)
    # node_code_pins snapshot the published custom code versions at publish
    # time (EXEC-CODE-002, design §4): publish-moment state embedded via
    # embed_node_code_pins — inside definition_json, outside
    # definition_hash. Since #115 they are an audit record and the
    # quality-replay pin source; ordinary jobs dispatch the latest
    # published code instead.
    pins = freeze_node_code_versions(
        job_db,
        custom_nodes_enabled,
        workspace_id,
        definition.key,
        list(definition.executable_nodes),
    )
    stored_json = embed_node_code_pins(definition_json, pins)
    version = job_db.next_workflow_revision_version(workspace_id, definition.key)
    revision_id = f"{workspace_id}:{definition.key}:v{version}"
    agent_routes = derive_agent_routes(job_db, workspace_id, definition)
    revision = job_db.create_workflow_revision(
        revision_id=revision_id,
        workspace_id=workspace_id,
        workflow_key=definition.key,
        version=version,
        status="active",
        definition_json=stored_json,
        definition_hash=definition_hash(definition_json),
        agent_routes=agent_routes,
    )
    # The new revision's schemas are the live truth for the workspace's
    # node overrides: prune keys it no longer accepts so intake keeps
    # working after a schema rename/removal (#428 二轮复审 P2-1).
    prune_workspace_node_overrides(
        job_db,
        workspace_id,
        definition,
        published_agent_definitions(job_db, workspace_id),
    )
    return revision
