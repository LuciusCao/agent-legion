"""Durable JSON artifact swap (#975): os.replace + parent-directory fsync."""

from __future__ import annotations

import json
import os
import stat

import pytest

import server.app.services.staged_json_artifact as staged_mod
from server.app.services.staged_json_artifact import replace_durable, stage_json


def _is_dir_fd(fd: int) -> bool:
    return stat.S_ISDIR(os.fstat(fd).st_mode)


def test_replace_durable_fsyncs_parent_directory_after_replace(tmp_path, monkeypatch):
    target = tmp_path / "gate.approval.json"
    staged = stage_json(target, {"verdict": "approved"})
    events: list[str] = []
    real_replace, real_fsync = os.replace, os.fsync

    def _replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    def _fsync(fd):
        events.append("dir" if _is_dir_fd(fd) else "file")
        return real_fsync(fd)

    monkeypatch.setattr(staged_mod.os, "replace", _replace)
    monkeypatch.setattr(staged_mod.os, "fsync", _fsync)
    replace_durable(staged, target)

    assert events == ["replace", "dir"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"verdict": "approved"}
    assert not staged.exists()


def test_replace_durable_propagates_dir_fsync_failure(tmp_path, monkeypatch):
    """The caller (inside its transaction) must see the failure and roll back."""
    target = tmp_path / "review_feedback.json"
    staged = stage_json(target, {"note": "x"})
    real_fsync = os.fsync

    def _fsync(fd):
        if _is_dir_fd(fd):
            raise OSError("dir fsync failed")
        return real_fsync(fd)

    monkeypatch.setattr(staged_mod.os, "fsync", _fsync)
    with pytest.raises(OSError, match="dir fsync failed"):
        replace_durable(staged, target)
