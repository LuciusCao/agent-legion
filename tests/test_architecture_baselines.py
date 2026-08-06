from pathlib import Path

import pytest

from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_exemptions(path: Path, exemptions: list[dict]) -> None:
    import yaml

    exemption_path = path / "config/architecture/architecture-exemptions.yaml"
    exemption_path.parent.mkdir(parents=True, exist_ok=True)
    exemption_path.write_text(yaml.safe_dump({"exemptions": exemptions}), encoding="utf-8")


def test_rejects_none_response_model(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/example', response_model=None)\n"
        "def example() -> dict[str, str]:\n"
        "    return {}\n",
    )
    write_neutral_budget_governance(tmp_path)
    errors = check_repository(tmp_path)
    assert any("named response_model" in error for error in errors)


def test_rejects_builtin_generic_response_model(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/example', response_model=dict[str, str])\n"
        "def example() -> dict[str, str]:\n"
        "    return {}\n",
    )
    write_neutral_budget_governance(tmp_path)
    errors = check_repository(tmp_path)
    assert any("named response_model" in error for error in errors)


def test_accepts_imported_named_response_model(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from fastapi import APIRouter\n"
        "from server.app.contracts import ExampleResponse\n"
        "router = APIRouter()\n"
        "@router.get('/example', response_model=ExampleResponse)\n"
        "def example() -> dict[str, str]:\n"
        "    return {}\n",
    )
    write_neutral_budget_governance(tmp_path)
    errors = check_repository(tmp_path)
    assert errors == []


def test_route_import_baseline_allows_only_recorded_modules(tmp_path):
    path = tmp_path / "server/app/routes/example.py"
    write(path, "from server.app.cms.client import CmsClient\n")
    write_neutral_budget_governance(tmp_path)
    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.route_import_boundary",
                "path": "server/app/routes/example.py:server.app.cms.client",
                "reason": "Test exemption for CMS client import.",
                "owner": "test",
                "remove_when": "issues/open/027-P1-split-routes-jobs-refactor.md",
            }
        ],
    )
    assert not any("route boundary" in error for error in check_repository(tmp_path))
    write(
        path,
        "from server.app.cms.client import CmsClient\n"
        "from server.app.cms.question import fetch_question_detail\n",
    )
    assert any("server.app.cms.question" in error for error in check_repository(tmp_path))


def test_scheduler_import_baseline_allows_only_recorded_modules(tmp_path):
    path = tmp_path / "server/app/workflows/scheduler.py"
    write(path, "from server.app.workflows.pi_runner import PiRunner\n")
    write_neutral_budget_governance(tmp_path)
    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.scheduler_import_boundary",
                "path": "server/app/workflows/scheduler.py:server.app.workflows.pi_runner",
                "reason": "Test exemption for scheduler pi_runner import.",
                "owner": "test",
                "remove_when": "issues/open/032-P2-event-driven-worker.md",
            }
        ],
    )
    assert not any("scheduler boundary" in error for error in check_repository(tmp_path))
    write(path, path.read_text(encoding="utf-8") + "import subprocess\n")
    assert any("forbids import subprocess" in error for error in check_repository(tmp_path))


def test_scheduler_threadpool_baseline_allows_only_recorded_targets_and_counts(tmp_path):
    path = tmp_path / "server/app/workflows/scheduler.py"
    write(
        path,
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def build(self):\n"
        "        self._local_executor = ThreadPoolExecutor(max_workers=1)\n",
    )
    write_neutral_budget_governance(tmp_path)
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
    assert not any(
        "ThreadPoolExecutor construction" in error for error in check_repository(tmp_path)
    )
    write(
        path,
        path.read_text(encoding="utf-8")
        + "        self._agent_executor = ThreadPoolExecutor(max_workers=1)\n",
    )
    assert any("self._agent_executor" in error for error in check_repository(tmp_path))


def test_jobs_router_is_not_a_router_aggregator(tmp_path):
    write(
        tmp_path / "server/app/routes/jobs.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\nrouter.include_router(other)\n",
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("server/app/routes/jobs.py: include_router forbidden" in error for error in errors)


def test_scheduler_executor_id_indexed_pool_is_allowed(tmp_path):
    write(
        tmp_path / "server/app/workflow_worker/thread.py",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def build(self, executor_id):\n"
        "        self._pools[executor_id] = ThreadPoolExecutor(max_workers=1)\n",
    )
    write_neutral_budget_governance(tmp_path)
    write_exemptions(
        tmp_path,
        [
            {
                "check": "architecture.scheduler_threadpool",
                "path": "server/app/workflow_worker/thread.py:self._pools[executor_id]",
                "reason": "Executor-id keyed shared pool bounded by capacity.",
                "owner": "test",
                "remove_when": "issues/open/032-P2-event-driven-worker.md",
            }
        ],
    )

    errors = check_repository(tmp_path)

    assert not any("ThreadPoolExecutor" in error for error in errors)


def test_workflow_yaml_capability_node_is_allowed(tmp_path):
    (tmp_path / "server/app").mkdir(parents=True)
    (tmp_path / "config/workflows").mkdir(parents=True)
    write(
        tmp_path / "config/workflows/example.yaml",
        "key: example\nlabel: Example\nnodes:\n  review:\n    capability: review\n",
    )
    write_neutral_budget_governance(tmp_path)

    assert check_repository(tmp_path) == []


def test_executor_module_config_subscript_not_named_executors_is_allowed(tmp_path):
    write(
        tmp_path / "server/app/executors/code.py",
        "class CodeExecutor:\n"
        "    def __init__(self, settings):\n"
        "        self.value = settings.config['other']\n",
    )
    write_neutral_budget_governance(tmp_path)

    assert check_repository(tmp_path) == []
