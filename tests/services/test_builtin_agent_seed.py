"""Built-in demo agent seed: seed-if-absent startup semantics.

Mirrors the executor seed contract
(``executor_definition_service.seed_builtin_executor_definitions``): the four
demo workflow agents are published when absent, never overwrite an
admin-evolved definition, and never resurrect an archived agent.
"""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.agent_catalog_builtin import (
    BUILTIN_AGENT_DEFINITIONS,
    seed_builtin_agent_definitions,
)
from server.app.services.agent_service import AgentService
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def service(job_db):
    return AgentService(job_db.path)


def test_conftest_seeds_builtin_demo_agents(service) -> None:
    # The conftest seed mirrors create_app: after every reset the four demo
    # agents are published with their capability/runtime/skill wiring.
    assert set(BUILTIN_AGENT_DEFINITIONS) == {
        "example-write-script-v1",
        "example-review-script-v1",
        "example-generate-questions-v1",
        "example-review-questions-v1",
    }
    for agent_id, expected in BUILTIN_AGENT_DEFINITIONS.items():
        published = service.get_published_definition(agent_id)
        assert published is not None, agent_id
        assert published == expected, agent_id
        assert published.runtime == "velites"
        assert published.skill.startswith("education-video-problems-generation/")


def test_seed_is_idempotent(service) -> None:
    assert seed_builtin_agent_definitions(TEST_DATABASE_URL) == []
    versions_before = {
        agent_id: service.get_published(agent_id).version for agent_id in BUILTIN_AGENT_DEFINITIONS
    }
    assert seed_builtin_agent_definitions(TEST_DATABASE_URL) == []
    for agent_id, version in versions_before.items():
        assert service.get_published(agent_id).version == version


def test_seed_does_not_overwrite_admin_evolution(service) -> None:
    evolved = AgentDefinition(
        capability="write_script",
        runtime="velites",
        skill="education-video-problems-generation/write-script",
        tools=("read",),
    )
    service.save_draft("example-write-script-v1", evolved, created_by="admin")
    service.publish("example-write-script-v1")

    assert seed_builtin_agent_definitions(TEST_DATABASE_URL) == []
    assert service.get_published_definition("example-write-script-v1") == evolved


def test_seed_does_not_resurrect_archived_agent(service) -> None:
    assert service.archive_all("example-review-questions-v1") > 0
    assert service.get_published("example-review-questions-v1") is None

    assert seed_builtin_agent_definitions(TEST_DATABASE_URL) == []
    assert service.get_published("example-review-questions-v1") is None
