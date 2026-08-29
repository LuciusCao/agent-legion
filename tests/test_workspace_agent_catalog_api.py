def test_list_agent_catalog_endpoint(client):
    # Agent 半区是 workspace 作用域（schema v46）：创建绑定 demo workflow 的
    # workspace（绑定时自动 seed demo agent 模板）后按其 scope 读取目录。
    created = client.post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "catalog-ws"},
    )
    assert created.status_code == 200, created.text
    workspace_id = created.json()["workspace"]["id"]
    # v62: creation no longer seeds the factory Agents.
    from tests.helpers import seed_workspace_agent_definitions

    seed_workspace_agent_definitions(workspace_id)
    response = client.get("/api/agent-catalog", params={"workspace_id": workspace_id})
    assert response.status_code == 200
    data = response.json()
    agent = next(item for item in data["agents"] if item["id"] == "example-review-questions-v1")
    assert agent["capability"] == "review_questions"
    # 全局 provider/model/thinking 投影已退役（agent 配置治理 phase 3）：
    # 执行默认走 workflow 顶层 execution 块，catalog 不再携带这些键。
    assert agent["runtime"] == "velites"
    assert agent["provider"] is None
    assert agent["model"] is None
    # P-0.5（schema v47）：executor 半区随概念退役移除。
    assert "executors" not in data


def test_get_configured_skill_detail(client_factory, tmp_path, monkeypatch):
    # The demo skill sources point at machine-local repos created by
    # `make import-demo`; materialize one under a fake HOME so the test is
    # environment-independent.
    import subprocess

    skill_dir = (
        tmp_path
        / "home"
        / ".agents"
        / "skills"
        / "agent-legion"
        / "education-video-problems-generation"
        / "generate-questions"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Generate Questions\n", encoding="utf-8")
    env = {
        **dict(__import__("os").environ),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for args in (
        ("init", "-q"),
        ("add", "."),
        ("commit", "-m", "init", "--no-gpg-sign"),
        ("tag", "v1.0.0"),
    ):
        subprocess.run(
            ["git", "-C", str(skill_dir), *args], check=True, capture_output=True, env=env
        )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    # fresh=True: the skill catalog router resolves HOME at app build time, so
    # the app must be created after the fake HOME is in place.
    with client_factory(fresh=True) as client:
        response = client.get(
            "/api/agent-catalog/skills/education-video-problems-generation/generate-questions"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ref"] == "v1.0.0"
    assert data["available"] is True
    assert data["tags"] == ["v1.0.0"]
    assert any(item["path"] == "SKILL.md" for item in data["files"])


def test_get_workspace_execution_configuration_reports_no_warnings_after_v005(client):
    workspace_response = client.post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "Legacy"},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # The legacy workspace_agent_assignments table is removed by V005, so no
    # migration warnings are produced. P-0.5 (schema v47): allocations and
    # bindings are gone; only node limits remain.
    response = client.get(f"/api/workspaces/{workspace_id}/execution-configuration")
    assert response.status_code == 200
    data = response.json()
    assert data["node_limits"] == []
    assert data["migration_warnings"] == []
