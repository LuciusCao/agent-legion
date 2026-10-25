"""Tests for scripts/architecture/nested_pool_checkout.py (#1308, DB-POOL-CHECKOUT-001).

The fixtures replay the #1239 census shapes (M16/M18): the A2 lexical shape
(helper opening its own transaction inside the caller's read block), the A3
facade shape (JobQueries self-checkout method via a db-like receiver), the
conn-first shadow false hits (workspace.py:310 / leases.py:120), and the
deferred-callback exemption (approval_decisions on_guarded).
"""

import json
from pathlib import Path

import pytest

from scripts.architecture.nested_pool_checkout import check_nested_pool_checkout

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_allowlist(root: Path, entries: dict[str, str] | None = None) -> None:
    payload = {"version": 1, "allowlist": entries or {}}
    write(
        root / "config/architecture/nested-pool-checkout-allowlist.json",
        json.dumps(payload, indent=2) + "\n",
    )


def fixture_root(tmp_path: Path) -> Path:
    """A minimal repo: the transaction helpers plus an empty allowlist."""
    write(
        tmp_path / "server/app/db/transaction.py",
        "def read_connection(dsn): ...\n"
        "def write_transaction(dsn): ...\n"
        "def connect_database(dsn): ...\n",
    )
    write_allowlist(tmp_path)
    return tmp_path


def test_flags_helper_opening_own_transaction_inside_read_block(tmp_path):
    """A2 shape: the callee opens its own write checkout under the held read."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/diagnostics.py",
        "from server.app.db.transaction import write_transaction\n\n"
        "def log_signal(dsn):\n"
        "    with write_transaction(dsn) as conn:\n"
        "        conn.execute('insert')\n",
    )
    write(
        root / "server/app/probe.py",
        "from server.app.db.transaction import read_connection\n"
        "from server.app.diagnostics import log_signal\n\n"
        "def probe(dsn):\n"
        "    with read_connection(dsn) as conn:\n"
        "        row = conn.execute('select 1')\n"
        "        if row:\n"
        "            log_signal(dsn)\n",
    )

    errors = check_nested_pool_checkout(root)

    assert any("probe.py:8" in error and "log_signal" in error for error in errors)


def test_flags_facade_self_checkout_method_on_db_like_receiver(tmp_path):
    """A3 shape: with job_db.connect() block calling a self-checkout method."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/jobs/queries/reads.py",
        "class ReadsMixin:\n"
        "    def read(self): ...\n"
        "    def get_active_revision(self):\n"
        "        with self.read() as conn:\n"
        "            return conn.execute('select 1')\n",
    )
    write(
        root / "tests/db/test_example.py",
        "def test_x(job_db):\n"
        "    with job_db.connect() as conn_a:\n"
        "        rev = job_db.get_active_revision()\n",
    )

    errors = check_nested_pool_checkout(root)

    assert any("test_example.py:3" in error and "get_active_revision" in error for error in errors)


def test_flags_lexically_nested_checkout_in_production_only(tmp_path):
    root = fixture_root(tmp_path)
    nested = (
        "from server.app.db.transaction import read_connection, write_transaction\n\n"
        "def f(dsn):\n"
        "    with read_connection(dsn) as conn:\n"
        "        with write_transaction(dsn) as wconn:\n"
        "            wconn.execute('insert')\n"
    )
    write(root / "server/app/prod.py", nested)
    write(root / "tests/db/test_race.py", nested)  # concurrency fixtures nest deliberately

    errors = check_nested_pool_checkout(root)

    assert any("prod.py:5" in error for error in errors)
    assert not any("test_race.py" in error for error in errors)


def test_conn_first_shadow_call_is_not_flagged(tmp_path):
    """workspace.py:310 shape: an imported conn-first module function whose name
    matches a JobQueries self-checkout method resolves to the import, not the method."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/jobs/queries/reads.py",
        "class ReadsMixin:\n"
        "    def read(self): ...\n"
        "    def read_node_config(self, key):\n"
        "        with self.read() as conn:\n"
        "            return conn.execute('select 1')\n",
    )
    write(
        root / "server/app/jobs/node_limits.py",
        "def read_node_config(conn, key):\n    return conn.execute('select 1')\n",
    )
    write(
        root / "server/app/services/workspace.py",
        "from server.app.jobs.node_limits import read_node_config\n\n"
        "def update(job_db, key):\n"
        "    with job_db.read() as conn:\n"
        "        row = read_node_config(conn, key)\n",
    )

    errors = check_nested_pool_checkout(root)

    assert not any("workspace.py" in error for error in errors)


def test_conn_first_function_calling_self_checkout_is_flagged(tmp_path):
    """The riding→fresh transition: a conn-first rider must not acquire."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/gate.py",
        "from server.app.db.transaction import read_connection\n\n"
        "def gate_probe(dsn):\n"
        "    with read_connection(dsn) as conn:\n"
        "        return conn.execute('select 1')\n",
    )
    write(
        root / "server/app/admission.py",
        "from server.app.gate import gate_probe\n\n"
        "def admit(conn, broker):\n"
        "    if gate_probe(broker.database_dsn):\n"
        "        return None\n"
        "    return conn.execute('select 1')\n",
    )

    errors = check_nested_pool_checkout(root)

    assert any(
        "admission.py:4" in error and "conn-first" in error and "gate_probe" in error
        for error in errors
    )


def test_conn_first_facade_self_checkout_is_flagged(tmp_path):
    """#1379: instance-receiver facade calls have no import candidate, but
    they still make the riding→fresh transition."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/jobs/queries/reads.py",
        "class ReadsMixin:\n"
        "    def read(self): ...\n"
        "    def fresh(self):\n"
        "        with self.read() as conn:\n"
        "            return conn.execute('select 1')\n"
        "    def ride(self, conn):\n"
        "        return conn.execute('select 1')\n",
    )
    write(
        root / "server/app/admission.py",
        "def admit(conn, job_db):\n    job_db.fresh()\n    return job_db.ride(conn)\n",
    )

    errors = check_nested_pool_checkout(root)

    assert any(
        "admission.py:2" in error and "conn-first" in error and "fresh" in error for error in errors
    )
    assert not any("admission.py:3" in error for error in errors)


def test_conn_first_ride_chain_is_not_flagged(tmp_path):
    """A conn-first helper calling another conn-first helper keeps riding."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/inner.py",
        "def lock_row(conn, key):\n    return conn.execute('select 1')\n",
    )
    write(
        root / "server/app/outer.py",
        "from server.app.inner import lock_row\n\n"
        "def promote(conn, key):\n"
        "    return lock_row(conn, key)\n",
    )

    errors = check_nested_pool_checkout(root)

    assert not errors


def test_deferred_callback_bodies_are_not_scanned(tmp_path):
    """on_guarded shape: callbacks run after the checkout is released."""
    root = fixture_root(tmp_path)
    write(
        root / "server/app/service.py",
        "from server.app.db.transaction import read_connection, write_transaction\n\n"
        "def act(dsn, register):\n"
        "    with read_connection(dsn) as conn:\n"
        "        def on_commit():\n"
        "            with write_transaction(dsn) as wconn:\n"
        "                wconn.execute('insert')\n"
        "        register(on_commit)\n",
    )

    errors = check_nested_pool_checkout(root)

    assert not errors


def test_sequential_checkouts_are_not_flagged(tmp_path):
    root = fixture_root(tmp_path)
    write(
        root / "server/app/sequential.py",
        "from server.app.db.transaction import read_connection, write_transaction\n\n"
        "def act(dsn):\n"
        "    with read_connection(dsn) as conn:\n"
        "        row = conn.execute('select 1')\n"
        "    with write_transaction(dsn) as wconn:\n"
        "        wconn.execute('insert')\n",
    )

    errors = check_nested_pool_checkout(root)

    assert not errors


def test_allowlist_suppresses_and_stale_entries_fail(tmp_path):
    root = fixture_root(tmp_path)
    write(
        root / "server/app/diagnostics.py",
        "from server.app.db.transaction import write_transaction\n\n"
        "def log_signal(dsn):\n"
        "    with write_transaction(dsn) as conn:\n"
        "        conn.execute('insert')\n",
    )
    write(
        root / "server/app/probe.py",
        "from server.app.db.transaction import read_connection\n"
        "from server.app.diagnostics import log_signal\n\n"
        "def probe(dsn):\n"
        "    with read_connection(dsn) as conn:\n"
        "        log_signal(dsn)\n",
    )
    write_allowlist(root, {"server/app/probe.py:6": "known instance, tracked elsewhere"})

    assert not check_nested_pool_checkout(root)

    write_allowlist(
        root,
        {
            "server/app/probe.py:6": "known instance",
            "server/app/gone.py:1": "stale entry",
        },
    )
    errors = check_nested_pool_checkout(root)
    assert any("stale" in error and "gone.py" in error for error in errors)


def test_missing_allowlist_behaves_as_empty(tmp_path):
    """No config file: clean trees pass, hits still fail unallowlisted."""
    write(tmp_path / "server/app/sequential.py", "X = 1\n")
    assert not check_nested_pool_checkout(tmp_path)

    write(
        tmp_path / "server/app/diagnostics.py",
        "from server.app.db.transaction import write_transaction\n\n"
        "def log_signal(dsn):\n"
        "    with write_transaction(dsn) as conn:\n"
        "        conn.execute('insert')\n",
    )
    write(
        tmp_path / "server/app/probe.py",
        "from server.app.db.transaction import read_connection\n"
        "from server.app.diagnostics import log_signal\n\n"
        "def probe(dsn):\n"
        "    with read_connection(dsn) as conn:\n"
        "        log_signal(dsn)\n",
    )
    errors = check_nested_pool_checkout(tmp_path)
    assert any("probe.py:6" in error for error in errors)
