import json
from pathlib import Path

from scripts.check_architecture import check_repository


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rejects_none_response_model(tmp_path):
    write(
        tmp_path / "server/app/routes/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/example', response_model=None)\n"
        "def example() -> dict[str, str]:\n"
        "    return {}\n",
    )
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

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
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

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
    write(tmp_path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')

    errors = check_repository(tmp_path)

    assert errors == []


def test_route_import_baseline_allows_only_recorded_modules(tmp_path):
    path = tmp_path / "server/app/routes/example.py"
    write(path, "from server.app.cms.client import CmsClient\n")
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "route_import_baselines": '
        '{"server/app/routes/example.py": ["server.app.cms.client"]}, "files": {}}',
    )

    assert not any("route boundary" in error for error in check_repository(tmp_path))

    write(
        path,
        "from server.app.cms.client import CmsClient\n"
        "from server.app.cms.question import fetch_question_detail\n",
    )

    assert any("server.app.cms.question" in error for error in check_repository(tmp_path))


def test_scheduler_import_baseline_allows_only_recorded_modules(tmp_path):
    path = tmp_path / "server/app/pipelines/scheduler.py"
    write(path, "from server.app.pipelines.pi_runner import PiRunner\n")
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "scheduler_import_baselines": '
        '{"server/app/pipelines/scheduler.py": ["server.app.pipelines.pi_runner"]}, '
        '"files": {}}',
    )

    assert not any("scheduler boundary" in error for error in check_repository(tmp_path))

    write(path, path.read_text(encoding="utf-8") + "import subprocess\n")

    assert any("forbids import subprocess" in error for error in check_repository(tmp_path))


def test_scheduler_threadpool_baseline_allows_only_recorded_targets_and_counts(tmp_path):
    path = tmp_path / "server/app/pipelines/scheduler.py"
    write(
        path,
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def build(self):\n"
        "        self._local_executor = ThreadPoolExecutor(max_workers=1)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        json.dumps(
            {
                "route_exemptions": [],
                "scheduler_threadpool_baselines": {
                    "server/app/pipelines/scheduler.py": {"self._local_executor": 1}
                },
                "files": {},
            }
        ),
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


def test_services_do_not_import_fastapi_regardless_of_filename(tmp_path):
    write(
        tmp_path / "server/app/services/catalog.py",
        "from fastapi import HTTPException\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "files": {}}',
    )

    errors = check_repository(tmp_path)

    assert any("service boundary forbids import fastapi" in error for error in errors)


def test_jobs_router_is_not_a_router_aggregator(tmp_path):
    write(
        tmp_path / "server/app/routes/jobs.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\nrouter.include_router(other)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "files": {}}',
    )

    errors = check_repository(tmp_path)

    assert any("server/app/routes/jobs.py: include_router forbidden" in error for error in errors)


def test_scheduler_executor_id_indexed_pool_is_allowed(tmp_path):
    write(
        tmp_path / "server/app/pipeline_worker_thread.py",
        "from concurrent.futures import ThreadPoolExecutor\n"
        "class Worker:\n"
        "    def build(self, executor_id):\n"
        "        self._pools[executor_id] = ThreadPoolExecutor(max_workers=1)\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        json.dumps(
            {
                "route_exemptions": [],
                "scheduler_threadpool_baselines": {
                    "server/app/pipeline_worker_thread.py": {"self._pools[executor_id]": 1}
                },
                "files": {},
            }
        ),
    )

    errors = check_repository(tmp_path)

    assert not any("ThreadPoolExecutor" in error for error in errors)


def test_pipeline_yaml_agent_node_without_limit_is_allowed(tmp_path):
    (tmp_path / "server/app").mkdir(parents=True)
    (tmp_path / "config/pipelines").mkdir(parents=True)
    write(
        tmp_path / "config/pipelines/example.yaml",
        "key: example\nlabel: Example\nnodes:\n"
        "  review:\n    capability: review\n    runner: agent\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "files": {}}',
    )

    assert check_repository(tmp_path) == []


def test_executor_module_config_subscript_not_named_executors_is_allowed(tmp_path):
    write(
        tmp_path / "server/app/executors/local.py",
        "class LocalExecutor:\n"
        "    def __init__(self, settings):\n"
        "        self.value = settings.config['other']\n",
    )
    write(
        tmp_path / "config/architecture-budgets.json",
        '{"route_exemptions": [], "files": {}}',
    )

    assert check_repository(tmp_path) == []
