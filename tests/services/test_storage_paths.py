import contextlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from server.app.storage_paths import (
    ManagedPathError,
    ensure_dir_once,
    job_log_dir,
    make_data_relative,
    resolve_data_path,
    resolve_job_dir,
    resolve_managed_path,
)


@pytest.fixture
def managed_root(tmp_path: Path) -> Path:
    root = tmp_path / "managed"
    root.mkdir()
    return root


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


class TestResolveManagedPath:
    def test_normal_child(self, managed_root: Path) -> None:
        child = managed_root / "child"
        child.mkdir()

        result = resolve_managed_path(managed_root, "child", allow_missing=False)

        assert result == child.resolve()

    def test_relative_path_is_relative_to_root(self, managed_root: Path) -> None:
        nested = managed_root / "nested" / "leaf.txt"
        nested.parent.mkdir()
        nested.write_text("x")

        result = resolve_managed_path(managed_root, "nested/leaf.txt", allow_missing=False)

        assert result == nested.resolve()

    def test_dotdot_sibling_rejected(self, managed_root: Path) -> None:
        sibling = managed_root.parent / "sibling"
        sibling.mkdir()

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(
                managed_root,
                "../sibling",
                allow_missing=False,
                record_id="video-1",
                root_kind="video",
            )

        message = str(exc_info.value)
        assert "video" in message
        assert "video-1" in message
        assert "../sibling" not in message
        assert exc_info.value.record_id == "video-1"
        assert exc_info.value.root_kind == "video"

    def test_absolute_outside_rejected(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside"
        outside.mkdir()
        target = outside / "file.txt"

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(
                managed_root,
                str(target),
                allow_missing=True,
                record_id="job-1",
                root_kind="job",
            )

        message = str(exc_info.value)
        assert "job" in message
        assert "job-1" in message
        assert str(outside) not in message
        assert str(target) not in message

    def test_internal_symlink_to_outside_rejected(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside_target"
        outside.mkdir()
        outside_file = outside / "secret.txt"
        outside_file.write_text("secret")
        link = managed_root / "link"
        link.symlink_to(outside)

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(
                managed_root,
                "link/secret.txt",
                allow_missing=False,
                record_id="video-2",
                root_kind="video",
            )

        message = str(exc_info.value)
        assert "video" in message
        assert str(outside) not in message
        assert "secret" not in message

    def test_missing_leaf_below_symlink_parent_rejected(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside_target"
        outside.mkdir()
        link = managed_root / "link"
        link.symlink_to(outside)

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(
                managed_root,
                "link/missing_leaf",
                allow_missing=True,
                record_id="job-2",
                root_kind="job",
            )

        message = str(exc_info.value)
        assert "job" in message
        assert str(outside) not in message

    def test_missing_leaf_below_broken_symlink_parent_rejected(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside_missing"
        link = managed_root / "broken_link"
        link.symlink_to(outside, target_is_directory=True)

        with pytest.raises(ManagedPathError):
            resolve_managed_path(
                managed_root,
                "broken_link/missing_leaf",
                allow_missing=True,
                record_id="job-broken-link",
                root_kind="job",
            )

    def test_managed_root_itself_rejected(self, managed_root: Path) -> None:
        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(managed_root, ".", allow_missing=False)

        assert "managed" in str(exc_info.value).lower()

    def test_expanduser_outside_rejected(
        self, managed_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = managed_root.parent / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(
                managed_root,
                "~/file.txt",
                allow_missing=True,
                record_id="video-3",
                root_kind="video",
            )

        assert "video" in str(exc_info.value)
        assert str(home) not in str(exc_info.value)

    def test_missing_leaf_inside_allowed(self, managed_root: Path) -> None:
        result = resolve_managed_path(
            managed_root,
            "nested/missing.txt",
            allow_missing=True,
            record_id="video-4",
            root_kind="video",
        )

        assert result == (managed_root / "nested" / "missing.txt").resolve()

    def test_missing_outside_path_disallowed(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside"
        outside.mkdir()

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(
                managed_root,
                str(outside / "missing.txt"),
                allow_missing=False,
                record_id="video-5",
                root_kind="video",
            )

        assert "video" in str(exc_info.value)
        assert str(outside) not in str(exc_info.value)

    def test_missing_inside_path_disallowed_when_allow_missing_is_false(
        self, managed_root: Path
    ) -> None:
        with pytest.raises(ManagedPathError, match="does not exist"):
            resolve_managed_path(
                managed_root,
                "missing.txt",
                allow_missing=False,
                record_id="video-strict",
                root_kind="video",
            )


class TestMakeDataRelative:
    def test_canonical_jobs_path(self, data_dir: Path) -> None:
        path = data_dir / "jobs" / "ws" / "job" / "runs" / "node" / "token" / "session"
        path.mkdir(parents=True)

        relative = make_data_relative(path, data_dir)

        assert relative == "jobs/ws/job/runs/node/token/session"

    def test_all_canonical_prefixes(self, data_dir: Path) -> None:
        for prefix in ("videos", "jobs", "logs", "packages"):
            path = data_dir / prefix / "item"
            path.mkdir(parents=True)

            relative = make_data_relative(path, data_dir)

            assert relative == f"{prefix}/item"

    def test_rejects_data_dir_itself(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            make_data_relative(data_dir, data_dir)

    def test_rejects_outside_path(self, data_dir: Path) -> None:
        outside = data_dir.parent / "outside"
        outside.mkdir()

        with pytest.raises(ManagedPathError):
            make_data_relative(outside, data_dir)

    def test_rejects_escape_via_dotdot(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            make_data_relative(data_dir.parent, data_dir)


class TestResolveDataPath:
    def test_canonical_jobs_path(self, data_dir: Path) -> None:
        relative = "jobs/ws/job/runs/node/token/session"
        (data_dir / relative).mkdir(parents=True)

        result = resolve_data_path(relative, data_dir, allow_missing=True)

        assert result == (data_dir / relative).resolve()

    def test_all_canonical_prefixes(self, data_dir: Path) -> None:
        for prefix in ("videos", "jobs", "logs", "packages"):
            relative = f"{prefix}/item"
            (data_dir / relative).mkdir(parents=True)

            result = resolve_data_path(relative, data_dir, allow_missing=True)

            assert result == (data_dir / relative).resolve()

    def test_rejects_empty_string(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            resolve_data_path("", data_dir, allow_missing=True)

    def test_rejects_dotdot_escape(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            resolve_data_path("../outside", data_dir, allow_missing=True)

    def test_rejects_data_dir_itself(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            resolve_data_path(".", data_dir, allow_missing=True)

    def test_rejects_absolute_data_dir_itself(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            resolve_data_path(str(data_dir), data_dir, allow_missing=True)

    def test_rejects_unrelated_absolute(self, data_dir: Path) -> None:
        with pytest.raises(ManagedPathError):
            resolve_data_path("/unrelated/session", data_dir, allow_missing=True)

    def test_rejects_outside_absolute_without_managed_suffix(self, data_dir: Path) -> None:
        outside = data_dir.parent / "outside"
        outside.mkdir()

        with pytest.raises(ManagedPathError):
            resolve_data_path(str(outside / "session"), data_dir, allow_missing=True)

    def test_accepts_absolute_inside_current_data_dir_with_warning(self, data_dir: Path) -> None:
        path = data_dir / "videos" / "v1"
        path.mkdir(parents=True)

        with pytest.warns(DeprecationWarning):
            result = resolve_data_path(str(path), data_dir, allow_missing=True)

        assert result == path.resolve()

    def test_rebases_legacy_absolute_with_data_dir_suffix(self, data_dir: Path) -> None:
        relative = "jobs/ws/job/runs/node/token/session"
        old_path = f"/old/checkout/{data_dir.name}/{relative}"
        (data_dir / relative).mkdir(parents=True)

        with pytest.warns(DeprecationWarning):
            result = resolve_data_path(old_path, data_dir, allow_missing=True)

        assert result == (data_dir / relative).resolve()

    def test_rejects_symlink_parent_that_escapes_root(self, data_dir: Path) -> None:
        outside = data_dir.parent / "outside"
        outside.mkdir()
        link = data_dir / "link"
        link.symlink_to(outside)

        with pytest.raises(ManagedPathError):
            resolve_data_path("link/file.txt", data_dir, allow_missing=True)


class TestResolveJobDir:
    def test_uses_storage_dir_when_set(self, managed_root: Path) -> None:
        job = {"id": "j1", "storage_dir": str(managed_root / "ws" / "j1")}

        with pytest.warns(DeprecationWarning):
            result = resolve_job_dir(job, managed_root)

        assert result == (managed_root / "ws" / "j1").resolve()

    def test_uses_relative_storage_dir_when_set(self, managed_root: Path) -> None:
        job = {"id": "j1", "storage_dir": f"{managed_root.name}/ws/j1"}

        result = resolve_job_dir(job, managed_root)

        assert result == (managed_root / "ws" / "j1").resolve()

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_rejects_escape(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside"
        outside.mkdir()
        job = {"id": "j2", "storage_dir": str(outside / "j2")}

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_job_dir(job, managed_root)

        assert "job" in str(exc_info.value)
        assert "j2" in str(exc_info.value)

    def test_rejects_other_managed_category(self, managed_root: Path) -> None:
        job = {"id": "j1", "storage_dir": "videos/v1"}

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_job_dir(job, managed_root)

        assert "job" in str(exc_info.value)


class TestEnsureDirOnce:
    # #618: mkdir-once memoization, asserted at the os.mkdir syscall level
    # (the thing fseventsd actually sees). The lru_cache is process-global,
    # so every test clears it first.

    def test_creates_once_and_skips_subsequent_calls(self, tmp_path: Path) -> None:
        target = tmp_path / "logs" / "jobs"
        ensure_dir_once.cache_clear()
        try:
            with probe_os_mkdir() as syscalls:
                for _ in range(10):
                    ensure_dir_once(target)
        finally:
            ensure_dir_once.cache_clear()

        assert target.is_dir()
        assert syscalls == sorted((str(tmp_path / "logs"), str(target)))

    def test_distinct_paths_are_created_independently(self, tmp_path: Path) -> None:
        first = tmp_path / "a" / "shared"
        second = tmp_path / "b" / "shared"
        ensure_dir_once.cache_clear()
        try:
            with probe_os_mkdir() as syscalls:
                ensure_dir_once(first)
                ensure_dir_once(second)
                ensure_dir_once(first)
                ensure_dir_once(second)
        finally:
            ensure_dir_once.cache_clear()

        assert first.is_dir() and second.is_dir()
        expected = sorted(str(p) for p in (first, first.parent, second, second.parent))
        assert syscalls == expected

    def test_returns_the_path(self, tmp_path: Path) -> None:
        target = tmp_path / "logs" / "jobs"
        ensure_dir_once.cache_clear()
        try:
            assert ensure_dir_once(target) is target
        finally:
            ensure_dir_once.cache_clear()

    def test_concurrent_cold_cache_creates_the_dir(self, tmp_path: Path) -> None:
        # lru_cache is not atomic across threads: a cold cache hit by many
        # threads at once runs mkdir(parents=True, exist_ok=True) more than
        # once. The contract is only that this is harmless — every caller
        # must see a usable directory.
        target = tmp_path / "logs" / "jobs"
        errors: list[BaseException] = []

        def hit() -> None:
            try:
                ensure_dir_once(target)
            except BaseException as exc:  # noqa: BLE001 - recorded, not masked
                errors.append(exc)

        ensure_dir_once.cache_clear()
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for future in as_completed([pool.submit(hit) for _ in range(32)]):
                    future.result()
        finally:
            ensure_dir_once.cache_clear()

        assert errors == []
        assert target.is_dir()


class TestJobLogDir:
    # #618: <logs_dir>/jobs resolved AND ensured once per process — the
    # scheduler's per-claim resolve + mkdir pair was the top write-type
    # fsevent item in issue #618's attribution.

    def test_resolves_and_creates_the_jobs_log_dir(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        job_log_dir.cache_clear()
        try:
            result = job_log_dir(logs_dir)
            again = job_log_dir(logs_dir)
        finally:
            job_log_dir.cache_clear()

        assert result == (logs_dir / "jobs").resolve()
        assert result.is_dir()
        assert again is result  # the lru_cache returns the same object

    def test_repeated_calls_issue_no_mkdir_syscalls(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        job_log_dir.cache_clear()
        try:
            first = job_log_dir(logs_dir)
            with probe_os_mkdir() as syscalls:
                for _ in range(25):
                    assert job_log_dir(logs_dir) is first
        finally:
            job_log_dir.cache_clear()
        assert syscalls == []


@contextlib.contextmanager
def probe_os_mkdir() -> Iterator[list[str]]:
    """Record every *successful* os.mkdir syscall issued inside the block.

    Only successes count: pathlib's parents=True recursion probes with a
    failing first attempt, and a failed attempt is not a filesystem write.
    """
    created: list[str] = []
    original = os.mkdir

    def counting_mkdir(path: object, *args: object, **kwargs: object) -> None:
        original(path, *args, **kwargs)  # type: ignore[arg-type,call-arg]
        created.append(str(path))

    os.mkdir = counting_mkdir  # type: ignore[assignment]
    try:
        yield created
    finally:
        os.mkdir = original  # type: ignore[assignment]
