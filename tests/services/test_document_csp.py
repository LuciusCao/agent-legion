"""CspCompatSwitch（#989）：TTL 缓存、invalidate、非 True 一律严格、读失败 fail closed。"""

from __future__ import annotations

import psycopg
import pytest

from server.app.services.document_csp import CspCompatSwitch


class _Store:
    def __init__(self, document):
        self.document = document
        self.reads = 0

    def get(self):
        self.reads += 1
        if isinstance(self.document, Exception):
            raise self.document
        return self.document


def _switch(document, now):
    switch = CspCompatSwitch("postgresql://unused", ttl_seconds=5.0, clock=lambda: now[0])
    store = _Store(document)
    switch._store = store
    return switch, store


@pytest.mark.no_db
def test_value_is_cached_for_the_ttl_and_invalidate_refreshes() -> None:
    now = [100.0]
    switch, store = _switch({"csp_script_unsafe_inline": True}, now)
    assert switch.enabled() is True
    store.document = {"csp_script_unsafe_inline": False}
    now[0] = 104.9
    assert switch.enabled() is True and store.reads == 1
    now[0] = 105.0
    assert switch.enabled() is False and store.reads == 2
    store.document = {"csp_script_unsafe_inline": True}
    switch.invalidate()
    assert switch.enabled() is True and store.reads == 3


@pytest.mark.no_db
@pytest.mark.parametrize(
    "document", [None, {}, {"csp_script_unsafe_inline": "true"}, {"csp_script_unsafe_inline": 1}]
)
def test_anything_but_true_is_strict(document) -> None:
    switch, _ = _switch(document, [0.0])
    assert switch.enabled() is False


@pytest.mark.no_db
def test_read_failure_fails_closed_without_caching() -> None:
    switch, store = _switch(psycopg.OperationalError("down"), [0.0])
    assert switch.enabled() is False
    store.document = {"csp_script_unsafe_inline": True}
    assert switch.enabled() is True and store.reads == 2
