"""Issue #124 residual: per-pass claim-input memos in the dispatch path.

One scheduling pass used to re-read the published code text and re-resolve
every vault secret_ref per claimed node. The workflow worker now memoizes
both per (pass, key), collapsing the repeated DB round trips on a congested
database. Code entries carry the publish generation, so an in-process
publish/rollback/archive lands on the very next claim; a secret write lands
on the next pass.
"""

from __future__ import annotations

from types import SimpleNamespace

from cryptography.fernet import Fernet

from server.app.db.transaction import write_transaction
from server.app.services.node_codes import NodeCodeService
from server.app.services.vault import VaultService
from server.app.workflow_worker import code_dispatch
from server.app.workflow_worker.code_dispatch import resolve_code_node_dispatch
from tests.postgres_support import TEST_DATABASE_URL

_CODE_V1 = "def run(ctx):\n    return {'v': 1}\n"
_CODE_V2 = "def run(ctx):\n    return {'v': 2}\n"


def _stub_worker() -> SimpleNamespace:
    # job_db mimics the JobQueries facade's connect surface (#187): services
    # hand the facade itself to connection helpers.
    return SimpleNamespace(
        job_db=SimpleNamespace(path=TEST_DATABASE_URL, dsn_identity=TEST_DATABASE_URL),
        settings=SimpleNamespace(
            executor_runtime=SimpleNamespace(workflows=SimpleNamespace(custom_nodes_enabled=True))
        ),
        state=SimpleNamespace(node_code_cache={}, secret_memo={}, batch_payload_cache={}),
    )


def _publish(workspace_id: str, node_key: str, code: str) -> None:
    service = NodeCodeService(TEST_DATABASE_URL)
    service.save_draft(workspace_id, "wf", node_key, code, "test")
    service.publish(workspace_id, "wf", node_key)


def _seed_workspace(workspace_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, %s, 'demo') on conflict do nothing",
            (workspace_id, workspace_id),
        )


def test_node_code_memoized_and_invalidated_by_publish(monkeypatch) -> None:
    _seed_workspace("ws-ncc")
    _publish("ws-ncc", "node-a", _CODE_V1)
    worker = _stub_worker()
    node = SimpleNamespace(key="node-a", capability="cap_a")

    reads = 0
    real_resolve = code_dispatch.resolve_dispatch_node_code

    def _counting_resolve(*args, **kwargs):
        nonlocal reads
        reads += 1
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(code_dispatch, "resolve_dispatch_node_code", _counting_resolve)

    # Two same-pass claims share one DB read.
    assert resolve_code_node_dispatch(worker, "ws-ncc", "wf", node, None) == _CODE_V1
    assert resolve_code_node_dispatch(worker, "ws-ncc", "wf", node, None) == _CODE_V1
    assert reads == 1

    # An in-process publish bumps the generation: the very next claim in the
    # same pass already serves the new code (the #115 contract).
    _publish("ws-ncc", "node-a", _CODE_V2)
    assert resolve_code_node_dispatch(worker, "ws-ncc", "wf", node, None) == _CODE_V2
    assert reads == 2


def test_node_code_unpublished_memoized_until_published() -> None:
    _seed_workspace("ws-ncc2")
    worker = _stub_worker()
    node = SimpleNamespace(key="node-missing", capability="cap_missing")
    for _ in range(2):
        try:
            resolve_code_node_dispatch(worker, "ws-ncc2", "wf", node, None)
        except ValueError:
            pass
        else:  # pragma: no cover - the node has no published code
            raise AssertionError("unpublished node must fail fast")
    generation, code = worker.state.node_code_cache[("ws-ncc2", "wf", "node-missing")]
    assert code is None
    # Publishing the missing node invalidates the memoized None in the same
    # pass: the next claim resolves the fresh code.
    _publish("ws-ncc2", "node-missing", _CODE_V1)
    assert resolve_code_node_dispatch(worker, "ws-ncc2", "wf", node, None) == _CODE_V1


def test_vault_memo_shares_one_read_and_drops_on_write(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-vault', 'ws-vault', 'demo') on conflict do nothing"
        )
    memo: dict[tuple[str, str], str | None] = {}
    vault = VaultService(TEST_DATABASE_URL, memo=memo)

    vault.set("ws-vault", "api-token", "secret-1")
    assert vault.get("ws-vault", "api-token") == "secret-1"
    assert memo[("ws-vault", "api-token")] == "secret-1"

    # A memo hit does not touch the DB: the row can vanish underneath and the
    # same pass still resolves (staleness bounded by the pass lifetime).
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "delete from workspace_secrets where workspace_id='ws-vault' and name='api-token'"
        )
    assert vault.get("ws-vault", "api-token") == "secret-1"

    # Writes through the memoized instance drop the entry immediately.
    vault.set("ws-vault", "api-token", "secret-2")
    assert ("ws-vault", "api-token") not in memo
    assert vault.get("ws-vault", "api-token") == "secret-2"
    vault.delete("ws-vault", "api-token")
    assert ("ws-vault", "api-token") not in memo
    # Missing secrets are memoized too (None), so repeated references to an
    # absent name cost one read per pass.
    assert vault.get("ws-vault", "api-token") is None
    assert memo[("ws-vault", "api-token")] is None
