import os
from pathlib import Path

import pytest

from server.app.storage_paths import (
    ManagedPathError,
    resolve_job_dir,
    resolve_managed_path,
    resolve_video_dir,
)


@pytest.fixture
def managed_root(tmp_path: Path) -> Path:
    root = tmp_path / "managed"
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

    def test_managed_root_itself_rejected(self, managed_root: Path) -> None:
        with pytest.raises(ManagedPathError) as exc_info:
            resolve_managed_path(managed_root, ".", allow_missing=False)

        assert "managed" in str(exc_info.value).lower()

    def test_expanduser_outside_rejected(self, managed_root: Path) -> None:
        home = managed_root.parent / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)

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


class TestResolveVideoDir:
    def test_uses_storage_dir_when_set(self, managed_root: Path) -> None:
        video = {"id": "v1", "storage_dir": str(managed_root / "v1")}

        result = resolve_video_dir(video, managed_root)

        assert result == (managed_root / "v1").resolve()

    def test_rejects_escape(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside"
        outside.mkdir()
        video = {"id": "v2", "storage_dir": str(outside / "v2")}

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_video_dir(video, managed_root)

        assert "video" in str(exc_info.value)
        assert "v2" in str(exc_info.value)


class TestResolveJobDir:
    def test_uses_storage_dir_when_set(self, managed_root: Path) -> None:
        job = {"id": "j1", "storage_dir": str(managed_root / "ws" / "j1")}

        result = resolve_job_dir(job, managed_root)

        assert result == (managed_root / "ws" / "j1").resolve()

    def test_rejects_escape(self, managed_root: Path) -> None:
        outside = managed_root.parent / "outside"
        outside.mkdir()
        job = {"id": "j2", "storage_dir": str(outside / "j2")}

        with pytest.raises(ManagedPathError) as exc_info:
            resolve_job_dir(job, managed_root)

        assert "job" in str(exc_info.value)
        assert "j2" in str(exc_info.value)
