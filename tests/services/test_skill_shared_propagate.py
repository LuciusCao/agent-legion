"""Shared-material propagation service (issue #673).

Per-skill outcomes against real git repos under a monkeypatched HOME:
synced (new patch tag + flipped drift), skipped (already in sync, repo
missing), failed (unreadable shared source, dirty tree), tag increments
(v1.2.3 → v1.2.4, no tags → v0.1.0, non-semver tags ignored), the
sources filter selecting skills, and batch isolation (one failure does
not strand the other skills).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_propagate import propagate_shared_materials
from server.app.services.skill_shared_propagate_plan import next_version_tag

_WS = "propagate-ws"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _make_skill_repo(repo: Path, files: dict[str, str], tags: tuple[str, ...] = ()) -> None:
    # The trio save_version's post-write contract check enforces (#542);
    # a missing root contract.yaml is a warning only.
    trio = {
        "SKILL.md": "# Skill\n",
        "references/output-contract.md": "# contract\n",
        "scripts/validate_output.py": "raise SystemExit(0)\n",
    }
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, content in {**trio, **files}.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    for tag in tags:
        _git(repo, "tag", tag)


@pytest.fixture
def ws_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    base = home / ".agents" / "skills"
    (base / _WS).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return base


def _seed_shared(
    base: Path,
    materials: list[dict],
    files: dict[str, str],
) -> Path:
    shared = base / _WS / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "map.json").write_text(
        json.dumps({"version": 1, "materials": materials}), encoding="utf-8"
    )
    for rel, content in files.items():
        target = shared / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return shared


def test_next_version_tag_rules() -> None:
    assert next_version_tag(()) == "v0.1.0"
    assert next_version_tag(("v1.2.3",)) == "v1.2.4"
    # Highest parseable wins regardless of input order; non-semver ignored.
    assert next_version_tag(("draft", "v1.10.0", "v1.2.9")) == "v1.10.1"
    assert next_version_tag(("not-a-version",)) == "v0.1.0"


def test_propagate_syncs_flips_drift_and_bumps_patch_tag(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["write-script"]}],
        {"references/style.md": "# v2\n"},
    )
    repo = ws_dir / _WS / "write-script"
    _make_skill_repo(repo, {"references/style.md": "# v1\n"}, tags=("v1.2.3",))

    result = propagate_shared_materials(_WS)
    (entry,) = result.results
    assert entry.status == "synced"
    assert entry.tag == "v1.2.4"
    assert entry.synced_files == ("references/style.md",)
    # The repo HEAD now carries the shared copy, committed + tagged.
    assert _git(repo, "show", "HEAD:references/style.md") == "# v2"
    assert "v1.2.4" in _git(repo, "tag", "--list")
    assert "Sync shared materials" in _git(repo, "log", "-1", "--pretty=%s")


def test_propagate_no_tags_starts_at_initial_version(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["fresh-skill"]}],
        {"references/style.md": "# v1\n"},
    )
    _make_skill_repo(ws_dir / _WS / "fresh-skill", {"SKILL.md": "# s\n"})

    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "synced"
    assert entry.tag == "v0.1.0"


def test_propagate_skips_already_synced_and_missing_repos(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["synced-skill", "ghost-skill"]}],
        {"references/style.md": "# same\n"},
    )
    _make_skill_repo(
        ws_dir / _WS / "synced-skill", {"references/style.md": "# same\n"}, tags=("v1.0.0",)
    )
    # ghost-skill: no repo directory.

    result = propagate_shared_materials(_WS)
    by_skill = {r.skill: r for r in result.results}
    assert by_skill["synced-skill"].status == "skipped"
    assert by_skill["synced-skill"].detail == "already in sync"
    assert by_skill["ghost-skill"].status == "skipped"
    assert by_skill["ghost-skill"].detail == "skill repo not found"
    # No new tag was created for the already-synced skill.
    assert _git(ws_dir / _WS / "synced-skill", "tag", "--list") == "v1.0.0"


def test_propagate_isolates_failures_per_skill(ws_dir) -> None:
    """A dirty tree fails one skill; the other still propagates."""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["dirty-skill", "clean-skill"]}],
        {"references/style.md": "# v2\n"},
    )
    dirty = ws_dir / _WS / "dirty-skill"
    _make_skill_repo(dirty, {"references/style.md": "# v1\n"}, tags=("v1.0.0",))
    (dirty / "uncommitted.md").write_text("dirty\n", encoding="utf-8")
    _make_skill_repo(
        ws_dir / _WS / "clean-skill", {"references/style.md": "# v1\n"}, tags=("v1.0.0",)
    )

    result = propagate_shared_materials(_WS)
    by_skill = {r.skill: r for r in result.results}
    assert by_skill["dirty-skill"].status == "failed"
    assert "uncommitted" in (by_skill["dirty-skill"].detail or "")
    assert by_skill["clean-skill"].status == "synced"
    assert by_skill["clean-skill"].tag == "v1.0.1"


def test_propagate_reports_unreadable_shared_source(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/gone.md", "skills": ["some-skill"]}],
        {"references/other.md": "# present\n"},
    )
    _make_skill_repo(ws_dir / _WS / "some-skill", {"SKILL.md": "# s\n"})

    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "failed"
    assert "unreadable" in (entry.detail or "")


def test_propagate_sources_filter_selects_skills(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [
            {"source": "references/a.md", "skills": ["skill-a"]},
            {"source": "references/b.md", "skills": ["skill-b"]},
        ],
        {"references/a.md": "# a2\n", "references/b.md": "# b2\n"},
    )
    _make_skill_repo(ws_dir / _WS / "skill-a", {"references/a.md": "# a1\n"})
    _make_skill_repo(ws_dir / _WS / "skill-b", {"references/b.md": "# b1\n"})

    result = propagate_shared_materials(_WS, ["references/a.md"])
    assert [r.skill for r in result.results] == ["skill-a"]
    assert result.results[0].status == "synced"
    # skill-b untouched: still on the old copy, no new tag.
    assert _git(ws_dir / _WS / "skill-b", "show", "HEAD:references/b.md") == "# b1"


def test_propagate_whole_mapped_set_even_when_filtered(ws_dir) -> None:
    """save_version syncs the skill's WHOLE mapped set: requesting one
    source also lands the skill's other mapped materials."""
    _seed_shared(
        ws_dir,
        [
            {"source": "references/a.md", "skills": ["multi-skill"]},
            {"source": "references/b.md", "skills": ["multi-skill"]},
        ],
        {"references/a.md": "# a2\n", "references/b.md": "# b2\n"},
    )
    repo = ws_dir / _WS / "multi-skill"
    _make_skill_repo(repo, {"references/a.md": "# a1\n", "references/b.md": "# b1\n"})

    (entry,) = propagate_shared_materials(_WS, ["references/a.md"]).results
    assert entry.status == "synced"
    assert set(entry.synced_files) == {"references/a.md", "references/b.md"}
    assert _git(repo, "show", "HEAD:references/b.md") == "# b2"


def test_propagate_unknown_source_is_422_and_writes_nothing(ws_dir) -> None:
    _seed_shared(
        ws_dir,
        [{"source": "references/a.md", "skills": ["skill-a"]}],
        {"references/a.md": "# a2\n"},
    )
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/a.md": "# a1\n"})

    with pytest.raises(SkillEditValidationError):
        propagate_shared_materials(_WS, ["references/nope.md"])
    assert _git(repo, "show", "HEAD:references/a.md") == "# a1"


def test_propagate_without_shared_dir_is_404(ws_dir) -> None:
    with pytest.raises(NotFoundError):
        propagate_shared_materials(_WS)


def test_generation_swap_mid_batch_is_retryable_conflict(ws_dir, monkeypatch) -> None:
    """codex P1：计划（map + 源指纹）在锁释放后被全量 PUT 换代时，
    批次以 ConflictError（路由 409，可重试）中止，而不是把旧计划应用
    到新一代上。用计划后立刻改写共享源文件模拟换代。"""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["skill-a"]}],
        {"references/style.md": "# v2\n"},
    )
    _make_skill_repo(ws_dir / _WS / "skill-a", {"references/style.md": "# v1\n"})

    from server.app.services import skill_shared_propagate_apply as apply_module

    real_recheck = apply_module._generation_matches
    swapped = {"done": False}

    def swap_then_check(shared_dir, generation):
        if not swapped["done"]:
            swapped["done"] = True
            # 模拟并发全量 PUT：同一 map，源文件内容换代。
            (ws_dir / _WS / "_shared" / "references" / "style.md").write_text(
                "# v3-concurrent\n", encoding="utf-8"
            )
        return real_recheck(shared_dir, generation)

    monkeypatch.setattr(apply_module, "_generation_matches", swap_then_check)
    from server.app.services.job_errors import ConflictError

    with pytest.raises(ConflictError, match="retry"):
        propagate_shared_materials(_WS)
    # 批次中止：skill 仓库保持原样（HEAD 仍是 v1，无新 tag）。
    repo = ws_dir / _WS / "skill-a"
    assert _git(repo, "show", "HEAD:references/style.md") == "# v1"
    assert _git(repo, "tag", "--list") == ""


def test_tag_selected_inside_repo_lock_uses_latest_tags(ws_dir, monkeypatch) -> None:
    """codex P2：tag 选择在 repo lock 临界区内执行——等待方读到胜者刚
    打上的 tag 再递增，而不是锁外算好后撞 tag conflict。"""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["skill-a"]}],
        {"references/style.md": "# v2\n"},
    )
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/style.md": "# v1\n"}, tags=("v1.0.0",))

    from server.app.services import skill_shared_propagate_plan as plan_module

    real_next = plan_module.next_version_tag
    seen_tags: list[tuple[str, ...]] = []

    def spy_next(tags):
        seen_tags.append(tuple(tags))
        return real_next(tags)

    monkeypatch.setattr(plan_module, "next_version_tag", spy_next)
    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "synced"
    # 锁内读取的 tag 列表就是计算依据（v1.0.0 → v1.0.1）。
    assert seen_tags == [("v1.0.0",)]
    assert entry.tag == "v1.0.1"


def test_tag_conflict_gets_friendly_detail(ws_dir, monkeypatch) -> None:
    """评审顺手项：竞争产生的 tag 冲突改写为用户友好文案（语义不变）。"""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["skill-a"]}],
        {"references/style.md": "# v2\n"},
    )
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/style.md": "# v1\n"}, tags=("v1.0.0",))

    from server.app.services import skill_shared_propagate_plan as plan_module

    # 模拟竞争：锁内选出的 tag 已被并发方占用。
    monkeypatch.setattr(plan_module, "next_version_tag", lambda tags: "v1.0.0")
    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "failed"
    assert entry.detail == "版本已被并发更新，请刷新后重试（数据未受影响）"
    # 仓库未被半应用：HEAD 仍是 v1。
    assert _git(repo, "show", "HEAD:references/style.md") == "# v1"


def test_nothing_to_commit_gets_friendly_detail(ws_dir, monkeypatch) -> None:
    """评审顺手项：竞争者已先行同步导致的 nothing-to-commit 同样改写。"""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["skill-a"]}],
        {"references/style.md": "# same\n"},
    )
    _make_skill_repo(ws_dir / _WS / "skill-a", {"references/style.md": "# same\n"})

    from server.app.services import skill_shared_propagate_apply as apply_module

    # 强制 skip 误判为「待同步」，走到 commit 才发现内容一致。
    monkeypatch.setattr(apply_module, "_head_matches", lambda repo, source, data: False)
    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "failed"
    assert entry.detail == "版本已被并发更新，请刷新后重试（数据未受影响）"


def test_save_uses_the_pinned_plan_and_never_rereads_shared(ws_dir, monkeypatch) -> None:
    """codex P1（#674）：代次复核与保存所用同步计划固定在同一临界步骤
    ——save 不得再在独立的锁里重读共享状态（外层的 plan_shared_sync 在
    传播期间被调用即失败）。"""
    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["skill-a"]}],
        {"references/style.md": "# v2\n"},
    )
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/style.md": "# v1\n"})

    def forbidden_reread(*args, **kwargs):
        raise AssertionError("save re-read the shared state instead of the pinned plan")

    monkeypatch.setattr("server.app.services.skill_editing.plan_shared_sync", forbidden_reread)
    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "synced"
    assert _git(repo, "show", "HEAD:references/style.md") == "# v2"


def test_planning_rejects_intermediate_symlink_escape(ws_dir, tmp_path) -> None:
    """codex P1（#674）：传播计划期的源读取同样做 containment——
    `_shared/references -> 外部` 的 source 计为不可读（failed），外部
    文件不会进 skill 仓库。"""
    shared = ws_dir / _WS / "_shared"
    shared.mkdir(parents=True)
    (shared / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/style.md", "skills": ["skill-a"]}],
            }
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "style.md").write_text("smuggled\n", encoding="utf-8")
    (shared / "references").symlink_to(outside)
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/style.md": "# v1\n"})

    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "failed"
    assert "unreadable" in (entry.detail or "")
    assert _git(repo, "show", "HEAD:references/style.md") == "# v1"


def test_shared_lock_held_through_skip_judgment_and_write(ws_dir, monkeypatch) -> None:
    """codex P1（#674 三轮）：shared 代次锁保持到 skip 判定与文件应用
    完成——写阶段内另一线程拿不到 shared 锁（并发 PUT 被阻塞到本
    skill 提交之后，窗口不复存在）。"""
    import threading

    from server.app.services import skill_editing as editing_module
    from server.app.services.skill_shared_store import shared_edit_lock

    _seed_shared(
        ws_dir,
        [{"source": "references/style.md", "skills": ["skill-a"]}],
        {"references/style.md": "# v2\n"},
    )
    _make_skill_repo(ws_dir / _WS / "skill-a", {"references/style.md": "# v1\n"})

    real_check = editing_module.graded_contract_check
    shared_dir = ws_dir / _WS / "_shared"
    probe: dict[str, bool] = {}

    def probe_check(repo_dir):
        # 写阶段（_save_version_locked 的契约复检）内探测锁占用。
        def try_acquire() -> None:
            lock = shared_edit_lock(shared_dir, ws_dir)
            try:
                lock.acquire(timeout=0.2)
            except Exception:
                probe["acquired_during_write"] = False
            else:
                probe["acquired_during_write"] = True
                lock.release()

        thread = threading.Thread(target=try_acquire)
        thread.start()
        thread.join()
        return real_check(repo_dir)

    monkeypatch.setattr(editing_module, "graded_contract_check", probe_check)
    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "synced"
    assert probe == {"acquired_during_write": False}


def test_propagate_rejects_shared_dir_symlink(ws_dir, tmp_path) -> None:
    """codex P1（#674 三轮）：`_shared` 自身是外部 symlink 时传播拒绝
    （map 加载即按无共享材料处理 → NotFoundError/404），外部目录不会
    成为可信 containment 根。"""
    outside = tmp_path / "private"
    (outside / "references").mkdir(parents=True)
    (outside / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/style.md", "skills": ["skill-a"]}],
            }
        ),
        encoding="utf-8",
    )
    (outside / "references" / "style.md").write_text("smuggled\n", encoding="utf-8")
    (ws_dir / _WS / "_shared").symlink_to(outside)
    _make_skill_repo(ws_dir / _WS / "skill-a", {"references/style.md": "# v1\n"})

    with pytest.raises(NotFoundError):
        propagate_shared_materials(_WS)
    repo = ws_dir / _WS / "skill-a"
    assert _git(repo, "show", "HEAD:references/style.md") == "# v1"


def test_non_utf8_source_fails_with_explicit_reason(ws_dir) -> None:
    """主 agent P3：非 UTF-8 映射源（只能本地 FS 放入）永远无法与同步
    副本字节一致——前置拒绝并给出「非 UTF-8 无法同步」的明确 detail，
    而不是 commit 无变化失败后被误改为「并发更新请重试」。"""
    shared = ws_dir / _WS / "_shared"
    (shared / "references").mkdir(parents=True)
    (shared / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/raw.md", "skills": ["skill-a"]}],
            }
        ),
        encoding="utf-8",
    )
    (shared / "references" / "raw.md").write_bytes(b"\xff\xfe invalid utf8\n")
    repo = ws_dir / _WS / "skill-a"
    _make_skill_repo(repo, {"references/raw.md": "# v1\n"}, tags=("v1.0.0",))

    (entry,) = propagate_shared_materials(_WS).results
    assert entry.status == "failed"
    assert entry.detail == "源文件非 UTF-8，无法同步：references/raw.md"
    # 不打空气 tag：仓库保持原样。
    assert _git(repo, "show", "HEAD:references/raw.md") == "# v1"
    assert _git(repo, "tag", "--list") == "v1.0.0"
