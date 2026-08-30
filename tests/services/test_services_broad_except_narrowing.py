"""#204 第四批（services 长尾收尾）窄化语义钉子。

每个用例对应一处本批窄化的 catch：降级族（boto 数据面 / OSError /
ManagedPathError / JSON 解码 / InvalidToken / IntegrityError）保持原有
降级语义；编程错误（TypeError / RuntimeError 等未声明族）上抛，不再被
吞成业务失败。保留（仅补注释）的宽捕获在对应模块的既有测试与
#251/#243/#233 的同款用例中已有钉子，这里不重复。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import psycopg
import pytest

from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.node_code_pins import node_code_pins_from_job_snapshot
from server.app.services.run_dir_cleanup import (
    cleanup_extra_runs_for_node,
    find_extra_run_dirs,
)
from server.app.services.skill_validator import SkillValidator
from server.app.services.token_usage_lease import (
    capture_token_usage_after_lease_finish,
)
from server.app.workflows.definition import (
    WorkflowDefinitionError,
    workflow_definition_from_dict,
)

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# node_code_pins: corrupt snapshot degrades to {}, programming error raises.
# ---------------------------------------------------------------------------


def test_node_code_pins_corrupt_snapshot_degrades_to_empty() -> None:
    """#204 窄化：损坏的快照 JSON（json.JSONDecodeError ⊂ ValueError）
    按文档契约降级为 {}（definition_from_job_snapshot 已记日志）。"""
    job = {"workflow_definition_snapshot_json": "{not valid json"}
    assert node_code_pins_from_job_snapshot(job) == {}


def test_node_code_pins_non_string_column_degrades() -> None:
    """#204 窄化：列值无法 str() 化（TypeError）同样走文档化的降级路径。"""

    class _Unstringable:
        def __str__(self) -> str:
            raise TypeError("cannot stringify")

    job = {"workflow_definition_snapshot_json": _Unstringable()}
    assert node_code_pins_from_job_snapshot(job) == {}


# ---------------------------------------------------------------------------
# run_dir_cleanup: unmappable dir skips, mappable ones still collect.
# ---------------------------------------------------------------------------


def test_find_extra_run_dirs_skips_unmappable_dir(tmp_path: Path, caplog) -> None:
    """#204 窄化：无法映射进 data_dir 的 run dir（ManagedPathError）被跳过
    并告警，其余 extra dirs 照常收集——而不是整轮失败。"""
    data_dir = tmp_path / "data"
    job_dir = data_dir / "jobs" / "job_1"
    node_parent = job_dir / "runs" / "node_a"
    keep = node_parent / "newest"
    extra = node_parent / "older"
    keep.mkdir(parents=True)
    extra.mkdir()
    (keep / "marker").write_text("new")
    (extra / "marker").write_text("old")

    # make_data_relative(old, data_dir) would succeed here; force the
    # ManagedPathError arm by passing a data_dir the dir cannot be relative
    # to (the sibling root).
    wrong_data_dir = tmp_path / "elsewhere"
    wrong_data_dir.mkdir()

    with caplog.at_level(logging.WARNING, logger="server.app.services.run_dir_cleanup"):
        pairs = find_extra_run_dirs(wrong_data_dir, job_dir, "node_a")

    assert pairs == []
    assert extra.is_dir()  # untouched
    assert "skip unmappable extra run dir" in caplog.text


def test_find_extra_run_dirs_collects_mappable_extras(tmp_path: Path) -> None:
    """#204 窄化的对照组：可映射的 extra dir 照常收集（ newest/older 同秒
    创建时 birthtime 排序不确定，断言「收集到且不是保留目录」即可）。"""
    data_dir = tmp_path / "data"
    job_dir = data_dir / "jobs" / "job_1"
    node_parent = job_dir / "runs" / "node_a"
    keep = node_parent / "newest"
    extra = node_parent / "older"
    keep.mkdir(parents=True)
    extra.mkdir()

    pairs = find_extra_run_dirs(data_dir, job_dir, "node_a")

    assert len(pairs) == 1
    assert pairs[0][0] in {keep, extra}
    assert pairs[0][1].startswith("jobs/")


class _FailingConn:
    """execute 一律抛出注入异常的连接 double。"""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def execute(self, sql: str, params: tuple) -> None:
        raise self._error


def test_cleanup_extra_runs_for_node_contains_per_dir_failure(tmp_path: Path, caplog) -> None:
    """#204 窄化：单条 run dir 的 DB 更新失败（OSError）被逐条包含，
    目录树保留（未删成半状态）、异常不上抛。"""
    data_dir = tmp_path / "data"
    job_dir = data_dir / "jobs" / "job_1"
    node_parent = job_dir / "runs" / "node_a"
    keep = node_parent / "newest"
    extra = node_parent / "older"
    keep.mkdir(parents=True)
    extra.mkdir()

    conn = _FailingConn(psycopg.OperationalError("db connection reset"))

    with caplog.at_level(logging.WARNING, logger="server.app.services.run_dir_cleanup"):
        removed = cleanup_extra_runs_for_node(conn, data_dir, job_dir, "node_a")

    assert removed == 0
    assert node_parent.is_dir()
    assert "Failed to remove extra run dir" in caplog.text


def test_cleanup_extra_runs_for_node_propagates_programming_error(tmp_path: Path) -> None:
    """#204 窄化：DB 更新的编程错误（TypeError）上抛给调用方，不再被
    吞成 warning——区别于连接层 OSError 的逐条包含。"""
    data_dir = tmp_path / "data"
    job_dir = data_dir / "jobs" / "job_1"
    node_parent = job_dir / "runs" / "node_a"
    keep = node_parent / "newest"
    extra = node_parent / "older"
    keep.mkdir(parents=True)
    extra.mkdir()

    conn = _FailingConn(TypeError("execute contract violation"))

    with pytest.raises(TypeError, match="execute contract violation"):
        cleanup_extra_runs_for_node(conn, data_dir, job_dir, "node_a")


# ---------------------------------------------------------------------------
# job_artifact_mutation: staging loop narrows to OSError/ManagedPathError.
# ---------------------------------------------------------------------------


class _FakeDefinition:
    """只够 stage_outputs 走到 staging 循环的最小定义。"""

    class _Node:
        def __init__(self, inputs: list[str], outputs: list[str]) -> None:
            self.inputs = inputs
            self.outputs = outputs

    def __init__(self) -> None:
        self.nodes = {"node_a": self._Node(inputs=[], outputs=["result.json"])}
        self.edges: list[tuple[str, str]] = []


def test_stage_outputs_rolls_back_and_reraises_oserror(tmp_path: Path) -> None:
    """#204 窄化：staging 循环中的 OSError 触发回滚并原样上抛。"""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text("existing")
    service = JobArtifactMutationService(jobs_dir)
    job = {"id": "job_1", "storage_dir": "jobs/job_1"}

    import server.app.services.job_artifact_mutation as mutation_module

    original_move = mutation_module.shutil.move

    def _failing_move(src: str, dst: str) -> None:
        raise OSError("disk full")

    mutation_module.shutil.move = _failing_move
    try:
        with pytest.raises(OSError, match="disk full"):
            service.stage_outputs(job, ["node_a"], _FakeDefinition())
    finally:
        mutation_module.shutil.move = original_move

    assert (job_dir / "result.json").read_text() == "existing"


def test_stage_outputs_programming_error_propagates_uncaught(tmp_path: Path) -> None:
    """#204 窄化：staging 循环里的编程错误（TypeError）不再触发回滚
    try/except 的吞没路径——直接上抛（原类型保持）。"""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text("existing")
    service = JobArtifactMutationService(jobs_dir)
    job = {"id": "job_1", "storage_dir": "jobs/job_1"}

    import server.app.services.job_artifact_mutation as mutation_module

    original_move = mutation_module.shutil.move

    def _broken_move(src: str, dst: str) -> None:
        raise TypeError("move contract violation")

    mutation_module.shutil.move = _broken_move
    try:
        with pytest.raises(TypeError, match="move contract violation"):
            service.stage_outputs(job, ["node_a"], _FakeDefinition())
    finally:
        mutation_module.shutil.move = original_move

    assert (job_dir / "result.json").read_text() == "existing"


# ---------------------------------------------------------------------------
# token_usage_lease: completion-path telemetry narrows to OSError.
# ---------------------------------------------------------------------------


class _UsageConn:
    database_dsn = "postgresql://invalid/test"

    def execute(self, sql: str, params: tuple | None = None):
        class _Row:
            def __init__(self, data: dict) -> None:
                self._data = data

            def __getitem__(self, key: str):
                return self._data[key]

        if "executor_leases" in sql:
            return _Result([_Row({"node_run_id": "nr-1", "workspace_id": "ws"})])
        if "node_runs" in sql:
            return _Result([_Row({"run_dir": "jobs/j1/runs/node_a/tok"})])
        return _Result([])


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def test_capture_token_usage_swallows_oserror(caplog) -> None:
    """#204 窄化：解析/持久化的 OSError（连接层）按 best-effort 吞掉
    （debug 日志），完成路径不受影响。"""
    conn = _UsageConn()

    import server.app.services.token_usage_lease as lease_module

    original = lease_module.parse_token_usage_for_lease

    def _failing_parse(conn, lease_id: str, data_dir: Path):
        raise OSError("connection reset")

    lease_module.parse_token_usage_for_lease = _failing_parse
    try:
        with caplog.at_level(logging.DEBUG, logger="server.app.services.token_usage_lease"):
            capture_token_usage_after_lease_finish(conn, "lease-1", Path("/data"))
    finally:
        lease_module.parse_token_usage_for_lease = original

    assert "Failed to capture token usage" in caplog.text


def test_capture_token_usage_propagates_programming_error() -> None:
    """#204 窄化：注入的编程错误（TypeError）上抛，不再静默 debug 掉。"""
    conn = _UsageConn()

    import server.app.services.token_usage_lease as lease_module

    original = lease_module.parse_token_usage_for_lease

    def _broken_parse(conn, lease_id: str, data_dir: Path):
        raise TypeError("parse contract violation")

    lease_module.parse_token_usage_for_lease = _broken_parse
    try:
        with pytest.raises(TypeError, match="parse contract violation"):
            capture_token_usage_after_lease_finish(conn, "lease-1", Path("/data"))
    finally:
        lease_module.parse_token_usage_for_lease = original


# ---------------------------------------------------------------------------
# skill_validator: lock-getter seam stays broad (documented), smoke the arm.
# ---------------------------------------------------------------------------


def test_skill_validator_lock_getter_failure_degrades_to_none(tmp_path: Path) -> None:
    """#204 保留补强：lock getter seam 的任意失败降级为「无锁定 ref」
    （编辑器展示路径），不 500 整个校验响应。"""
    validator = SkillValidator(tmp_path)

    def _broken_getter():
        raise RuntimeError("db down")

    validator._lock_getter = _broken_getter  # type: ignore[assignment]
    skill_dir = tmp_path / "demo" / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo")

    result = validator.validate(str(skill_dir))

    assert result.valid is True
    assert result.locked_ref is None


# ---------------------------------------------------------------------------
# workflow_draft_compare narrowing smoke: loader family only.
# ---------------------------------------------------------------------------


def test_workflow_definition_from_dict_corrupt_snapshot_is_wde() -> None:
    """#204 前置验证（同 #243 加固）：损坏的快照字段形状以
    WorkflowDefinitionError 抛出——draft compare 的窄化正建立在该契约上。"""
    with pytest.raises(WorkflowDefinitionError):
        workflow_definition_from_dict(json.loads('{"key": "k", "label": "l", "nodes": []}'))


def test_node_code_pins_degrades_non_object_snapshot_top():
    """Codex on PR #264: a snapshot that parses as valid JSON but whose top
    level is not an object (e.g. ``[]``) used to escape as AttributeError,
    breaking candidate computation — it must degrade to {} like the rest of
    the corrupt-payload family."""
    from server.app.services.node_code_pins import node_code_pins_from_job_snapshot

    assert node_code_pins_from_job_snapshot({"workflow_definition_snapshot_json": "[]"}) == {}
    assert node_code_pins_from_job_snapshot({"workflow_definition_snapshot_json": '"legacy"'}) == {}
    assert node_code_pins_from_job_snapshot({"workflow_definition_snapshot_json": "1"}) == {}


def test_staging_catch_family_covers_explicit_valueerror():
    """Codex P1 on PR #264: the staging loop's rollback catch must include
    the plain ValueError the explicit escape check raises (:127) — otherwise
    a legal output staged before an escaping one is stranded half-staged
    (loop aborts with `moves` unrecoverable by the caller). Verified at the
    exception-family level: the catch tuple literally contains ValueError."""
    import inspect

    from server.app.services import job_artifact_mutation as mod

    source = inspect.getsource(mod)
    assert "except (OSError, ValueError, ManagedPathError):" in source, (
        "staging rollback catch must keep ValueError in the tuple — "
        "the explicit escape check at the top of the loop raises plain "
        "ValueError and the rollback must cover it (codex P1 on #264)"
    )
