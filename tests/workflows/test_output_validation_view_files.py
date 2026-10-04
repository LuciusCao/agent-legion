"""#876: view file mechanics — reflink probe residue and reconcile I/O discipline.

Split from ``test_output_validation_view`` (file-size discipline): that file
owns the declared-view semantics and the input-identity family; this one owns
the per-file mechanics regressions — the reflink probe must never leave
residue inside the validator-visible view (B 员 P3), and the output reconcile
must skip entries whose bytes still equal the source's without paying the
temp+copy+replace write amplification (B 员 P3), with the identity check on
content sha256 — never (mtime, size), which is forgeable on an identity path
(codex P2, pinned by the forged-mtime case below). Same seam as the sibling
file: everything pinned through ``validate_worker_outputs`` with the
result-validate pool inlined.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import server.app.workflows.output_contract_engine as output_contract_engine
import server.app.workflows.worker_output_validation as worker_output_validation
from server.app.skills.manager import SkillManager
from server.app.workflows.worker_output_validation import validate_worker_outputs
from tests.helpers.skill_git import _make_manager as _make_real_manager
from tests.helpers.skill_git import _make_skill_repo

pytestmark = pytest.mark.no_db

_KEY = "group/review"


@pytest.fixture(autouse=True)
def _no_real_engine_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default: the legacy script path alone decides."""
    monkeypatch.setattr(output_contract_engine, "resolve_sandbox_binary", lambda: None)


@pytest.fixture(autouse=True)
def _inline_validate_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_output_validation, "validate_in_pool", lambda call, *args: call(*args)
    )


def _manager(tmp_path: Path, validator_body: str | None) -> SkillManager:
    _make_skill_repo(tmp_path / "skills", _KEY, validate_script=validator_body)
    return _make_real_manager(tmp_path)


def _validator(seen_file: Path, rules: str) -> str:
    """A validator recording every file it sees, then applying ``rules``."""
    return (
        "import json, pathlib, sys\n"
        "job = pathlib.Path(sys.argv[1])\n"
        "seen = sorted(str(p.relative_to(job)) for p in job.rglob('*') if p.is_file())\n"
        f"pathlib.Path({str(seen_file)!r}).write_text('\\n'.join(seen))\n"
    ) + rules


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    """The accumulating job dir and this attempt's read view (staging)."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    run_view = tmp_path / "staging"
    run_view.mkdir()
    return job_dir, run_view


def _manifest(inputs: list[str], outputs: list[str]) -> dict:
    return {"skill": _KEY, "skill_ref": "latest", "inputs": inputs, "expected_outputs": outputs}


def _seen(seen_file: Path) -> list[str]:
    return seen_file.read_text().splitlines()


def _no_hardlink(source: Path, target: Path) -> None:
    """Force the copy fallback: hardlink-unsupported mount."""
    raise OSError(1, "Operation not permitted")


def test_reflink_probe_residue_stays_out_of_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: the per-filesystem probe writes its temp file OUTSIDE the view
    (the view's parent — same st_dev, verdict unchanged), so even when the
    suppressed cleanup unlink fails, ``.reflink-probe-*`` residue lands in
    the job dir (reclaimed with it) and never in the validator's rglob."""
    import server.app.workflows._reflink_copy as reflink_copy

    monkeypatch.setattr(reflink_copy, "_support", {})  # 强制重新探测
    real_unlink = os.unlink

    def _failing_unlink(path: object, *args: object, **kwargs: object) -> None:
        if ".reflink-probe-" in str(path):
            raise OSError(13, "Permission denied")
        real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reflink_copy.os, "unlink", _failing_unlink)
    seen_file = tmp_path / "seen.txt"
    manager = _manager(tmp_path, _validator(seen_file, ""))
    job_dir, run_view = _layout(tmp_path)
    (job_dir / "cleaned_question.json").write_text("{}")

    manifest = _manifest(["cleaned_question.json"], [])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    # 探测确实发生过（残留落在视图外的 job_dir，随 job_dir GC 回收）……
    assert any(p.name.startswith(".reflink-probe-") for p in job_dir.iterdir())
    # ……但 validator 的视图清单里绝没有探测残留。
    assert not any(name.startswith(".reflink-probe-") for name in _seen(seen_file))


def test_unmodified_copied_outputs_skip_sync_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5: a copy-fallback (hardlink-unsupported) output the validator never
    touched must not pay the temp+copy+replace — reconcile skips it on the
    content-hash identity check (#876 codex P2: content sha256, never the
    forgeable (mtime, size))."""
    import server.app.workflows._validation_view_files as view_files

    syncs: list[str] = []
    monkeypatch.setattr(view_files, "_sync_back", lambda spot, source: syncs.append(spot.name))
    monkeypatch.setattr(view_files.os, "link", _no_hardlink)
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", ""))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw")

    manifest = _manifest([], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert syncs == []
    assert (run_view / "review_a.json").read_text() == "raw"  # 字节原位未动


def test_modified_copied_output_still_syncs_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5 对照：拷贝回落条目被 validator 改过（此处尺寸变化）仍照常
    sync_back——跳过逻辑只放内容一致的。"""
    import server.app.workflows._validation_view_files as view_files

    syncs: list[str] = []
    monkeypatch.setattr(view_files, "_sync_back", lambda spot, source: syncs.append(spot.name))
    monkeypatch.setattr(view_files.os, "link", _no_hardlink)
    rules = "(job / 'review_a.json').write_text('cleaned-and-longer')\n"
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw")

    manifest = _manifest([], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert syncs == ["review_a.json"]


def test_same_size_rewrite_with_restored_mtime_still_syncs_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#876 codex P2 伪造攻击钉死：validator 同尺寸改写视图副本并用
    os.utime 回拨 mtime——(mtime, size) 与放置时完全一致，但内容已变；
    reconcile 的内容 hash 判定必须看穿伪装并照常 sync_back（run_view 落
    到改写后的字节）。"""
    import server.app.workflows._validation_view_files as view_files

    monkeypatch.setattr(view_files.os, "link", _no_hardlink)
    rules = (
        "import os\n"
        "p = job / 'review_a.json'\n"
        "st = p.stat()\n"
        "p.write_text('XXXX')\n"  # 同尺寸（4 字节）改写
        "os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))\n"  # mtime 回拨伪造未修改
    )
    manager = _manager(tmp_path, _validator(tmp_path / "seen.txt", rules))
    job_dir, run_view = _layout(tmp_path)
    (run_view / "review_a.json").write_text("raw!")  # 4 字节

    manifest = _manifest([], ["review_a.json"])
    assert validate_worker_outputs(manager, manifest, job_dir, run_view) is None
    assert (run_view / "review_a.json").read_text() == "XXXX"
