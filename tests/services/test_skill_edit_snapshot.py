"""A FULL-state export must fail rather than omit an unreadable member."""

import os

import pytest

from server.app.services.skill_edit_snapshot import shared_edit_snapshot
from server.app.services.skill_repo_edit import SkillEditValidationError

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "missing-map", "bad-path", "too-many"]
)
def test_shared_snapshot_rejects_incomplete_or_unwritable_tree(tmp_path, kind):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "map.json").write_text('{"version": 1, "materials": []}')
    (root / "references").mkdir()
    member = root / "references" / "unsafe.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("must not leak")
    if kind == "symlink":
        member.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, member)
    elif kind == "fifo":
        os.mkfifo(member)
    elif kind == "missing-map":
        (root / "map.json").unlink()
        member.write_text("orphaned content")
    elif kind == "bad-path":
        (root / "unsupported.txt").write_text("not accepted by PUT")
    else:
        for n in range(100):
            (root / "references" / f"{n}.txt").write_text("x")
    with pytest.raises(SkillEditValidationError):
        shared_edit_snapshot(root)
    assert outside.read_text() == "must not leak"
