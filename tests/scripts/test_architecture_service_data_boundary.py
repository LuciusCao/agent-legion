import json
from pathlib import Path

import pytest

from scripts.architecture.service_data_boundary import (
    check_service_data_boundary,
    collect_service_data_bypasses,
    count_service_data_bypasses,
)
from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_boundary_baseline(root: Path, files: dict[str, list[int]]) -> None:
    baseline_path = root / "config/architecture/service-data-boundary-baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps({"version": 1, "files": files}, indent=2) + "\n", encoding="utf-8"
    )


def test_counts_sql_literals_by_keyword_family():
    source = (
        'A = "select * from jobs"\n'
        'B = "INSERT INTO t VALUES (1)"\n'
        'C = "alter table t add column x text"\n'
        'D = "hello world"\n'
    )

    sql_literals, db_primitive_refs, dsn_path_refs = count_service_data_bypasses(source)

    assert sql_literals == 3
    assert db_primitive_refs == 0
    assert dsn_path_refs == 0


def test_counts_db_primitive_imports_and_calls():
    source = (
        "from server.app.db.transaction import read_connection\n"
        "from server.app.db.transaction import write_transaction\n"
        "import server.app.db.connection\n"
    )

    sql_literals, db_primitive_refs, dsn_path_refs = count_service_data_bypasses(source)

    assert sql_literals == 0
    assert db_primitive_refs == 3
    assert dsn_path_refs == 0


def test_counts_db_primitives_from_submodule_import_paths():
    # from server.app.db.transaction import ... counts each imported name.
    source = "from server.app.db.transaction import read_connection, write_transaction\n"

    _, db_primitive_refs, _ = count_service_data_bypasses(source)

    assert db_primitive_refs == 2


def test_counts_db_primitives_from_package_level_imports():
    # from server.app.db import transaction hands the service the module that
    # owns read_connection/write_transaction — the same escape hatch.
    source = (
        "from server.app.db import transaction\n"
        "from server.app.db import connection as dbconn\n"
        "from server.app.db import schema\n"
    )

    _, db_primitive_refs, _ = count_service_data_bypasses(source)

    assert db_primitive_refs == 2


def test_counts_dsn_path_attribute_access():
    # The DSN escape hatch: `.path` attribute access on any Name (heuristic —
    # `os.path` and `condition.path` count too); attribute chains like
    # `self.job_db.path` do not (value is an Attribute, not a Name).
    source = (
        "import psycopg\n"
        "conn = psycopg.connect(job_db.path)\n"
        "home = os.path.expanduser('~')\n"
        "other = self.job_db.path\n"
    )

    sql_literals, db_primitive_refs, dsn_path_refs = count_service_data_bypasses(source)

    assert sql_literals == 0
    assert db_primitive_refs == 0
    assert dsn_path_refs == 2


def test_counts_dsn_identity_attribute_access():
    # #187: `.dsn_identity` is the facade's cache-key identity — reading it
    # in a service (outside the sanctioned resolver) is the same escape.
    source = "key = job_db.dsn_identity\n"

    _, _, dsn_escape_refs = count_service_data_bypasses(source)

    assert dsn_escape_refs == 1


def test_counts_getattr_form_dsn_escapes():
    # #187 getattr-escape closure: `getattr(x, "path"/"dsn_identity", ...)`
    # is the dynamic twin of the attribute escape and must be counted —
    # with or without the default argument.
    source = (
        'a = getattr(job_db, "path", None)\n'
        'b = getattr(job_db, "dsn_identity", "")\n'
        'c = getattr(job_db, "path")\n'
        'd = getattr(other, "unrelated", None)\n'
        "e = getattr(job_db, name_var)\n"
    )

    _, _, dsn_escape_refs = count_service_data_bypasses(source)

    assert dsn_escape_refs == 3


def test_collect_only_scans_services_root(tmp_path):
    write(tmp_path / "server/app/services/bypass.py", 'SQL = "SELECT 1 FROM t"\n')
    write(tmp_path / "server/app/routes/allowed.py", 'SQL = "SELECT 1 FROM t"\n')
    write(tmp_path / "server/app/jobs/queries/allowed.py", 'SQL = "SELECT 1 FROM t"\n')

    counts = collect_service_data_bypasses(tmp_path)

    assert list(counts) == ["server/app/services/bypass.py"]


def test_rejects_bypass_in_service_file_without_baseline_entry(tmp_path):
    write(tmp_path / "server/app/services/legacy.py", 'SQL = "SELECT * FROM t"\n')
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("no baseline entry" in error and "legacy.py" in error for error in errors)


def test_rejects_count_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\nB = "INSERT INTO t VALUES (1)"\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [1, 0, 0]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error and "legacy.py" in error for error in errors)


def test_rejects_db_primitive_growth_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        "from server.app.db.transaction import read_connection\n"
        "from server.app.db.transaction import write_transaction\n",
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [0, 1, 0]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error for error in errors)


def test_compares_each_counter_against_its_own_baseline_entry(tmp_path):
    # Tuple comparison is lexicographic: (1, 2, 0) > (2, 1, 0) is False, so a
    # single `>` check would let DB-primitive growth pass as long as the SQL
    # count dropped. Each counter must be compared against its own entry.
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\n'
        "from server.app.db.transaction import read_connection\n"
        "from server.app.db.transaction import write_transaction\n",
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [2, 1, 0]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error and "legacy.py" in error for error in errors)


def test_rejects_package_level_db_import_without_baseline_entry(tmp_path):
    write(
        tmp_path / "server/app/services/sneaky.py",
        "from server.app.db import transaction\n",
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("no baseline entry" in error and "sneaky.py" in error for error in errors)


def test_rejects_dsn_path_escape_without_baseline_entry(tmp_path):
    write(
        tmp_path / "server/app/services/sneaky.py",
        "import psycopg\nconn = psycopg.connect(job_db.path)\n",
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any(
        "no baseline entry" in error and "sneaky.py" in error and "DSN escape" in error
        for error in errors
    )


def test_rejects_dsn_path_growth_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\nB = job_db.path\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [1, 0, 0]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error and "legacy.py" in error for error in errors)


def test_rejects_getattr_form_escape_without_baseline_entry(tmp_path):
    # The #187 getattr escape: pulling the DSN out of a facade-shaped object
    # via getattr must trip the ratchet exactly like the attribute form.
    write(
        tmp_path / "server/app/services/sneaky.py",
        'dsn = str(getattr(job_db, "path", "") or "")\n',
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any(
        "no baseline entry" in error and "sneaky.py" in error and "DSN" in error for error in errors
    )


def test_rejects_getattr_form_growth_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\nB = getattr(job_db, "dsn_identity", None)\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [1, 0, 0]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error and "legacy.py" in error for error in errors)


def test_rejects_dsn_identity_attribute_escape_without_baseline_entry(tmp_path):
    write(
        tmp_path / "server/app/services/sneaky.py",
        "identity = job_db.dsn_identity\n",
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any(
        "no baseline entry" in error and "sneaky.py" in error and "DSN" in error for error in errors
    )


def test_accepts_counts_within_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\nB = "INSERT INTO t VALUES (1)"\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [2, 0, 0]})

    errors = check_repository(tmp_path)

    assert not any("baseline entry" in error or "exceeds baseline" in error for error in errors)


def test_accepts_facade_only_service_file(tmp_path):
    write(tmp_path / "server/app/services/clean.py", "X = job_db.get_job(1)\n")
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert not any("baseline" in error and "clean.py" in error for error in errors)


def test_missing_baseline_is_configuration_error(tmp_path):
    (tmp_path / "server/app/services").mkdir(parents=True)

    errors = check_service_data_boundary(tmp_path)

    assert any("service data boundary configuration" in error for error in errors)


def test_malformed_baseline_is_configuration_error(tmp_path):
    write(
        tmp_path / "config/architecture/service-data-boundary-baseline.json",
        "{not json",
    )

    errors = check_service_data_boundary(tmp_path)

    assert any("service data boundary configuration" in error for error in errors)


def test_baseline_rejects_zero_zero_entries(tmp_path):
    write(tmp_path / "server/app/services/legacy.py", 'A = "SELECT 1"\n')
    baseline = tmp_path / "config/architecture/service-data-boundary-baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        json.dumps({"version": 1, "files": {"server/app/services/legacy.py": [0, 0, 0]}}),
        encoding="utf-8",
    )

    errors = check_service_data_boundary(tmp_path)

    assert any("must record at least one bypass" in error for error in errors)


def test_current_repo_passes_its_own_baseline():
    # The committed baseline must satisfy the real tree (the ratchet starts
    # frozen at the current counts, so this only fails if the checker's
    # semantics drift from what generated it).
    root = Path(__file__).resolve().parents[2]

    errors = check_service_data_boundary(root)

    assert errors == []


def _boundary_git_repo(tmp_path: Path, entries: dict[str, list[int]]) -> Path:
    """Build a real git repo whose HEAD^ carries a boundary baseline.

    An empty seed commit keeps HEAD^ resolvable (mirrors the budget
    monotonicity fixtures); the baseline JSON (and its service files) commit
    into HEAD^ — the pre-change anchor — so the working-tree edit plays the
    raise attempt against genuine history.
    """
    import subprocess

    root = tmp_path
    write_boundary_baseline(root, entries)
    for path in entries:
        write(root / path, 'A = "SELECT 1"\n')
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init baseline"],
        # A trailing commit so the baseline lands in HEAD^ (the pre-change
        # anchor), leaving HEAD as the working tree's comparison point.
        ["git", "commit", "-q", "--allow-empty", "-m", "trailing"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    return root


def test_monotonic_guard_rejects_raised_count(tmp_path: Path):
    root = _boundary_git_repo(tmp_path, {"server/app/services/legacy.py": [3, 1, 0]})
    write_boundary_baseline(root, {"server/app/services/legacy.py": [4, 1, 0]})

    errors = check_service_data_boundary(root)

    assert any(
        "baseline triple [4, 1, 0] rose above committed floor [3, 1, 0]" in error
        for error in errors
    )


def test_monotonic_guard_rejects_new_entry_for_tracked_file(tmp_path: Path):
    # The add-entry-to-pass channel #292 exists to close: a service file that
    # HEAD already tracks but the baseline did not register must not gain a
    # first entry by a silent edit.
    import subprocess

    root = tmp_path
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    # The veteran service predates this change: it is committed into HEAD^
    # (the pre-change anchor), so a baseline entry appearing now is new debt
    # on a tracked file — including when the attacker commits the entry in
    # the same change (codex review on #305).
    write(root / "server/app/services/veteran.py", 'A = "SELECT 1"\n')
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "veteran service, no baseline"], cwd=root, check=True
    )
    write(root / "server/app/services/veteran2.py", 'B = "SELECT 2"\n')
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "HEAD layer"], cwd=root, check=True)
    write_boundary_baseline(root, {"server/app/services/veteran.py": [1, 0, 0]})

    errors = check_service_data_boundary(root)

    assert any(
        "baseline entry appeared for an already-tracked service file" in error for error in errors
    )


def test_monotonic_guard_accepts_first_entry_for_brand_new_file(tmp_path: Path):
    # A genuinely new service file registering its first baseline entry is
    # the legitimate first-time channel (the plain no-entry check still
    # governs it); the monotonic guard must not misfire.
    root = _boundary_git_repo(tmp_path, {})
    write(root / "server/app/services/fresh.py", 'A = "SELECT 1"\n')
    write_boundary_baseline(root, {"server/app/services/fresh.py": [1, 0, 0]})

    errors = check_service_data_boundary(root)

    assert not any("rose above committed" in error for error in errors)
    assert not any("appeared for an already-tracked" in error for error in errors)


def test_monotonic_guard_accepts_lowered_count(tmp_path):
    root = _boundary_git_repo(tmp_path, {"server/app/services/legacy.py": [3, 1, 0]})
    write_boundary_baseline(root, {"server/app/services/legacy.py": [2, 1, 0]})

    errors = check_service_data_boundary(root)

    assert not any("rose above committed" in error for error in errors)


def test_monotonic_guard_silent_without_git(tmp_path: Path):
    # Non-git checkouts have no committed anchor; the guard stays quiet
    # rather than failing (mirrors the budget guard).
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [3, 1, 0]})

    errors = check_service_data_boundary(tmp_path)

    assert not any("boundary monotonicity" in error for error in errors)


def test_monotonic_guard_rejects_committed_new_entry(tmp_path: Path):
    # codex review on #305: the attacker may COMMIT the new bypasses together
    # with their baseline entry — HEAD then carries the entry, so historic
    # evidence must come from HEAD^ only, never from HEAD itself.
    import subprocess

    root = tmp_path
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    write(root / "server/app/services/veteran.py", 'A = "SELECT 1"\n')
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "veteran without baseline"], cwd=root, check=True)
    # The attack: new bypasses on the tracked file + the baseline entry,
    # committed as one change.
    write_boundary_baseline(root, {"server/app/services/veteran.py": [1, 0, 0]})
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add baseline entry"], cwd=root, check=True)

    errors = check_service_data_boundary(root)

    assert any(
        "baseline entry appeared for an already-tracked service file" in error for error in errors
    )


def test_monotonic_guard_accepts_entry_for_file_created_this_change(tmp_path: Path):
    # The legitimate flip side: a service file created by THIS change (absent
    # from HEAD^) registering its first baseline entry in the same commit is
    # a new file, not new debt.
    import subprocess

    root = tmp_path
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    write(root / "server/app/services/fresh.py", 'A = "SELECT 1"\n')
    write_boundary_baseline(root, {"server/app/services/fresh.py": [1, 0, 0]})
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "new service with baseline"], cwd=root, check=True)

    errors = check_service_data_boundary(root)

    assert not any("appeared for an already-tracked service file" in error for error in errors)


def test_monotonic_guard_rejects_rename_count_reset(tmp_path: Path):
    # subagent review on #305: renaming a service file (and re-keying its
    # baseline entry) must not reset the boundary counts — the old path's
    # floor carries onto the new path (#236 semantics).
    import subprocess

    root = tmp_path
    write(root / "server/app/services/old_name.py", 'A = "SELECT 1"\n')
    write_boundary_baseline(root, {"server/app/services/old_name.py": [3, 1, 0]})
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "baseline under old name"],
        ["git", "commit", "-q", "--allow-empty", "-m", "trailing"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    # The attack: rename + re-key with inflated counts.
    (root / "server/app/services/old_name.py").rename(root / "server/app/services/new_name.py")
    write_boundary_baseline(root, {"server/app/services/new_name.py": [9, 9, 9]})

    errors = check_service_data_boundary(root)

    assert any("rose above committed floor" in error for error in errors)
