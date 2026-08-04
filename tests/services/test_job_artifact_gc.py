from server.app.services.job_artifact_gc import gc_deleted_job_artifacts, read_artifact_candidates


class _FakeStore:
    def __init__(
        self,
        refs: list[dict] | None = None,
        orphaned: list[str] | None = None,
        fail_on: str | None = None,
    ) -> None:
        self._refs = refs or []
        self._orphaned = orphaned or []
        self._fail_on = fail_on
        self.deleted_unreferenced: list[str] | None = None

    def refs_for_job(self, job_id: str) -> list[dict]:
        if self._fail_on == "refs_for_job":
            raise RuntimeError("db down")
        return self._refs

    def delete_refs_for_job(self, job_id: str) -> list[str]:
        if self._fail_on == "delete_refs_for_job":
            raise RuntimeError("db down")
        return self._orphaned

    def delete_unreferenced(self, hashes: list[str]) -> int:
        if self._fail_on == "delete_unreferenced":
            raise RuntimeError("db down")
        self.deleted_unreferenced = hashes
        return len(hashes)


def test_read_candidates_returns_hashes_from_refs():
    store = _FakeStore(refs=[{"hash": "a"}, {"hash": "b"}])
    assert read_artifact_candidates(store, "job-1") == ["a", "b"]


def test_read_candidates_empty_when_store_is_none():
    assert read_artifact_candidates(None, "job-1") == []


def test_read_candidates_degrades_to_empty_on_failure():
    store = _FakeStore(fail_on="refs_for_job")
    assert read_artifact_candidates(store, "job-1") == []


def test_gc_merges_and_dedupes_candidates_and_orphans():
    store = _FakeStore(orphaned=["b", "c"])
    gc_deleted_job_artifacts(store, "job-1", ["a", "b"])
    assert store.deleted_unreferenced == ["a", "b", "c"]


def test_gc_skips_delete_unreferenced_when_no_hashes():
    store = _FakeStore(orphaned=[])
    gc_deleted_job_artifacts(store, "job-1", [])
    assert store.deleted_unreferenced is None


def test_gc_noop_when_store_is_none():
    gc_deleted_job_artifacts(None, "job-1", ["a"])


def test_gc_swallows_delete_refs_failure():
    store = _FakeStore(fail_on="delete_refs_for_job")
    gc_deleted_job_artifacts(store, "job-1", ["a"])
    assert store.deleted_unreferenced is None


def test_gc_swallows_delete_unreferenced_failure():
    store = _FakeStore(fail_on="delete_unreferenced")
    gc_deleted_job_artifacts(store, "job-1", ["a"])
    assert store.deleted_unreferenced is None
