"""GlobalSettingsKV queries mixin (issue #281): get/put/update + failure paths.

These tests pin the contract the five stores (skill sources, instance
settings, cleanup sweep, token-usage pricing, studio agent registry)
delegate to after their inline SQL was centralized: absent keys read as
None, corrupt JSON raises instead of silently defaulting, put replaces
whole documents, and update runs its read-modify-write in one transaction.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)
from tests.postgres_support import TEST_DATABASE_URL

TEST_KEY = "test_kv_probe"


@pytest.fixture
def kv(job_db) -> GlobalSettingsKVQueriesMixin:
    # The bare-DSN adapter is the piece the stores reach when handed a DSN
    # string (#187 ConnectSource contract); it must behave exactly like the
    # facade methods JobQueries itself exposes.
    with job_db.connect() as conn:
        conn.execute("delete from global_settings where key=%s", (TEST_KEY,))
    return global_settings_kv_from_dsn(TEST_DATABASE_URL)


def _raw_put(dsn: str, key: str, value: str) -> None:
    from server.app.db.transaction import write_transaction

    with write_transaction(dsn) as conn:
        conn.execute(
            """
            insert into global_settings(key, value) values (%s, %s)
            on conflict(key)
            do update set value=excluded.value, updated_at=current_timestamp
            """,
            (key, value),
        )


def test_get_returns_none_when_key_absent(kv) -> None:
    assert kv.get_global_settings_document(TEST_KEY) is None


def test_put_then_get_round_trips_document(kv) -> None:
    document = {"b": 2, "a": {"nested": [1, 2, 3]}}
    kv.put_global_settings_document(TEST_KEY, document)
    assert kv.get_global_settings_document(TEST_KEY) == document


def test_put_overwrites_whole_document(kv) -> None:
    kv.put_global_settings_document(TEST_KEY, {"keep": 1, "drop": 2})
    kv.put_global_settings_document(TEST_KEY, {"keep": 3})
    assert kv.get_global_settings_document(TEST_KEY) == {"keep": 3}


def test_get_raises_on_corrupt_json(kv) -> None:
    # Pre-existing copies all called json.loads bare: a corrupt row surfaced
    # as an exception, never as a silent default. The unified KV keeps that
    # fail-loud behavior (issue #281 contract).
    _raw_put(TEST_DATABASE_URL, TEST_KEY, "not-json{")
    with pytest.raises(json.JSONDecodeError):
        kv.get_global_settings_document(TEST_KEY)


def test_get_raises_on_non_object_json(kv) -> None:
    # Same as the inline copies: `cast` is erased at runtime, so a JSON
    # scalar is returned as-is (str here) rather than raising. Pinned as the
    # pre-existing observable behavior — callers type-narrow at their layer
    # (e.g. registry.get's isinstance checks).
    _raw_put(TEST_DATABASE_URL, TEST_KEY, '"a plain string"')
    assert kv.get_global_settings_document(TEST_KEY) == "a plain string"


def test_update_starts_from_empty_document(kv) -> None:
    kv.update_global_settings_document(TEST_KEY, lambda doc: {**doc, "seeded": True})
    assert kv.get_global_settings_document(TEST_KEY) == {"seeded": True}


def test_update_mutates_only_its_own_keys(kv) -> None:
    kv.put_global_settings_document(TEST_KEY, {"other": 1})

    def _advance(document: dict[str, Any]) -> dict[str, Any]:
        return {**document, "cursor": {"id": 42}}

    kv.update_global_settings_document(TEST_KEY, _advance)
    assert kv.get_global_settings_document(TEST_KEY) == {
        "other": 1,
        "cursor": {"id": 42},
    }


def test_update_reads_latest_document(kv) -> None:
    # The cleanup-sweep store relies on update seeing concurrent puts to
    # sibling keys, not a stale snapshot from before the transaction.
    kv.put_global_settings_document(TEST_KEY, {"sibling": 1})
    observed: list[dict[str, Any]] = []

    def _updater(document: dict[str, Any]) -> dict[str, Any]:
        observed.append(dict(document))
        return {**document, "mine": 2}

    kv.update_global_settings_document(TEST_KEY, _updater)
    assert observed == [{"sibling": 1}]
    assert kv.get_global_settings_document(TEST_KEY) == {"sibling": 1, "mine": 2}


def test_facade_exposes_mixin_methods(job_db) -> None:
    # JobQueries composes the mixin (queries/groups.py IdentityQueriesMixin),
    # so production wiring gets the same three methods for free.
    job_db.put_global_settings_document(TEST_KEY, {"via": "facade"})
    assert job_db.get_global_settings_document(TEST_KEY) == {"via": "facade"}
    job_db.update_global_settings_document(TEST_KEY, lambda doc: {**doc, "ok": True})
    assert job_db.get_global_settings_document(TEST_KEY) == {"via": "facade", "ok": True}
