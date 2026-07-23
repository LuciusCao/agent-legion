import json
import subprocess

from server.app.pipeline.runners import list_openclaw_agents


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
