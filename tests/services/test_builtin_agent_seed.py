"""Built-in demo agent seed: workspace-scoped seed-if-absent semantics (v46).

Mirrors the executor seed contract
(``executor_definition_service.seed_builtin_executor_definitions``): the four
demo workflow agents are published into a workspace when absent, never
overwrite an admin-evolved definition, and never resurrect an archived agent.
Nothing is seeded globally — each workspace gets its own copy.
"""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.agent_catalog.builtin import (
    BUILTIN_AGENT_DEFINITIONS,
    seed_demo_workspace_agent_definitions,
)
from server.app.services.agent_service import AgentService
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.workflow_node_skill import node_skill_publish_error
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Seed WS", default_workflow_key="demo_workflow")["id"]


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
    seeded = seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id)
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
    other = job_db.create_workspace("Other WS", default_workflow_key="demo_workflow")["id"]
    seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id)
    assert AgentService(job_db.dsn_identity, other).list_latest() == []


def test_seed_is_idempotent(service, workspace_id) -> None:
    seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id)
    assert seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id) == []
    versions_before = {
        agent_id: service.get_published(agent_id).version for agent_id in BUILTIN_AGENT_DEFINITIONS
    }
    assert seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id) == []
    for agent_id, version in versions_before.items():
        assert service.get_published(agent_id).version == version


def test_seed_does_not_overwrite_admin_evolution(service, workspace_id) -> None:
    seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id)
    evolved = AgentDefinition(
        capability="write_script",
        runtime="velites",
        skill="education-video-problems-generation/write-script",
        tools=("read",),
    )
    service.save_draft("example-write-script-v1", evolved, created_by="admin")
    service.publish("example-write-script-v1")

    assert seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id) == []
    assert service.get_published_definition("example-write-script-v1") == evolved


def test_seed_does_not_resurrect_archived_agent(service, workspace_id) -> None:
    seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id)
    assert service.archive_all("example-review-questions-v1") > 0
    assert service.get_published("example-review-questions-v1") is None

    assert seed_demo_workspace_agent_definitions(TEST_DATABASE_URL, workspace_id) == []
    assert service.get_published("example-review-questions-v1") is None
