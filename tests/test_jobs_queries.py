from pathlib import Path

from server.app.jobs.queries import JobQueries


def test_upsert_workspace_agent_assignment(tmp_path: Path) -> None:
    db = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    result = db.upsert_workspace_agent_assignment("ws1", "pi", 3)
    assert result["workspace_id"] == "ws1"
    assert result["agent_id"] == "pi"
    assert result["concurrency_limit"] == 3

    # update
    result2 = db.upsert_workspace_agent_assignment("ws1", "pi", 5)
    assert result2["concurrency_limit"] == 5

    # list confirms
    agents = db.list_workspace_agents("ws1")
    assert len(agents) == 1
    assert agents[0]["concurrency_limit"] == 5

    # clamp to minimum of 1
    result3 = db.upsert_workspace_agent_assignment("ws1", "pi", 0)
    assert result3["concurrency_limit"] == 1
