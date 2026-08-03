import importlib
from pathlib import Path

import pytest

from scripts.architecture import workspace_boundaries
from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _empty_budgets(path: Path) -> None:
    write_neutral_budget_governance(path)


class TestRouteDagAndDeletionBoundary:
    def test_rejects_filesystem_deletion_in_package_route(self, tmp_path):
        write(
            tmp_path / "server/app/routes/packages.py",
            "from fastapi import APIRouter\n"
            "from pathlib import Path\n"
            "router = APIRouter()\n"
            "@router.delete('/packages/{package_id}')\n"
            "def delete_package(package_id: int):\n"
            "    package_path = Path('/tmp/package.zip')\n"
            "    package_path.unlink()\n",
        )
        _empty_budgets(tmp_path)
        errors = check_repository(tmp_path)
        assert any("filesystem deletion" in error for error in errors)

    @pytest.mark.parametrize(
        "setup,body,expected",
        [
            ("from os import remove", "remove('/tmp/package.zip')", True),
            ("from shutil import rmtree as drop", "drop('/tmp/package')", True),
            ("", "items = []\n    items.remove('value')", False),
            ("", "client.unlink()", False),
            ("import os", "delete = os.remove\n    delete('/tmp/package.zip')", True),
            ("import os", "delete: object = os.remove\n    delete('/tmp/x')", True),
            ("from pathlib import Path as LocalPath", "LocalPath.unlink(path)", True),
            (
                "import os\ndef local_fn(value):\n    return value",
                "delete = os.remove\n    delete: object = local_fn\n    delete('/tmp/x')",
                False,
            ),
            ("import os as operating_system", "operating_system.remove('/tmp/x')", True),
            ("from os import remove\ndef remove(x): return x", "remove(package_id)", False),
        ],
    )
    def test_resolves_filesystem_deletion_origins(self, tmp_path, setup, body, expected):
        source = (
            f"from fastapi import APIRouter\n{setup}\nrouter = APIRouter()\n"
            "@router.delete('/packages/{package_id}')\n"
            f"def delete_package(package_id: int):\n    {body}\n"
        )
        write(tmp_path / "server/app/routes/packages.py", source)
        _empty_budgets(tmp_path)
        errors = check_repository(tmp_path)
        found = any("filesystem deletion" in error for error in errors)
        assert found is expected

    @pytest.mark.parametrize(
        "expected_error,source",
        [
            (
                "DAG traversal",
                "from fastapi import APIRouter\n"
                "from server.app.workflows.scheduler import downstream_nodes\n"
                "router = APIRouter()\n"
                "@router.post('/x')\n"
                "def x():\n"
                "    return downstream_nodes(None, 'a')\n",
            ),
            (
                "filesystem deletion",
                "import shutil\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                "@router.delete('/x')\n"
                "def x():\n"
                "    shutil.rmtree('/tmp/x')\n",
            ),
        ],
    )
    def test_rejects_dag_or_deletion_in_route(self, tmp_path, expected_error, source):
        write(tmp_path / "server/app/routes/jobs.py", source)
        _empty_budgets(tmp_path)
        errors = check_repository(tmp_path)
        assert any(expected_error in error for error in errors)

    def test_rejects_filesystem_deletion_in_any_route_module(self, tmp_path):
        # Regression: agent_workers.py was not in the checked prefix list, so
        # deletion calls there slipped past the gate.
        write(
            tmp_path / "server/app/routes/agent_workers.py",
            "from fastapi import APIRouter\n"
            "from pathlib import Path\n"
            "router = APIRouter()\n"
            "@router.post('/agent-executions/{execution_id}/result')\n"
            "def result(execution_id: str):\n"
            "    Path('/tmp/archive.tar.gz').unlink()\n",
        )
        _empty_budgets(tmp_path)
        errors = check_repository(tmp_path)
        assert any("filesystem deletion" in error for error in errors)

    def test_allows_deletion_in_services(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_deletion.py",
            "import shutil\n"
            "from pathlib import Path\n"
            "class JobDeletionService:\n"
            "    def delete(self):\n"
            "        shutil.rmtree(Path('/tmp/x'))\n",
        )
        _empty_budgets(tmp_path)
        errors = check_repository(tmp_path)
        assert not any("filesystem deletion" in error for error in errors)


def test_preserves_exact_route_mutation_diagnostics(tmp_path):
    write(
        tmp_path / "server/app/routes/jobs.py",
        "import shutil\n"
        "from server.app.workflows.scheduler import downstream_nodes\n"
        "def mutate():\n"
        "    shutil.rmtree('/tmp/x')\n"
        "    downstream_nodes(None, 'a')\n",
    )

    assert workspace_boundaries.check_route_dag_and_deletion(tmp_path) == [
        "server/app/routes/jobs.py:4: filesystem deletion 'rmtree' belongs in services; "
        "routes must call orchestration services",
        "server/app/routes/jobs.py:5: DAG traversal 'downstream_nodes' belongs in services; "
        "routes must call orchestration services",
    ]


def test_workspace_boundaries_reexports_route_mutation_checker():
    route_mutations = importlib.import_module("scripts.architecture.route_mutations")

    assert (
        workspace_boundaries.check_route_dag_and_deletion
        is route_mutations.check_route_dag_and_deletion
    )
