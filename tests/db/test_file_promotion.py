"""``promote_file_moves_guarded`` 的可回滚文件提升守卫（纯文件系统层）。

自 ``tests/db/test_artifact_promotion.py`` 拆出的姊妹文件（800 行拆分纪
律）：钉住「应用前全量预检、失败整体回滚、成功丢弃备份」的文件面语义 —
重复 target 预检拒绝、良性重复对去重、替换失败时回滚簿已登记、真实目录
target/source 零副作用拒绝（codex #774 P2：目录让备份/回滚双向不可逆）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors._file_promotion import promote_file_moves_guarded


def test_file_moves_guarded_restores_current_backup_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#759 复审 P2：目标已备份而 source→target 替换失败时，本条目已在回滚
    簿内——旧目标从备份恢复，备份目录不滞留。修复前回滚簿在替换成功后才
    登记，回滚臂会连备份一起删掉、旧目标永久丢失。"""
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")
    source = tmp_path / "staged.json"
    source.write_text("new", encoding="utf-8")

    import server.app.executors._file_promotion as fp

    original = fp._replace_file
    calls = {"n": 0}

    def failing_second(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # 第一次是 target→备份；第二次才是 source→target
            raise OSError("disk failure")
        original(src, dst)

    monkeypatch.setattr(fp, "_replace_file", failing_second)

    with pytest.raises(OSError, match="disk failure"):
        promote_file_moves_guarded([(target, source)], backup_parent=tmp_path)

    assert target.read_text(encoding="utf-8") == "old"
    assert source.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".promote-rollback-*"))


def test_file_moves_guarded_rejects_duplicate_normalized_targets(tmp_path: Path) -> None:
    """#759 复审 P2：两个归一到同一路径的产物名（异 staging source）在应用
    前拒绝——不允许第二项被「source 缺席 + target 在场」误当重放静默跳过。"""
    source_a = tmp_path / "staged-a.json"
    source_a.write_text("a", encoding="utf-8")
    source_b = tmp_path / "staged-b.json"
    source_b.write_text("b", encoding="utf-8")
    moves = [
        (tmp_path / "reports/out.json", source_a),
        (Path(f"{tmp_path}/reports//out.json"), source_b),
    ]

    with pytest.raises(ValueError, match="duplicate promote target"):
        promote_file_moves_guarded(moves, backup_parent=tmp_path)

    assert not (tmp_path / "reports").exists()  # 应用前拒绝：零副作用
    assert not list(tmp_path.glob(".promote-rollback-*"))


def test_file_moves_guarded_dedupes_identical_pairs(tmp_path: Path) -> None:
    """#759 对抗复审 P1：完全相同的 (target, source) 对是良性形态（finish 批
    重放、工作流重复声明 outputs 的笔误）——去重后正常提升，不得被重复
    target 预检误杀成 finish 永久卡死。"""
    target = tmp_path / "out.json"
    source = tmp_path / "staged.json"
    source.write_text("payload", encoding="utf-8")

    guard = promote_file_moves_guarded([(target, source), (target, source)], backup_parent=tmp_path)
    guard.discard()

    assert target.read_text(encoding="utf-8") == "payload"
    assert not source.exists()  # 恰好提升一次
    assert not list(tmp_path.glob(".promote-rollback-*"))


def test_file_moves_guarded_rejects_directory_target(tmp_path: Path) -> None:
    """codex #774 P2：target 当前是真实目录（先前节点产出 ``reports/out.json``、
    当前节点产出文件 ``reports``）时，任何移动之前整批拒绝。修复前整棵目
    录被挪进备份：成功收尾经 rmtree 递归删除（其他产物的本地副本消失而
    清单行仍在），回滚臂也无法把目录 os.replace 回已存在的文件上。"""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "out.json").write_text("kept", encoding="utf-8")
    source = tmp_path / "staged.json"
    source.write_text("new", encoding="utf-8")

    with pytest.raises(ValueError, match="promote target is a directory"):
        promote_file_moves_guarded([(tmp_path / "reports", source)], backup_parent=tmp_path)

    assert (tmp_path / "reports" / "out.json").read_text(encoding="utf-8") == "kept"
    assert source.read_text(encoding="utf-8") == "new"  # 零副作用
    assert not list(tmp_path.glob(".promote-rollback-*"))


def test_file_moves_guarded_rejects_directory_source(tmp_path: Path) -> None:
    """codex #774 P2 的对称面：staging source 是真实目录时同样在任何移动之
    前拒绝——目录提升成功一半时回滚簿只会对 target 做 unlink（对目录抛
    IsADirectoryError 被逐条告警吞掉），留下半应用现场。"""
    (tmp_path / "staged_dir").mkdir()
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="promote source is a directory"):
        promote_file_moves_guarded([(target, tmp_path / "staged_dir")], backup_parent=tmp_path)

    assert target.read_text(encoding="utf-8") == "old"
    assert (tmp_path / "staged_dir").is_dir()  # 零副作用
    assert not list(tmp_path.glob(".promote-rollback-*"))


def test_file_moves_guarded_recheck_rejects_directory_swapped_in_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#774 对抗复审 P2（TOCTOU 收口）：预检通过时 target 还是文件，备份
    rename 前现场被并发写入换成目录（运行中的沙箱进程直写 job_dir 不经代
    次闸）。备份后立即复查命中目录形态——整体回滚再拒绝，绝不走到成功
    收尾的 rmtree 毁整棵目录。修复前该竞态静默成功：目录进备份、被
    discard 的 rmtree 递归删除。"""
    import server.app.executors._file_promotion as fp

    target = tmp_path / "reports"
    target.write_text("old-file", encoding="utf-8")  # 预检时刻是文件
    (tmp_path / "planted").mkdir()
    (tmp_path / "planted" / "out.json").write_text("kept", encoding="utf-8")
    source = tmp_path / "staged.json"
    source.write_text("new", encoding="utf-8")

    original = fp._replace_file

    def swap_to_directory(src: Path, dst: Path) -> None:
        if src == target:  # target→备份 的 rename：先把现场换成目录
            target.unlink()
            (tmp_path / "planted").rename(target)
        original(src, dst)

    monkeypatch.setattr(fp, "_replace_file", swap_to_directory)

    with pytest.raises(ValueError, match="promote target became a directory"):
        promote_file_moves_guarded([(target, source)], backup_parent=tmp_path)

    assert (target / "out.json").read_text(encoding="utf-8") == "kept"  # 目录整体回位
    assert source.read_text(encoding="utf-8") == "new"  # 未提升
    assert not list(tmp_path.glob(".promote-rollback-*"))  # 回滚成功，备份目录已清


def test_file_moves_guarded_rollback_retains_backup_dir_on_partial_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#774 对抗复审 P2（S3 侧删除前提的文件面对称）：回滚自身部分失败时
    备份目录必须保留——失败项的备份文件是旧目标的最后本地恢复源（ERROR
    日志携带路径）。修复前 rollback 末尾无条件 rmtree，恢复源随失败一起
    被删。"""
    import logging

    target_a = tmp_path / "a.json"
    target_a.write_text("old-a", encoding="utf-8")
    source_a = tmp_path / "staged-a.json"
    source_a.write_text("new-a", encoding="utf-8")
    target_b = tmp_path / "b.json"
    target_b.write_text("old-b", encoding="utf-8")
    source_b = tmp_path / "staged-b.json"
    source_b.write_text("new-b", encoding="utf-8")

    guard = promote_file_moves_guarded(
        [(target_a, source_a), (target_b, source_b)], backup_parent=tmp_path
    )
    backup_dir = next(tmp_path.glob(".promote-rollback-*"))

    # 回滚前把 b 的落点换成非空目录：os.replace(备份文件, 目录) 必失败。
    target_b.unlink()
    target_b.mkdir()
    (target_b / "occupant").write_text("x", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="server.app.executors._file_promotion_guard"):
        guard.rollback()

    assert target_a.read_text(encoding="utf-8") == "old-a"  # 成功项正常恢复
    assert (target_b / "occupant").exists()  # 失败项：现场保持
    assert (backup_dir / "1").read_text(encoding="utf-8") == "old-b"  # 最后恢复源留存
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(str(target_b) in msg and str(backup_dir) in msg for msg in errors)


def test_file_moves_guarded_rollback_interrupted_retains_backup_dir_with_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#774 对抗复审 P2：回滚循环被 BaseException（KeyboardInterrupt）中断
    时，备份目录保留且补 ERROR 指针——未恢复项的备份是旧目标的最后本地
    恢复源，无指针只能靠 .promote-rollback-* glob 偶然发现。簿已封存：
    二次 rollback 幂等空转。"""
    import logging

    import server.app.executors._file_promotion_guard as fpg

    target_a = tmp_path / "a.json"
    target_a.write_text("old-a", encoding="utf-8")
    source_a = tmp_path / "staged-a.json"
    source_a.write_text("new-a", encoding="utf-8")
    target_b = tmp_path / "b.json"  # 无既有文件：簿内 backup=None（unlink 臂）
    source_b = tmp_path / "staged-b.json"
    source_b.write_text("new-b", encoding="utf-8")

    guard = promote_file_moves_guarded(
        [(target_a, source_a), (target_b, source_b)], backup_parent=tmp_path
    )
    backup_dir = next(tmp_path.glob(".promote-rollback-*"))

    calls = {"n": 0}

    def interrupting(src: Path, dst: Path) -> None:
        calls["n"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(fpg, "_replace_file", interrupting)

    with (
        caplog.at_level(logging.ERROR, logger="server.app.executors._file_promotion_guard"),
        pytest.raises(KeyboardInterrupt),
    ):
        guard.rollback()

    assert calls["n"] == 1  # 逆序恢复在 b（unlink 臂，不经 _replace_file）之后中于 a
    assert not target_b.exists()  # b 已回滚（新文件移除）
    assert target_a.read_text(encoding="utf-8") == "new-a"  # a 未恢复（中断点）
    assert (backup_dir / "0").read_text(encoding="utf-8") == "old-a"  # 最后恢复源留存
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(str(backup_dir) in msg for msg in errors)
    guard.rollback()  # 封存后幂等空转
    assert (backup_dir / "0").exists()
