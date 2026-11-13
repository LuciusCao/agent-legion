"""Legacy demo Agent history fixture: workspace-scoped seed-if-absent semantics.

The four demo definitions are published through VersionedEntityStore only when
absent, never overwrite an evolved definition, and never resurrect an archived
Agent. Nothing is seeded globally — each workspace gets its own copy.
"""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.agent_catalog.builtin import BUILTIN_AGENT_DEFINITIONS
from server.app.services.agent_service import AgentService, reset_published_agent_cache
from server.app.services.versioned_entities import VersionedEntityStore
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.workflow_node_skill import node_skill_publish_error
from tests.helpers import (
    publish_agent_definition,
    save_agent_definition_draft,
    seed_workspace_agent_definitions,
)


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Seed WS")["id"]


@pytest.fixture
def service(job_db, workspace_id) -> AgentService:
    return AgentService(job_db.dsn_identity, workspace_id)


def test_seed_publishes_demo_agents_into_the_workspace(service, workspace_id) -> None:
    assert set(BUILTIN_AGENT_DEFINITIONS) == {
        "example-write-script-v1",
        "example-review-script-v1",
        "example-generate-questions-v1",
        "example-review-questions-v1",
    }
    seeded = seed_workspace_agent_definitions(workspace_id)
    assert set(seeded) == set(BUILTIN_AGENT_DEFINITIONS)
    for agent_id, expected in BUILTIN_AGENT_DEFINITIONS.items():
        published = service.get_published_definition(agent_id)
        assert published is not None, agent_id
        assert published == expected, agent_id
        assert published.runtime == "velites"
        # issue #76: the skill binding lives on the demo DAG nodes, not on the
        # Agent definitions (legacy fallback left empty).
        assert published.skill == ""


@pytest.mark.no_db
def test_demo_dag_nodes_carry_the_skill_binding() -> None:
    """The demo DAG declares (key, ref) skill bindings on its four Agent nodes,
    so a revision publish passes the node-skill gate with skill-less Agents."""
    definition = workflow_definition_from_dict(DEMO_WORKFLOW_DEFINITION)
    expected = {
        "write_script": "write-script",
        "review_script": "review-script",
        "generate_questions": "generate-questions",
        "review_questions": "review-questions",
    }
    for node_key, skill_name in expected.items():
        node = definition.nodes[node_key]
        assert node.skill is not None, node_key
        assert node.skill.key == f"education-video-problems-generation/{skill_name}"
        assert node.skill.ref == "v1.0.0"
        assert node_skill_publish_error(node, agent_skill="") is None, node_key


def test_seed_leaves_other_workspaces_empty(job_db, workspace_id) -> None:
    other = job_db.create_workspace("Other WS")["id"]
    seed_workspace_agent_definitions(workspace_id)
    assert AgentService(job_db.dsn_identity, other).list_latest() == []


def test_seed_is_idempotent(service, workspace_id) -> None:
    seed_workspace_agent_definitions(workspace_id)
    assert seed_workspace_agent_definitions(workspace_id) == []
    versions_before = {
        agent_id: service.get_published(agent_id).version for agent_id in BUILTIN_AGENT_DEFINITIONS
    }
    assert seed_workspace_agent_definitions(workspace_id) == []
    for agent_id, version in versions_before.items():
        assert service.get_published(agent_id).version == version


def test_seed_does_not_overwrite_admin_evolution(job_db, service, workspace_id) -> None:
    seed_workspace_agent_definitions(workspace_id)
    evolved = AgentDefinition(
        capability="write_script",
        runtime="velites",
        skill="education-video-problems-generation/write-script",
        tools=("read",),
    )
    saved = save_agent_definition_draft(
        job_db.dsn_identity,
        workspace_id,
        "example-write-script-v1",
        evolved,
        created_by="admin",
    )
    publish_agent_definition(
        job_db.dsn_identity, workspace_id, "example-write-script-v1", saved.definition_hash
    )

    assert seed_workspace_agent_definitions(workspace_id) == []
    assert service.get_published_definition("example-write-script-v1") == evolved


def test_seed_does_not_resurrect_archived_agent(job_db, service, workspace_id) -> None:
    seed_workspace_agent_definitions(workspace_id)
    store = VersionedEntityStore(job_db.dsn_identity, "agent")
    assert store.archive_all("example-review-questions-v1", workspace_id) > 0
    reset_published_agent_cache()
    assert service.get_published("example-review-questions-v1") is None

    assert seed_workspace_agent_definitions(workspace_id) == []
    assert service.get_published("example-review-questions-v1") is None
