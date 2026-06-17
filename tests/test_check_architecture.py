import json
from pathlib import Path

import pytest

from scripts.check_architecture import check_repository, forbidden_imports, is_scheduler_path


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_exemptions(path: Path, exemptions: list[dict]) -> None:
    import yaml

    exemption_path = path / "config/architecture-exemptions.yaml"
    exemption_path.parent.mkdir(parents=True, exist_ok=True)
    exemption_path.write_text(yaml.safe_dump({"exemptions": exemptions}), encoding="utf-8")


def test_rejects_new_route_without_response_model(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        "def example() -> dict[str, object]:\n"
        "    return {}\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("response_model" in error and "example.py" in error for error in errors)


def test_rejects_route_importing_cms_client(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from server.app.cms.client import CmsClient\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("route boundary" in error for error in errors)


@pytest.mark.parametrize(
    "source,expected_error",
    [
        (
            "from server.app.workflows.pi_runner import PiRunner\n",
            "scheduler boundary",
        ),
        (
            "from server.app.pipeline.openclaw import OpenClawRunner\n",
            "scheduler boundary",
        ),
        (
            "from server.app.workflows.pi_runner import PiRunner\n",
            "pi_runner",
        ),
        (
            "from server.app.workflows.skills import resolve_workflow_skill\n",
            "skills",
        ),
        (
            "from server.app.workflows.reading_analysis import fetch_questions\n",
            "reading_analysis",
        ),
        (
            "import subprocess\n",
            "subprocess",
        ),
    ],
)
def test_rejects_scheduler_boundary_forbidden_imports(tmp_path, source, expected_error):
    write(tmp_path / "server/app/workflow_worker_thread.py", source)
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("scheduler boundary" in error and expected_error in error for error in errors)


def test_rejects_scheduler_threadpool_construction(tmp_path):
    write(
        tmp_path / "server/app/workflows/scheduler.py",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "def build():\n"
        "    return ThreadPoolExecutor(max_workers=1)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "scheduler_threadpool_baselines": '
        '{"server/app/workflows/scheduler.py": {"self._local_executor": 1}}, "files": {}}',
    )

    errors = check_repository(tmp_path)

    assert any(
        "scheduler boundary forbids ThreadPoolExecutor construction" in error
        and "scheduler.py" in error
        for error in errors
    )


def test_accepts_scheduler_legacy_executor_assignment(tmp_path):
    write(
        tmp_path / "server/app/workflows/scheduler.py",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def __init__(self):\n"
        "        self._local_executor = ThreadPoolExecutor(max_workers=1)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"files": {}}',
    )
    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.scheduler_threadpool",
                "path": "server/app/workflows/scheduler.py:self._local_executor",
                "reason": "Single shared executor pool bounded by capacity.",
                "owner": "test",
                "remove_when": "issues/open/032-P2-event-driven-worker.md",
            }
        ],
    )

    errors = check_repository(tmp_path)

    assert not any("ThreadPoolExecutor construction" in error for error in errors)


def test_route_annotation_exemptions(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from typing import Any\n"
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/example')\n"
        "def example() -> dict[str, Any]:\n"
        "    return {}\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"files": {}}',
    )
    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.route_response_model",
                "path": "server/app/routes/example.py:example",
                "reason": "Dynamic response pending typed models.",
                "owner": "test",
                "remove_when": "issues/open/036-P3-type-sync-automation.md",
            }
        ],
    )

    errors = check_repository(tmp_path)

    assert any("return annotation may not contain Any" in error for error in errors)

    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.route_response_model",
                "path": "server/app/routes/example.py:example",
                "reason": "Dynamic response pending typed models.",
                "owner": "test",
                "remove_when": "issues/open/036-P3-type-sync-automation.md",
            },
            {
                "check": "architecture.route_annotation_any",
                "path": "server/app/routes/example.py:example",
                "reason": "Dynamic response pending typed models.",
                "owner": "test",
                "remove_when": "issues/open/036-P3-type-sync-automation.md",
            },
        ],
    )

    errors = check_repository(tmp_path)

    assert not any("return annotation may not contain Any" in error for error in errors)


def test_accepts_compliant_route(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from pydantic import BaseModel\n"
        "from fastapi import APIRouter\n"
        "class ExampleResponse(BaseModel):\n"
        "    value: str\n"
        "router = APIRouter()\n"
        "@router.get('/example', response_model=ExampleResponse)\n"
        "def example() -> dict[str, str]:\n"
        "    return {}\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert errors == []


def test_accepts_scheduler_legacy_executor_assignment_with_annotation(tmp_path):
    write(
        tmp_path / "server/app/workflows/scheduler.py",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def __init__(self):\n"
        "        self._local_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"files": {}}',
    )
    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.scheduler_threadpool",
                "path": "server/app/workflows/scheduler.py:self._local_executor",
                "reason": "Single shared executor pool bounded by capacity.",
                "owner": "test",
                "remove_when": "issues/open/032-P2-event-driven-worker.md",
            }
        ],
    )

    errors = check_repository(tmp_path)

    assert not any("ThreadPoolExecutor construction" in error for error in errors)


def test_imported_modules_records_from_submodule_names(tmp_path):
    from scripts.check_architecture import imported_modules

    source = "from server.app.workflows import pi_runner\n"
    tree = __import__("ast").parse(source)

    modules = imported_modules(tree)

    assert modules == {"server.app.workflows": 1, "server.app.workflows.pi_runner": 1}


def test_route_imported_submodule_is_forbidden(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from server.app import cms\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("route boundary forbids import server.app.cms" in error for error in errors)


def test_forbidden_imports_submodule_match():
    modules = {"server.app.cms.client": 3}
    result = forbidden_imports(modules, ("server.app.cms",))
    assert result == [("server.app.cms.client", 3)]


def test_is_scheduler_path_workflow_worker_thread():
    assert is_scheduler_path("server/app/workflow_worker_thread.py")


def test_rejects_file_growth_above_recorded_budget(tmp_path):
    write(tmp_path / "server/app/routes/jobs.py", "\n".join(["pass"] * 11) + "\n")
    write(
        tmp_path / "config/architecture-budgets.json",
        json.dumps(
            {
                "route_exemptions": [],
                "files": {"server/app/routes/jobs.py": 10},
            }
        ),
    )

    errors = check_repository(tmp_path)

    assert any("11 lines exceeds budget 10" in error for error in errors)


def test_allows_file_to_shrink_below_recorded_budget(tmp_path):
    write(tmp_path / "server/app/routes/jobs.py", "\n".join(["pass"] * 9) + "\n")
    write(
        tmp_path / "config/architecture-budgets.json",
        json.dumps(
            {
                "route_exemptions": [],
                "files": {"server/app/routes/jobs.py": 10},
            }
        ),
    )

    assert check_repository(tmp_path) == []


def test_default_budget_enforced_for_new_files(tmp_path):
    write(
        tmp_path / "server/app/services/big_service.py",
        "\n".join(["pass"] * 401) + "\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        json.dumps(
            {
                "route_exemptions": [],
                "files": {},
                "defaults": {"server/app/services": 400},
            }
        ),
    )

    errors = check_repository(tmp_path)

    assert any(
        "server/app/services/big_service.py" in error and "401 lines exceeds budget 400" in error
        for error in errors
    )


def test_rejects_workflow_worker_accessing_runner_attribute(tmp_path):
    write(
        tmp_path / "server/app/workflow_worker_thread.py",
        "class Worker:\n    def run(self, node):\n        if node.runner == 'local':\n            pass\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any(".runner or .agent" in error for error in errors)


def test_rejects_workflow_worker_accessing_agent_attribute(tmp_path):
    write(
        tmp_path / "server/app/workflow_worker_thread.py",
        "class Worker:\n    def run(self, node):\n        if node.agent is not None:\n            pass\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any(".runner or .agent" in error for error in errors)


def test_rejects_scheduler_using_futures_length_for_capacity(tmp_path):
    write(
        tmp_path / "server/app/workflows/scheduler.py",
        "class Worker:\n    def has_capacity(self):\n        return len(self._futures) < 10\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("_futures length" in error for error in errors)


def test_rejects_scheduler_threadpool_keyed_by_workspace(tmp_path):
    write(
        tmp_path / "server/app/workflow_worker_thread.py",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def build(self, workspace_id):\n"
        "        self._pools[workspace_id] = ThreadPoolExecutor(max_workers=1)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "scheduler_threadpool_baselines": '
        '{"server/app/workflow_worker_thread.py": {"self._pools[workspace_id]": 1}}, "files": {}}',
    )

    errors = check_repository(tmp_path)

    assert any("ThreadPoolExecutor construction keyed by workspace" in error for error in errors)


def test_rejects_executor_module_reading_raw_executors_config(tmp_path):
    write(
        tmp_path / "server/app/executors/local.py",
        "class LocalExecutor:\n"
        "    def __init__(self, settings):\n"
        "        self.config = settings.config['executors']\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("raw settings.config['executors']" in error for error in errors)


@pytest.mark.parametrize(
    "yaml_content,expected_error",
    [
        (
            "key: example\nlabel: Example\nnodes:\n  fetch:\n    label: Fetch\n",
            "non-empty capability",
        ),
        (
            "key: example\nlabel: Example\nnodes:\n  fetch:\n    capability: fetch\n    runner: local\n",
            "field 'runner' was removed",
        ),
        (
            "key: example\nlabel: Example\nnodes:\n  review:\n    capability: review\n    agent:\n      engine: pi\n",
            "field 'agent' was removed",
        ),
        (
            "key: example\nlabel: Example\nconcurrency:\n  nodes:\n    review: 2\n"
            "nodes:\n  review:\n    capability: review\n",
            "top-level 'concurrency' was removed",
        ),
    ],
)
def test_rejects_invalid_workflow_yaml(tmp_path, yaml_content, expected_error):
    (tmp_path / "server/app").mkdir(parents=True)
    (tmp_path / "config/workflows").mkdir(parents=True)
    write(tmp_path / "config/workflows/example.yaml", yaml_content)
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any(expected_error in error for error in errors)


def test_rejects_legacy_module_present(tmp_path):
    write(
        tmp_path / "server/app/routes/workspace_agents.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("legacy module must be removed" in error for error in errors)


def test_rejects_forbidden_pattern_in_source(tmp_path):
    write(
        tmp_path / "server/app/workflows/example.py",
        'node["runner"]\n',
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert any("forbidden pattern" in error and "node.runner literal" in error for error in errors)
