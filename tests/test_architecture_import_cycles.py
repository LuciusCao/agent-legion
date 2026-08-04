import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _check_import_cycles(root: Path) -> list[str]:
    module = importlib.import_module("scripts.architecture.import_cycles")
    return module.check_import_cycles(root)


def test_reports_complete_cyclic_component(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/a.py", "from server.app import b\n")
    _write(tmp_path, "server/app/b.py", "from server.app import c\n")
    _write(tmp_path, "server/app/c.py", "from . import a\n")

    assert _check_import_cycles(tmp_path) == [
        "import cycle: server.app.a -> server.app.b -> server.app.c"
    ]


def test_resolves_relative_package_imports(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/pkg/__init__.py", "from . import member\n")
    _write(tmp_path, "server/app/pkg/member.py", "from server.app.pkg import sibling\n")
    _write(tmp_path, "server/app/pkg/sibling.py", "from . import member\n")

    assert _check_import_cycles(tmp_path) == [
        "import cycle: server.app.pkg -> server.app.pkg.member -> server.app.pkg.sibling"
    ]


def test_reports_components_and_names_in_stable_order(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/zeta.py", "import server.app.alpha\n")
    _write(tmp_path, "server/app/alpha.py", "import server.app.zeta\n")
    _write(tmp_path, "server/app/delta.py", "import server.app.charlie\n")
    _write(tmp_path, "server/app/charlie.py", "import server.app.delta\n")

    assert _check_import_cycles(tmp_path) == [
        "import cycle: server.app.alpha -> server.app.zeta",
        "import cycle: server.app.charlie -> server.app.delta",
    ]


def test_dotted_import_includes_package_initializer_edges(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/pkg/__init__.py", "from server.app import other\n")
    _write(tmp_path, "server/app/pkg/member.py", "")
    _write(tmp_path, "server/app/other.py", "import server.app.pkg.member\n")

    assert _check_import_cycles(tmp_path) == ["import cycle: server.app.other -> server.app.pkg"]


def test_dotted_import_without_reverse_edge_is_acyclic(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/pkg/__init__.py", "")
    _write(tmp_path, "server/app/pkg/member.py", "")
    _write(tmp_path, "server/app/other.py", "import server.app.pkg.member\n")

    assert _check_import_cycles(tmp_path) == []


@pytest.mark.parametrize("declaration", ["def load():", "async def load():"])
def test_function_body_import_is_lazy(tmp_path: Path, declaration: str) -> None:
    _write(tmp_path, "server/app/a.py", "import server.app.b\n")
    _write(
        tmp_path,
        "server/app/b.py",
        f"{declaration}\n    import server.app.a\n",
    )

    assert _check_import_cycles(tmp_path) == []


@pytest.mark.parametrize("declaration", ["def load():", "async def load():"])
def test_function_body_import_counts_in_executor_workflow_pipeline(
    tmp_path: Path, declaration: str
) -> None:
    _write(tmp_path, "server/app/executors/a.py", "import server.app.workflows.b\n")
    _write(
        tmp_path,
        "server/app/workflows/b.py",
        f"{declaration}\n    import server.app.executors.a\n",
    )

    assert _check_import_cycles(tmp_path) == [
        "import cycle: server.app.executors.a -> server.app.workflows.b"
    ]


def test_scoped_function_body_import_respects_type_checking_guard(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/pipeline/a.py", "import server.app.executors.b\n")
    _write(
        tmp_path,
        "server/app/executors/b.py",
        "from typing import TYPE_CHECKING\n"
        "def load():\n"
        "    if TYPE_CHECKING:\n"
        "        import server.app.pipeline.a\n",
    )

    assert _check_import_cycles(tmp_path) == []


def test_function_body_type_import_does_not_mask_module_guard(tmp_path: Path) -> None:
    # TYPE_CHECKING bound only inside a function body must not mark the
    # module-level guard as type-only: at runtime the name is undefined and
    # the guarded import is a real dependency.
    _write(tmp_path, "server/app/executors/a.py", "import server.app.workflows.b\n")
    _write(
        tmp_path,
        "server/app/workflows/b.py",
        "def load():\n"
        "    from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import server.app.executors.a\n",
    )

    assert _check_import_cycles(tmp_path) == [
        "import cycle: server.app.executors.a -> server.app.workflows.b"
    ]


@pytest.mark.parametrize(
    "guarded_source",
    [
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\nif TC:\n    import server.app.a\n",
        "import typing\nif typing.TYPE_CHECKING:\n    import server.app.a\n",
        "import typing as t\nif t.TYPE_CHECKING:\n    import server.app.a\n",
    ],
)
def test_type_checking_import_is_not_runtime_dependency(
    tmp_path: Path, guarded_source: str
) -> None:
    _write(tmp_path, "server/app/a.py", "import server.app.b\n")
    _write(tmp_path, "server/app/b.py", guarded_source)

    assert _check_import_cycles(tmp_path) == []


@pytest.mark.parametrize(
    "source",
    [
        "from typing import TYPE_CHECKING as TC\nTC, other = (True, 1)\nif TC:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\nfor TC in [True]:\n    if TC:\n        import server.app.a\n",
        "if object():\n    TC = True\nelse:\n    from typing import TYPE_CHECKING as TC\nif TC:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\n(TC := True)\nif TC:\n    import server.app.a\n",
        "from contextlib import nullcontext\nfrom typing import TYPE_CHECKING as TC\nwith nullcontext(True) as TC:\n    if TC:\n        import server.app.a\n",
        "try:\n    TC = True\nexcept Exception:\n    from typing import TYPE_CHECKING as TC\nif TC:\n    import server.app.a\n",
        "match 1:\n    case 1:\n        TC = True\n    case _:\n        from typing import TYPE_CHECKING as TC\nif TC:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\nfor _ in range(2):\n    if TC:\n        import server.app.a\n    TC = True\n",
        "from typing import TYPE_CHECKING as TC\ntry:\n    TC = True\n    raise RuntimeError\nexcept RuntimeError:\n    if TC:\n        import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\nclass Runtime:\n    global TC\n    TC = True\nif TC:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\ndef eager(value=(TC := True)):\n    pass\nif TC:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\neager = lambda value=(TC := True): value\nif TC:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING as TC\n@((TC := (lambda value: value)))\nclass Runtime:\n    pass\nif TC:\n    import server.app.a\n",
        "from contextlib import suppress\nfrom typing import TYPE_CHECKING as TC\nwith suppress(Exception):\n    TC = True\n    raise Exception\n    from typing import TYPE_CHECKING as TC\nif TC:\n    import server.app.a\n",
    ],
)
def test_rebound_type_checking_alias_is_runtime_dependency(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "server/app/a.py", "import server.app.b\n")
    _write(tmp_path, "server/app/b.py", source)

    assert _check_import_cycles(tmp_path) == ["import cycle: server.app.a -> server.app.b"]


@pytest.mark.parametrize(
    "source",
    [
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import server.app.a\nTYPE_CHECKING = True\n",
        "from typing import TYPE_CHECKING\nclass Runtime:\n    TYPE_CHECKING = True\nif TYPE_CHECKING:\n    import server.app.a\n",
        "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import server.app.a\n",
    ],
)
def test_type_checking_guard_respects_scope_and_statement_order(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path, "server/app/a.py", "import server.app.b\n")
    _write(tmp_path, "server/app/b.py", source)

    assert _check_import_cycles(tmp_path) == []


def test_class_body_import_is_eager(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/a.py", "import server.app.b\n")
    _write(
        tmp_path,
        "server/app/b.py",
        "class RuntimeConfig:\n    import server.app.a\n",
    )

    assert _check_import_cycles(tmp_path) == ["import cycle: server.app.a -> server.app.b"]


def test_jobs_lazy_getattr_import_is_absent_from_graph() -> None:
    module = importlib.import_module("scripts.architecture.import_cycles")
    root = Path(__file__).resolve().parents[1]
    path = root / "server/app/jobs/__init__.py"
    known = {
        module._module_name(candidate.relative_to(root))
        for candidate in root.glob("server/app/**/*.py")
    }

    dependencies = module._dependencies("server.app.jobs.__init__", path, known)

    assert "server.app.jobs.queries" not in dependencies


def test_jobs_package_import_is_lazy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import server.app.jobs; "
            "assert 'server.app.jobs.queries' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_legacy_job_queries_export_remains_compatible() -> None:
    from server.app.jobs import JobQueries
    from server.app.jobs.queries import JobQueries as DirectJobQueries

    assert JobQueries is DirectJobQueries


def test_current_repository_has_no_import_cycles() -> None:
    root = Path(__file__).resolve().parents[1]
    assert _check_import_cycles(root) == []


def test_repository_reports_each_import_cycle_once(tmp_path: Path) -> None:
    _write(tmp_path, "server/app/a.py", "from server.app import b\n")
    _write(tmp_path, "server/app/b.py", "from . import a\n")
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert errors.count("import cycle: server.app.a -> server.app.b") == 1
