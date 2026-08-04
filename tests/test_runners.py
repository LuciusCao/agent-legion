import json
import subprocess

import pytest

from server.app.pipeline.runners import (
    RunnerPool,
    build_openclaw_runner,
    build_openclaw_runners,
    discover_openclaw_agents,
)
from server.app.settings import load_settings

# --- discover_openclaw_agents ---


def test_discover_returns_empty_on_command_error(monkeypatch):
    def fail_run(*args, **kwargs):
        raise RuntimeError("command not found")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert discover_openclaw_agents() == []


def test_discover_returns_empty_on_nonzero_exit(monkeypatch):
    def bad_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "", "")

    monkeypatch.setattr(subprocess, "run", bad_run)
    assert discover_openclaw_agents() == []


def test_discover_filters_invalid_items(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            json.dumps([{"id": "main"}, {"name": "no-id"}, 42]),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert discover_openclaw_agents() == ["main"]


# --- build_openclaw_runners ---


def test_build_with_explicit_runners_config(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "runners": [
            {"command_template": ["openclaw", "agent", "--agent", "r1"]},
        ],
        "cwd": ".",
        "timeout_seconds": 300,
    }
    runners = build_openclaw_runners(settings)
    assert len(runners) == 1
    assert runners[0].command_template == ["openclaw", "agent", "--agent", "r1"]


def test_build_with_discovered_agents(tmp_path, monkeypatch):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "command_template": [
            "openclaw",
            "agent",
            "--local",
            "--agent",
            "main",
            "--message",
            "{prompt_text}",
            "--json",
        ],
        "cwd": ".",
    }
    monkeypatch.setattr(
        "server.app.pipeline.runners.discover_openclaw_agents",
        lambda timeout=10: ["main", "agent_1"],
    )
    runners = build_openclaw_runners(settings)
    assert len(runners) == 2
    assert "--agent" in runners[0].command_template
    assert "--agent" in runners[1].command_template


def test_build_fallback_when_no_agents_discovered(tmp_path, monkeypatch):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "command_template": [
            "openclaw",
            "agent",
            "--local",
            "--agent",
            "main",
            "--json",
        ],
        "cwd": ".",
    }
    monkeypatch.setattr(
        "server.app.pipeline.runners.discover_openclaw_agents",
        lambda timeout=10: [],
    )
    runners = build_openclaw_runners(settings)
    assert len(runners) == 1
    assert runners[0].command_template == [
        "openclaw",
        "agent",
        "--local",
        "--agent",
        "main",
        "--json",
    ]


def test_build_without_agent_in_template(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "command_template": ["openclaw", "run"],
        "cwd": ".",
    }
    runners = build_openclaw_runners(settings)
    assert len(runners) == 1
    assert runners[0].command_template == ["openclaw", "run"]


def test_build_runner_from_settings(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "command_template": ["openclaw", "run"],
        "cwd": ".",
    }
    runner = build_openclaw_runner(settings)
    assert runner.command_template == ["openclaw", "run"]


# --- RunnerPool ---


def test_runner_pool_acquire_and_release(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "runners": [
            {"command_template": ["openclaw", "agent", "--agent", "r1"]},
            {"command_template": ["openclaw", "agent", "--agent", "r2"]},
        ],
        "cwd": ".",
    }
    pool = RunnerPool.from_settings(settings)
    assert pool.size() == 2

    idx1, runner1 = pool.acquire()
    assert runner1.command_template == ["openclaw", "agent", "--agent", "r1"]

    idx2, runner2 = pool.acquire()
    assert runner2.command_template == ["openclaw", "agent", "--agent", "r2"]

    with pytest.raises(RuntimeError, match="No free runner"):
        pool.acquire()

    pool.release(idx1)
    idx3, runner3 = pool.acquire()
    assert runner3.command_template == ["openclaw", "agent", "--agent", "r1"]


def test_runner_pool_all_runners(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"] = {
        "runners": [
            {"command_template": ["openclaw", "agent", "--agent", "r1"]},
        ],
        "cwd": ".",
    }
    pool = RunnerPool.from_settings(settings)
    assert len(pool.all_runners()) == 1


def test_runner_pool_acquire_raises_when_empty():
    pool = RunnerPool()
    with pytest.raises(RuntimeError, match="Runners not initialized"):
        pool.acquire()
