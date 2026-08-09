"""Agent 子进程环境构造（worker/executor.py agent_subprocess_env）测试。

保证 agent bash 会话里的 `python`/`python3` 解析到 worker 自己的 venv
解释器——环境缺失时模型会用 `find /` 全盘扫描定位解释器，并行 job 下
打爆宿主机 fseventsd/Spotlight。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from worker.executor import agent_subprocess_env


def test_prepends_worker_interpreter_bin_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_GATEWAY_TOKEN", raising=False)
    env = agent_subprocess_env({})
    first = env["PATH"].split(os.pathsep)[0]
    # 不 resolve()：.venv/bin/python 是指向基础解释器的符号链接，解析后
    # 会绕过 venv（缺项目依赖），必须保留 venv 目录本身。
    assert first == str(Path(sys.executable).parent)
    # 该目录确实提供 python/python3（venv 语义，防符号链接回归）。
    assert (Path(first) / "python").exists()
    assert (Path(first) / "python3").exists()


def test_preserves_existing_path_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = agent_subprocess_env({})
    parts = env["PATH"].split(os.pathsep)
    assert parts[1:] == ["/usr/bin", "/bin"]


def test_config_environment_overrides_still_get_venv_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    env = agent_subprocess_env({"PATH": "/custom/bin", "FOO": "bar"})
    assert env["FOO"] == "bar"
    assert env["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).parent)
    assert "/custom/bin" in env["PATH"]


def test_gateway_token_follows_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "worker-token")
    env = agent_subprocess_env({"LLM_GATEWAY_TOKEN": "config-token"})
    assert env["LLM_GATEWAY_TOKEN"] == "worker-token"
    monkeypatch.delenv("LLM_GATEWAY_TOKEN")
    env = agent_subprocess_env({"LLM_GATEWAY_TOKEN": "config-token"})
    assert "LLM_GATEWAY_TOKEN" not in env
