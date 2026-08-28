import pytest

from server.app.services.agent_catalog_projection import AgentCatalogService
from tests.helpers import seed_workspace_agent_definitions


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Catalog WS", default_workflow_key="demo_workflow")["id"]


@pytest.fixture
def service(job_db, settings, agent_manager):
    return AgentCatalogService(settings)


def test_catalog_has_no_executors_half(service: AgentCatalogService, workspace_id: str) -> None:
    """P-0.5（schema v47）：executor 概念退役，catalog 只剩 Agent 半边。"""
    result = service.catalog(workspace_id)
    assert set(result) == {"agents"}


def test_catalog_exposes_published_agent_definitions(
    service: AgentCatalogService, workspace_id: str
) -> None:
    # Agent 目录是 workspace 作用域（schema v46）：把 demo 模板播进本 workspace。
    seed_workspace_agent_definitions(workspace_id)
    result = service.catalog(workspace_id)
    agents_by_id = {agent["id"]: agent for agent in result["agents"]}

    agent = agents_by_id["example-review-questions-v1"]
    assert agent["runtime"] == "velites"
    assert agent["capability"] == "review_questions"
    assert agent["skill"] == "education-video-problems-generation/review-questions"
    assert agent["tools"] == ["read", "write", "bash"]
    # 全局 provider/model/thinking 投影已退役：执行默认走 workspace agentDefaults。
    assert "provider" not in agent
    assert "model" not in agent
    assert "thinking" not in agent
