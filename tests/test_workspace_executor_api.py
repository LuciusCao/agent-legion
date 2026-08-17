def test_list_executors_endpoint(client):
    # Agent 半区是 workspace 作用域（schema v46）：创建绑定 demo workflow 的
    # workspace（绑定时自动 seed demo agent 模板）后按其 scope 读取目录。
    created = client.post(
        "/api/workspaces",
        json={"name": "catalog-ws", "default_workflow_key": "education_video_problems_generation"},
    )
    assert created.status_code == 200, created.text
    workspace_id = created.json()["workspace"]["id"]
    response = client.get("/api/executors", params={"workspace_id": workspace_id})
    assert response.status_code == 200
    data = response.json()
    agent = next(item for item in data["agents"] if item["id"] == "example-review-questions-v1")
    assert agent["capability"] == "review_questions"
    # 全局 provider/model/thinking 投影已退役（agent 配置治理 phase 3）：
    # 执行默认走 workspace agentDefaults，catalog 不再携带这些键。
    assert agent["runtime"] == "velites"
    assert agent["provider"] is None
    assert agent["model"] is None
    executors = {item["id"]: item for item in data["executors"]}
    code_executor = executors["code-default"]
    assert code_executor["kind"] == "code"
    assert code_executor["global_capacity"] == 16
    assert code_executor["capabilities"] == [
        "intake_knowledge_points",
        "publish_content",
    ]
    assert {
        "name": "intake_knowledge_points",
        "skill": None,
        "tools": [],
        "provider": None,
        "model": None,
        "thinking": None,
        "skill_ref": None,
        "skill_commit": None,
    } in code_executor["capability_details"]


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
            "/api/executors/skills/education-video-problems-generation/generate-questions"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ref"] == "v1.0.0"
    assert data["available"] is True
    assert any(item["path"] == "SKILL.md" for item in data["files"])


def test_get_workspace_executor_configuration_reports_no_warnings_after_v005(client):
    workspace_response = client.post(
        "/api/workspaces",
        json={"name": "Legacy", "default_workflow_key": "education_video_problems_generation"},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # The legacy workspace_agent_assignments table is removed by V005, so no
    # migration warnings are produced.
    response = client.get(f"/api/workspaces/{workspace_id}/executor-configuration")
    assert response.status_code == 200
    data = response.json()
    assert data["allocations"] == []
    assert data["bindings"] == []
    assert data["node_limits"] == []
    assert data["migration_warnings"] == []
