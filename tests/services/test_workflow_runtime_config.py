"""M47: runtime 通道服务——runtime 切片 extract/apply/diff 与保存/回滚。

版本语义：每次有实际变更的保存追加一条不可变版本行（审计 + 回滚源）；
无变更保存是 no-op。dispatch 解析链不读本表（active revision 仍是唯一
解析来源），这里只钉写面与历史语义。
"""

from __future__ import annotations

import json

import pytest

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.runtime_config import (
    apply_runtime_config,
    diff_runtime_config_keys,
    extract_runtime_config,
    rollback_workflow_runtime_config,
    save_workflow_runtime_config,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping


def _agent_flow_yaml(model: str = "", tools: str = "") -> dict:
    node = {
        "type": "agent",
        "capability": "do_thing",
        "execution": {"runtime": "velites"},
        "outputs": ["result.json"],
    }
    if model:
        node["execution"]["model"] = model
    if tools:
        node["tools"] = [tools]
    return {
        "key": "runtime_channel_flow",
        "label": "Runtime Channel Flow",
        "nodes": {"do_thing": node},
    }


def _definition(**kwargs: str):
    return workflow_definition_from_mapping(_agent_flow_yaml(**kwargs))


# ---------------------------------------------------------------------------
# Pure slice helpers (no DB).
# ---------------------------------------------------------------------------


@pytest.mark.no_db
def test_extract_runtime_config_normalizes_full_key_set() -> None:
    config = extract_runtime_config(_definition(model="m1", tools="bash"))

    assert config["execution"] == {"provider": "", "model": "", "thinking": ""}
    node = config["nodes"]["do_thing"]
    assert node["model"] == "m1"
    assert node["prompt"] == "" and node["prompt_mode"] == ""
    assert node["tools"] == ["bash"]
    assert node["requires_labels"] == {}


@pytest.mark.no_db
def test_diff_runtime_config_keys_reports_dotted_paths() -> None:
    old = extract_runtime_config(_definition(model="m1"))
    new = extract_runtime_config(_definition(model="m2", tools="bash"))

    assert diff_runtime_config_keys(old, new) == ["nodes.do_thing.model", "nodes.do_thing.tools"]
    assert diff_runtime_config_keys(old, old) == []


@pytest.mark.no_db
def test_apply_runtime_config_preserves_runtime_and_unknown_nodes() -> None:
    base = _definition(model="m2")
    target = extract_runtime_config(_definition(model="m1"))
    # 目标版本不认识后来新增的节点：保留其现值；execution.runtime 永不被改写。
    wider = workflow_definition_from_mapping(
        {
            "key": "runtime_channel_flow",
            "label": "Runtime Channel Flow",
            "nodes": {
                "do_thing": _agent_flow_yaml(model="m2")["nodes"]["do_thing"],
                "new_node": {
                    "type": "agent",
                    "capability": "other",
                    "execution": {"runtime": "pi", "model": "keep-me"},
                },
            },
        }
    )
    applied = apply_runtime_config(wider, target)

    assert applied.nodes["do_thing"].execution.model == "m1"
    assert applied.nodes["do_thing"].execution.runtime == "velites"
    assert applied.nodes["new_node"].execution.model == "keep-me"
    assert applied.nodes["new_node"].execution.runtime == "pi"
    # 原对象不被 mutate（frozen dataclass 复制语义）。
    assert base.nodes["do_thing"].execution.model == "m2"


# ---------------------------------------------------------------------------
# Service level: save / history / rollback over the real schema (postgres).
# ---------------------------------------------------------------------------


def _publish(job_db) -> tuple[dict, dict]:
    workspace = job_db.create_workspace("runtime-channel-ws", workspace_id="runtime_channel_flow")
    revision = WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace["id"], _definition(model="m1")
    )
    return workspace, revision


def _active_model(job_db, workspace_id: str) -> str:
    active = job_db.get_active_workflow_revision(workspace_id, "runtime_channel_flow")
    return json.loads(str(active["definition_json"]))["nodes"]["do_thing"]["execution"]["model"]


def test_runtime_save_updates_in_place_and_appends_version(job_db) -> None:
    workspace, revision = _publish(job_db)

    result = save_workflow_runtime_config(
        job_db, workspace["id"], _definition(model="m2"), created_by="user:u1"
    )

    assert result["revision"]["id"] == revision["id"]
    assert result["revision"]["version"] == 1
    assert _active_model(job_db, workspace["id"]) == "m2"
    version_row = result["runtime_version"]
    assert version_row is not None
    assert version_row["version"] == 1
    assert version_row["revision_id"] == revision["id"]
    assert version_row["created_by"] == "user:u1"
    assert json.loads(str(version_row["changed_keys_json"])) == ["nodes.do_thing.model"]
    stored = json.loads(str(version_row["config_json"]))
    assert stored["nodes"]["do_thing"]["model"] == "m2"


def test_runtime_save_without_changes_is_a_noop(job_db) -> None:
    workspace, _revision = _publish(job_db)
    save_workflow_runtime_config(
        job_db, workspace["id"], _definition(model="m2"), created_by="user:u1"
    )

    result = save_workflow_runtime_config(
        job_db, workspace["id"], _definition(model="m2"), created_by="user:u1"
    )

    assert result["runtime_version"] is None
    assert len(job_db.list_runtime_config_versions(workspace["id"])) == 1


def test_runtime_save_refuses_structural_change(job_db) -> None:
    workspace, revision = _publish(job_db)
    structural = workflow_definition_from_mapping(
        {
            "key": "runtime_channel_flow",
            "label": "Runtime Channel Flow",
            "nodes": {
                "do_thing": {
                    "type": "agent",
                    "capability": "do_thing",
                    "execution": {"runtime": "velites", "model": "m2"},
                    "outputs": ["result.json", "extra.json"],
                }
            },
        }
    )

    with pytest.raises(ConflictError):
        save_workflow_runtime_config(job_db, workspace["id"], structural, created_by="user:u1")

    assert job_db.list_runtime_config_versions(workspace["id"]) == []
    assert _active_model(job_db, workspace["id"]) == "m1"
    assert (
        job_db.get_active_workflow_revision(workspace["id"], "runtime_channel_flow")["id"]
        == revision["id"]
    )


def test_runtime_save_requires_an_active_revision(job_db) -> None:
    workspace = job_db.create_workspace("blank-ws", workspace_id="runtime_channel_flow")

    with pytest.raises(NotFoundError):
        save_workflow_runtime_config(
            job_db, workspace["id"], _definition(model="m2"), created_by="user:u1"
        )


def test_runtime_save_tools_and_requires_labels_stay_in_place(job_db) -> None:
    """M47 分类翻转：tools / requires_labels 不再是结构字段——仅改它们也走
    原地更新 + 版本行，不发布新 revision。"""
    workspace, revision = _publish(job_db)
    changed = workflow_definition_from_mapping(
        {
            "key": "runtime_channel_flow",
            "label": "Runtime Channel Flow",
            "nodes": {
                "do_thing": {
                    "type": "agent",
                    "capability": "do_thing",
                    "execution": {"runtime": "velites", "model": "m1"},
                    "outputs": ["result.json"],
                    "tools": ["bash"],
                    "requires_labels": {"gpu": "true"},
                }
            },
        }
    )

    result = save_workflow_runtime_config(job_db, workspace["id"], changed, created_by="user:u1")

    assert result["revision"]["id"] == revision["id"]
    assert result["revision"]["version"] == 1
    assert json.loads(str(result["runtime_version"]["changed_keys_json"])) == [
        "nodes.do_thing.requires_labels.gpu",
        "nodes.do_thing.tools",
    ]


def test_draft_publish_runtime_only_path_appends_version(job_db) -> None:
    """草稿发布走 runtime-only 原地路径时同样追加版本行（单一写面）。"""
    workspace, _revision = _publish(job_db)

    WorkflowRevisionService(job_db).save_workspace_revision(
        workspace["id"], _definition(model="m2")
    )

    versions = job_db.list_runtime_config_versions(workspace["id"])
    assert len(versions) == 1
    # 无操作者语境的调用方（默认参数）落空串。
    assert versions[0]["created_by"] == ""


def test_rollback_appends_new_version_with_target_content(job_db) -> None:
    workspace, _revision = _publish(job_db)
    save_workflow_runtime_config(
        job_db, workspace["id"], _definition(model="m2"), created_by="user:u1"
    )
    save_workflow_runtime_config(
        job_db, workspace["id"], _definition(model="m3"), created_by="user:u2"
    )

    result = rollback_workflow_runtime_config(job_db, workspace["id"], 1, created_by="user:u3")

    assert _active_model(job_db, workspace["id"]) == "m2"
    versions = job_db.list_runtime_config_versions(workspace["id"])
    assert [row["version"] for row in versions] == [3, 2, 1]
    newest = versions[0]
    # 回滚 = 追加内容等于目标版本的新版本；历史行不被篡改。
    assert json.loads(str(newest["config_json"])) == json.loads(str(versions[2]["config_json"]))
    assert json.loads(str(newest["changed_keys_json"])) == ["nodes.do_thing.model"]
    assert newest["created_by"] == "user:u3"
    assert result["runtime_version"]["version"] == 3


def test_rollback_unknown_version_raises_not_found(job_db) -> None:
    workspace, _revision = _publish(job_db)

    with pytest.raises(NotFoundError):
        rollback_workflow_runtime_config(job_db, workspace["id"], 99, created_by="user:u1")
