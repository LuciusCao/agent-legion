import json
import subprocess
from pathlib import Path

import pytest

from server.app.pipeline.runners import (
    RunnerPool,
    _build_agent_command,
    build_openclaw_runner,
    build_openclaw_runners,
    discover_openclaw_agents,
    list_openclaw_agents,
)
from server.app.settings import load_settings


def test_list_openclaw_agents_success(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps([{"id": "main"}, {"id": "aux"}]), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    agents = list_openclaw_agents()
    assert agents == [{"id": "main"}, {"id": "aux"}]


def test_list_openclaw_agents_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_openclaw_agents() == []


def test_list_openclaw_agents_invalid_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_openclaw_agents() == []


def test_list_openclaw_agents_exception(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("no openclaw")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_openclaw_agents() == []


def test_discover_openclaw_agents(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps([{"id": "a"}, {"id": "b"}]), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert discover_openclaw_agents() == ["a", "b"]


def test_build_agent_command_replaces_agent():
    base = ["openclaw", "--agent", "main", "--json"]
    result = _build_agent_command(base, "aux")
    assert result == ["openclaw", "--agent", "aux", "--json"]


def test_build_agent_command_no_agent_unchanged():
    base = ["openclaw", "--json"]
    result = _build_agent_command(base, "aux")
    assert result == base


def test_build_openclaw_runners_with_runners_config(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "runners": [
            {"command_template": ["openclaw", "--agent", "r1"]},
            {"command_template": ["openclaw", "--agent", "r2"]},
        ]
    }
    runners = build_openclaw_runners(settings)
    assert len(runners) == 2
    assert runners[0].agent_id == "r1"
    assert runners[1].agent_id == "r2"


def test_build_openclaw_runners_with_runners_config_count(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "runners": [
            {"command_template": ["openclaw", "--agent", "r1"], "count": 3},
            {"command_template": ["openclaw", "--agent", "r2"], "count": 2},
        ]
    }
    runners = build_openclaw_runners(settings)
    assert len(runners) == 5
    assert runners[0].agent_id == "r1"
    assert runners[1].agent_id == "r1"
    assert runners[2].agent_id == "r1"
    assert runners[3].agent_id == "r2"
    assert runners[4].agent_id == "r2"
    # 每个 runner 应该是独立实例（模板列表互不影响）
    runners[0].command_template.append("--extra")
    assert "--extra" not in runners[1].command_template


def test_build_openclaw_runners_with_discovered_agents(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([{"id": "agent1"}]), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {"command_template": ["openclaw", "--agent", "main"]}
    runners = build_openclaw_runners(settings)
    assert len(runners) == 1
    assert runners[0].agent_id == "agent1"


def test_build_openclaw_runners_without_agent_flag(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {"command_template": ["openclaw", "--json"]}
    runners = build_openclaw_runners(settings)
    assert len(runners) == 1
    assert runners[0].agent_id == ""


def test_runner_pool_size_and_acquire():
    from server.app.pipeline.openclaw import OpenClawRunner

    runner = OpenClawRunner(command_template=["cmd"], cwd=Path("."), timeout_seconds=60)
    pool = RunnerPool([runner])
    assert pool.size() == 1
    idx, acquired = pool.acquire()
    assert idx == 0
    assert acquired is runner
    with pytest.raises(RuntimeError, match="No free runner"):
        pool.acquire()
    pool.release(idx)
    idx2, acquired2 = pool.acquire()
    assert idx2 == 0


def test_runner_pool_all_runners():
    from server.app.pipeline.openclaw import OpenClawRunner

    runner = OpenClawRunner(command_template=["cmd"], cwd=Path("."), timeout_seconds=60)
    pool = RunnerPool([runner])
    assert pool.all_runners() == [runner]


def test_runner_pool_from_settings(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {"command_template": ["openclaw", "--json"]}
    pool = RunnerPool.from_settings(settings)
    assert pool.size() == 1


def test_build_openclaw_runner_returns_first_runner(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {"command_template": ["openclaw", "--json"]}
    runner = build_openclaw_runner(settings)
    assert runner.agent_id == ""


def test_runner_pool_acquire_raises_when_empty():
    pool = RunnerPool([])
    with pytest.raises(RuntimeError, match="Runners not initialized"):
        pool.acquire()
