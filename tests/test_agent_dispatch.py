from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from server.app import agent_dispatch
from server.app.agent_catalog import AgentDefinition
from server.app.agent_dispatch import AgentDispatchService
from server.app.workflows.pi_config import PiConfig
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution

_EXECUTION_ID = "00000000-0000-0000-0000-000000000123"


def _definition(*, runtime: str = "pi") -> AgentDefinition:
    return AgentDefinition(
        capability="generate",
        runtime=runtime,
        skill="question/generate",
        tools=("read", "write"),
        config_schema={
            "type": "object",
            "properties": {
                "page_size": {"type": "integer"},
                "api_key": {"type": "string", "secret": True},
            },
        },
    )


def _node() -> WorkflowNode:
    return WorkflowNode(
        key="generate",
        label="Generate",
        capability="generate",
        outputs=["answer.json"],
        execution=WorkflowNodeExecution(
            provider="node-provider",
            thinking="high",
            prompt="Answer carefully",
        ),
    )


@pytest.fixture
def harness(settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    manager = MagicMock()
    pool = MagicMock()
    broker = MagicMock()
    broker.bundle_dir = tmp_path / "bundles"
    broker.has_active_request.return_value = False
    broker.enqueue.return_value = _EXECUTION_ID
    artifact_store = MagicMock()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()

    resolve_skill_dir = MagicMock(return_value=skill_dir)
    get_skill_version = MagicMock(return_value="skill-v1")
    stage_agent_inputs = MagicMock()
    render_command_spec = MagicMock(return_value={"command": ["pi", "--print"]})

    def build_bundle(path: Path, *, skill_dir: Path, manifest: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("bundle", encoding="utf-8")

    build_agent_bundle = MagicMock(side_effect=build_bundle)

    monkeypatch.setattr(agent_dispatch, "build_skill_manager", lambda _root: manager)
    monkeypatch.setattr(agent_dispatch, "AgentEnqueuePool", lambda: pool)
    monkeypatch.setattr(agent_dispatch, "resolve_skill_dir", resolve_skill_dir)
    monkeypatch.setattr(agent_dispatch, "get_skill_version", get_skill_version)
    monkeypatch.setattr(agent_dispatch, "stage_agent_inputs", stage_agent_inputs)
    monkeypatch.setattr(agent_dispatch, "render_command_spec", render_command_spec)
    monkeypatch.setattr(agent_dispatch, "build_agent_bundle", build_agent_bundle)
    monkeypatch.setattr(agent_dispatch.uuid, "uuid4", lambda: _EXECUTION_ID)
    monkeypatch.setattr(
        agent_dispatch.PiConfig,
        "from_runtime",
        lambda _runtime: PiConfig(
            binary="pi-bin",
            flavor="pi",
            provider="default-provider",
            model="default-model",
            thinking="low",
            timeout_seconds=42,
            velites_no_sandbox=True,
        ),
    )

    service = AgentDispatchService(settings, broker, artifact_store)
    return SimpleNamespace(
        service=service,
        manager=manager,
        pool=pool,
        broker=broker,
        artifact_store=artifact_store,
        skill_dir=skill_dir,
        resolve_skill_dir=resolve_skill_dir,
        get_skill_version=get_skill_version,
        stage_agent_inputs=stage_agent_inputs,
        render_command_spec=render_command_spec,
        build_agent_bundle=build_agent_bundle,
        tmp_path=tmp_path,
    )


def _enqueue(harness: SimpleNamespace, *, definition: AgentDefinition | None = None) -> bool:
    return harness.service.enqueue(
        agent_id="generator-v1",
        definition=definition or _definition(),
        workspace={"id": "workspace-1", "name": "Workspace"},
        job={"id": "job-1", "title": "Question"},
        workflow_key="questions",
        node=_node(),
        job_dir=harness.tmp_path / "jobs" / "job-1",
        log_path=harness.tmp_path / "logs" / "job-1.log",
        inputs=("question.json",),
        node_config={"page_size": 25, "api_key": "must-not-leak", "unknown": "drop"},
    )


def test_enqueue_skips_an_existing_active_request(harness: SimpleNamespace) -> None:
    harness.broker.has_active_request.return_value = True

    assert _enqueue(harness) is False

    harness.broker.has_active_request.assert_called_once_with("job-1", "generate")
    harness.resolve_skill_dir.assert_not_called()
    harness.broker.enqueue.assert_not_called()


def test_enqueue_rejects_an_unsupported_runtime(harness: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match="runtime 'openclaw' is not implemented"):
        _enqueue(harness, definition=_definition(runtime="openclaw"))

    harness.resolve_skill_dir.assert_not_called()
    harness.manager.cleanup_execution.assert_not_called()


def test_enqueue_builds_an_immutable_manifest_and_bundle(harness: SimpleNamespace) -> None:
    assert _enqueue(harness) is True

    request = harness.broker.enqueue.call_args.args[0]
    manifest = dict(request.manifest)
    assert request.execution_id == _EXECUTION_ID
    assert request.workspace_id == "workspace-1"
    assert request.job_id == "job-1"
    assert request.node_key == "generate"
    assert manifest["config"] == {"page_size": 25}
    assert manifest["expected_outputs"] == ["answer.json"]
    assert manifest["skill_version"] == "skill-v1"
    assert manifest["command_spec"] == {"command": ["pi", "--print"]}
    assert manifest["bundle_name"] == f"{_EXECUTION_ID}.tar.gz"
    assert manifest["pi"] == {
        "binary": "pi-bin",
        "flavor": "pi",
        "provider": "node-provider",
        "model": "default-model",
        "thinking": "high",
        "timeout_seconds": 42,
        "velites_no_sandbox": True,
    }

    context = harness.stage_agent_inputs.call_args.args[1]
    assert context.execution_id == _EXECUTION_ID
    assert context.executor_id == "agent:generator-v1"
    assert context.inputs == ("question.json",)
    assert context.expected_outputs == ("answer.json",)
    assert context.runtime["node_execution"]["prompt"] == "Answer carefully"
    assert (harness.broker.bundle_dir / manifest["bundle_name"]).exists()
    harness.manager.cleanup_execution.assert_called_once_with(_EXECUTION_ID)


def test_enqueue_removes_bundle_when_broker_rejects_the_request(
    harness: SimpleNamespace,
) -> None:
    harness.broker.enqueue.return_value = None

    assert _enqueue(harness) is False

    bundle_path = harness.broker.bundle_dir / f"{_EXECUTION_ID}.tar.gz"
    assert not bundle_path.exists()
    harness.manager.cleanup_execution.assert_called_once_with(_EXECUTION_ID)


def test_enqueue_removes_bundle_and_skill_when_broker_raises(
    harness: SimpleNamespace,
) -> None:
    harness.broker.enqueue.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        _enqueue(harness)

    bundle_path = harness.broker.bundle_dir / f"{_EXECUTION_ID}.tar.gz"
    assert not bundle_path.exists()
    harness.manager.cleanup_execution.assert_called_once_with(_EXECUTION_ID)


def test_enqueue_cleans_skill_when_staging_fails(harness: SimpleNamespace) -> None:
    harness.stage_agent_inputs.side_effect = RuntimeError("artifact missing")

    with pytest.raises(RuntimeError, match="artifact missing"):
        _enqueue(harness)

    harness.build_agent_bundle.assert_not_called()
    harness.broker.enqueue.assert_not_called()
    harness.manager.cleanup_execution.assert_called_once_with(_EXECUTION_ID)


def test_enqueue_requires_a_bundle_directory(harness: SimpleNamespace) -> None:
    harness.broker.bundle_dir = None

    with pytest.raises(RuntimeError, match="bundle directory is not configured"):
        _enqueue(harness)

    harness.build_agent_bundle.assert_not_called()
    harness.broker.enqueue.assert_not_called()
    harness.manager.cleanup_execution.assert_called_once_with(_EXECUTION_ID)


def test_enqueue_removes_partial_bundle_when_build_fails(harness: SimpleNamespace) -> None:
    def leave_partial_bundle(path: Path, **_kwargs: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("partial", encoding="utf-8")
        raise RuntimeError("archive write failed")

    harness.build_agent_bundle.side_effect = leave_partial_bundle

    with pytest.raises(RuntimeError, match="archive write failed"):
        _enqueue(harness)

    bundle_path = harness.broker.bundle_dir / f"{_EXECUTION_ID}.tar.gz"
    assert not bundle_path.exists()
    harness.broker.enqueue.assert_not_called()
    harness.manager.cleanup_execution.assert_called_once_with(_EXECUTION_ID)
