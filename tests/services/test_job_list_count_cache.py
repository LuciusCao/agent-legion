"""First-screen aggregate TTL cache (issue #358).

The workspace job list's first page and facets call both hit
``count_jobs_filtered`` + the facet group-bys — a full scan of the
workspace's filtered jobs slice each time. ``JobListQueryService`` caches
the raw ``job_facets`` result per (workspace, filter) for a short TTL, so a
refresh storm collapses into one scan per window.
"""

from __future__ import annotations

import pytest

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_list_count_cache import TtlCache


class TestTtlCache:
    def test_first_call_computes_and_caches(self) -> None:
        cache = TtlCache(ttl_seconds=60)
        calls = []

        def compute() -> int:
            calls.append(1)
            return len(calls)

        assert cache.get_or_compute("k", compute) == 1
        assert cache.get_or_compute("k", compute) == 1
        assert len(calls) == 1

    def test_expired_entry_recomputes(self) -> None:
        cache = TtlCache(ttl_seconds=0.0)
        calls = []

        def compute() -> int:
            calls.append(1)
            return len(calls)

        cache.get_or_compute("k", compute)
        # TTL 0: the entry is always stale, so every read recomputes.
        cache.get_or_compute("k", compute)
        assert len(calls) == 2

    def test_distinct_keys_compute_independently(self) -> None:
        cache = TtlCache(ttl_seconds=60)
        assert cache.get_or_compute("a", lambda: "va") == "va"
        assert cache.get_or_compute("b", lambda: "vb") == "vb"

    def test_soft_bound_drops_oldest_entries(self) -> None:
        cache = TtlCache(ttl_seconds=60, max_entries=2)
        cache.get_or_compute("a", lambda: 1)
        cache.get_or_compute("b", lambda: 2)
        cache.get_or_compute("c", lambda: 3)
        # Inserting "c" beyond the bound evicted "a" (oldest); "b" survives.
        assert cache.get_or_compute("b", lambda: "recomputed") == 2
        assert cache.get_or_compute("a", lambda: "recomputed") == "recomputed"

    def test_clear_empties_the_cache(self) -> None:
        cache = TtlCache(ttl_seconds=60)
        cache.get_or_compute("k", lambda: 1)
        cache.clear()
        assert cache.get_or_compute("k", lambda: "fresh") == "fresh"


def _filter(**overrides) -> JobListFilter:
    return JobListFilter(**overrides)


@pytest.fixture
def list_service(job_db, settings):
    from server.app.services.job_list_queries import JobListQueryService

    return JobListQueryService(job_db, settings)


def _make_workspace(job_db, slug: str) -> str:
    from tests.helpers import publish_builtin_revision

    workspace = job_db.create_workspace(slug, default_workflow_key=slug)
    publish_builtin_revision(job_db, workspace["id"])
    return workspace["id"]


def _insert_job(job_db, workspace_id: str, source_id: str, status: str = "queued") -> None:
    job_db.create_job(
        workspace_id=workspace_id,
        workflow_key=workspace_id,
        source_type="question_id",
        source_id=source_id,
        run_id="",
        title=f"job {source_id}",
        node_keys=[],
    )
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set status = %s where id = %s",
            (status, f"{workspace_id}_{workspace_id}_{source_id}"),
        )


def test_facets_cached_within_ttl(list_service, job_db, monkeypatch) -> None:
    workspace_id = _make_workspace(job_db, "list-cache-ws")
    _insert_job(job_db, workspace_id, "j-1")
    first = list_service.facets(workspace_id, _filter())
    assert first["total"] == 1
    # A second call inside the TTL returns the cached aggregates even though
    # the underlying data changed.
    _insert_job(job_db, workspace_id, "j-2")
    cached = list_service.facets(workspace_id, _filter())
    assert cached["total"] == 1
    assert cached == first


def test_facets_recompute_after_ttl(list_service, job_db, monkeypatch) -> None:
    workspace_id = _make_workspace(job_db, "list-cache-ttl-ws")
    _insert_job(job_db, workspace_id, "j-1")
    monkeypatch.setattr(list_service._aggregate_cache, "_ttl", 0.0)
    list_service.facets(workspace_id, _filter())
    _insert_job(job_db, workspace_id, "j-2")
    fresh = list_service.facets(workspace_id, _filter())
    assert fresh["total"] == 2


def test_page_total_rides_the_same_cache_entry(list_service, job_db) -> None:
    """page()'s filtered total and facets() share one cached computation."""
    workspace_id = _make_workspace(job_db, "list-cache-page-ws")
    _insert_job(job_db, workspace_id, "j-1")
    page = list_service.page(workspace_id, _filter(), limit=10)
    assert page["total"] == 1
    # The facet entry computed for the page is now warm: a facets() call
    # with the same filter must not add a second cache entry.
    entries_before = len(list_service._aggregate_cache._entries)
    facets = list_service.facets(workspace_id, _filter())
    assert facets["total"] == 1
    assert len(list_service._aggregate_cache._entries) == entries_before


def test_page_cursor_pages_skip_the_aggregates(list_service, job_db) -> None:
    workspace_id = _make_workspace(job_db, "list-cache-cursor-ws")
    for i in range(3):
        _insert_job(job_db, workspace_id, f"j-{i}")
    first = list_service.page(workspace_id, _filter(), limit=2)
    assert first["next_cursor"] is not None
    entries_after_first = len(list_service._aggregate_cache._entries)
    cursor_page = list_service.page(workspace_id, _filter(), limit=2, cursor=first["next_cursor"])
    assert cursor_page["total"] is None
    assert cursor_page["stats"] == {}
    # Cursor pages never touch the aggregate cache.
    assert len(list_service._aggregate_cache._entries) == entries_after_first


def test_distinct_filters_cache_independently(list_service, job_db) -> None:
    workspace_id = _make_workspace(job_db, "list-cache-filters-ws")
    _insert_job(job_db, workspace_id, "j-1", status="running")
    _insert_job(job_db, workspace_id, "j-2", status="completed")
    running = list_service.facets(workspace_id, _filter(status="running"))
    assert running["total"] == 1
    # A different filter is a different cache key: computed fresh.
    completed = list_service.facets(workspace_id, _filter(status="completed"))
    assert completed["total"] == 1
    unfiltered = list_service.facets(workspace_id, _filter())
    assert unfiltered["total"] == 2
    # Same filter again hits the cache (data unchanged → same numbers).
    assert list_service.facets(workspace_id, _filter(status="running")) == running


def test_workspaces_are_isolated_by_cache_key(list_service, job_db) -> None:
    ws_a = _make_workspace(job_db, "list-cache-ws-a")
    ws_b = _make_workspace(job_db, "list-cache-ws-b")
    _insert_job(job_db, ws_a, "j-1")
    _insert_job(job_db, ws_b, "j-1")
    _insert_job(job_db, ws_b, "j-2")
    assert list_service.facets(ws_a, _filter())["total"] == 1
    assert list_service.facets(ws_b, _filter())["total"] == 2
