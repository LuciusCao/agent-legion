from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from server.app.executors.remote_bundle import BundleError, build_bundle, extract_result_archive


def _make_skill(root: Path) -> Path:
    skill = root / "skill_src"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill / "scripts" / "validate_output.py").write_text("pass", encoding="utf-8")
    return skill


def test_build_bundle_contains_skill_and_manifest_only(tmp_path):
    skill = _make_skill(tmp_path)
    bundle = tmp_path / "bundles" / "e1.tar.gz"

    build_bundle(
        bundle,
        skill_dir=skill,
        manifest={"job_id": "j1", "run_token": "abc"},
    )

    with tarfile.open(bundle, "r:gz") as tar:
        names = set(tar.getnames())
        assert "skill/SKILL.md" in names
        assert "skill/scripts/validate_output.py" in names
        assert not any(name.startswith("inputs/") for name in names)
        manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
        assert manifest == {"job_id": "j1", "run_token": "abc"}


def test_extract_result_archive_writes_outputs_and_runs(tmp_path):
    archive = tmp_path / "e1.result.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in (
            ("output.json", b"{}"),
            ("runs/node_a/abc123/events.jsonl", b"{}\n"),
            ("runs/node_a/abc123/session/sess.json", b"{}"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    extract_result_archive(archive, job_dir)

    assert (job_dir / "output.json").is_file()
    assert (job_dir / "runs" / "node_a" / "abc123" / "events.jsonl").is_file()
    assert (job_dir / "runs" / "node_a" / "abc123" / "session" / "sess.json").is_file()


@pytest.mark.parametrize("evil", ["../evil.txt", "/abs/evil.txt", "runs/../../evil.txt"])
def test_extract_rejects_traversal(tmp_path, evil):
    archive = tmp_path / "evil.tar.gz"
    content = b"pwned"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(evil)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    with pytest.raises(BundleError, match="unsafe path"):
        extract_result_archive(archive, job_dir)
    assert not (tmp_path / "evil.txt").exists()
    assert list(job_dir.iterdir()) == []


def test_extract_rejects_symlinks(tmp_path):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    with pytest.raises(BundleError, match="links are not allowed"):
        extract_result_archive(archive, job_dir)
