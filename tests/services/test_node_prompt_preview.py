"""node_prompt_preview 服务：effective prompt 预览与 draft YAML prompt 编辑。

execution.prompt 语义：空 = 自动组装默认指令（is_default）；非空 = 整段
替代默认指令。预览与 render_command_spec 同一条 build_prompt 路径
（{job_dir}/{skill_dir} 占位符）。
"""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.node_prompt_preview import preview_node_prompt, save_node_prompt
from tests.helpers import publish_builtin_revision

_WORKFLOW_KEY = "education_video_problems_generation"
_DEMO_SKILL = "education-video-problems-generation/write-script"

_DRAFT_YAML = """
key: draft_flow
label: Draft Flow
nodes:
  write_script:
    label: 撰写脚本
    capability: write_script
    skill: education-video-problems-generation/write-script
    inputs: [knowledge_point.json]
    outputs: [script.md]
    execution:
      prompt: Follow the house style.
"""

_DRAFT_YAML_NO_SKILL = """
key: draft_flow
label: Draft Flow
nodes:
  write_script:
    label: 撰写脚本
    capability: write_script
    inputs: [knowledge_point.json]
    outputs: [script.md]
    execution:
      prompt: Follow the house style.
"""

_CUSTOM_NODE_YAML = """
key: draft_flow
label: Draft Flow
nodes:
  custom_node:
    label: Custom
    capability: custom_cap
    outputs: [out.json]
"""

_CUSTOM_NODE_SKILL_YAML = """
key: draft_flow
label: Draft Flow
nodes:
  custom_node:
    label: Custom
    capability: custom_cap
    skill: node-bound/skill
    outputs: [out.json]
"""


def _workspace(job_db, name: str) -> str:
    workspace = job_db.create_workspace(name, default_workflow_key=_WORKFLOW_KEY)
    publish_builtin_revision(job_db, str(workspace["id"]))
    return str(workspace["id"])


def _workspace_with_agent_skill(job_db, name: str) -> str:
    """Workspace whose published Agent for ``custom_cap`` carries a legacy skill."""
    workspace = job_db.create_workspace(name, default_workflow_key="wf")
    workspace_id = str(workspace["id"])
    service = AgentService(job_db.dsn_identity, workspace_id)
    service.save_draft(
        "custom-agent-v1",
        AgentDefinition(capability="custom_cap", runtime="velites", skill="agent/legacy-skill"),
        created_by="test",
    )
    service.publish("custom-agent-v1")
    return workspace_id


def test_preview_active_revision_defaults(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-default")

    payload = preview_node_prompt(job_db, workspace_id, "write_script")

    assert payload["is_default"] is True
    assert payload["custom_instructions"] == ""
    # publish_builtin_revision 种子 demo Agent 与 demo DAG：write_script 节点
    # 自带 skill 绑定（#76，节点优先于 Agent 定义）。
    assert payload["skill_key"] == _DEMO_SKILL
    assert "撰写教学视频脚本" in payload["default_instructions"]
    assert f"`{_DEMO_SKILL}`" in payload["default_instructions"]
    effective = payload["effective_prompt"]
    assert "Job ID: <job_id>" in effective
    assert "Working directory: {job_dir}" in effective
    assert "Skill directory: {skill_dir}" in effective
    assert "- knowledge_point.json" in effective
    assert "- script.md" in effective
    assert payload["default_instructions"] in effective


def test_preview_definition_yaml_override_with_custom_prompt(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-custom")

    payload = preview_node_prompt(job_db, workspace_id, "write_script", _DRAFT_YAML)

    assert payload["is_default"] is False
    assert payload["custom_instructions"] == "Follow the house style."
    # 自定义 prompt 整段替代默认指令；信封保留。
    assert "Node instructions:\nFollow the house style." in payload["effective_prompt"]
    assert payload["default_instructions"] not in payload["effective_prompt"]
    assert "Job ID: <job_id>" in payload["effective_prompt"]
    # 草稿节点自带 skill 绑定时优先于 Agent 定义（与 definition_yaml 同行）。
    assert payload["skill_key"] == _DEMO_SKILL


def test_preview_prefers_the_node_skill_binding(job_db) -> None:
    workspace_id = _workspace_with_agent_skill(job_db, "ws-prompt-node-skill")

    payload = preview_node_prompt(job_db, workspace_id, "custom_node", _CUSTOM_NODE_SKILL_YAML)

    assert payload["skill_key"] == "node-bound/skill"


def test_preview_falls_back_to_the_agent_definition_skill(job_db) -> None:
    workspace_id = _workspace_with_agent_skill(job_db, "ws-prompt-agent-skill")

    payload = preview_node_prompt(job_db, workspace_id, "custom_node", _CUSTOM_NODE_YAML)

    assert payload["skill_key"] == "agent/legacy-skill"


def test_preview_unbound_capability_has_no_skill_key(job_db) -> None:
    workspace = job_db.create_workspace("ws-prompt-unbound", default_workflow_key="wf")
    del workspace  # 只需 workspace 行存在；definition_yaml 显式给出定义。

    payload = preview_node_prompt(job_db, "ws-prompt-unbound", "write_script", _DRAFT_YAML_NO_SKILL)

    assert payload["skill_key"] is None
    assert "loaded node skill" in payload["default_instructions"]


def test_preview_unknown_node_raises_not_found(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-unknown-node")

    with pytest.raises(NotFoundError, match="Unknown workflow node"):
        preview_node_prompt(job_db, workspace_id, "no_such_node")


def test_preview_start_node_is_rejected(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-start")

    with pytest.raises(InvalidOperationError, match="never executes"):
        preview_node_prompt(job_db, workspace_id, "_start")


def test_preview_without_revision_or_workspace_raises_not_found(job_db) -> None:
    workspace = job_db.create_workspace("ws-prompt-no-rev", default_workflow_key="ghost_flow")
    assert workspace["id"]

    with pytest.raises(NotFoundError, match="No active workflow revision"):
        preview_node_prompt(job_db, "ws-prompt-no-rev", "write_script")
    with pytest.raises(NotFoundError, match="Workspace not found"):
        preview_node_prompt(job_db, "ws-missing", "write_script")


def test_preview_invalid_yaml_raises_invalid_operation(job_db) -> None:
    with pytest.raises(InvalidOperationError):
        preview_node_prompt(job_db, "ws-whatever", "n", "- just\n- a\n- list\n")


def test_save_node_prompt_bases_on_active_revision_when_no_draft(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-save")

    saved = save_node_prompt(job_db, workspace_id, "write_script", "Follow the house style.")

    assert saved["is_default"] is False
    assert saved["updated_at"]
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    assert draft is not None
    assert "prompt: Follow the house style." in draft["definition_yaml"]
    # 编辑后的 draft 立即可预览：is_default 翻转为自定义。
    payload = preview_node_prompt(job_db, workspace_id, "write_script", draft["definition_yaml"])
    assert payload["is_default"] is False
    assert payload["custom_instructions"] == "Follow the house style."


def test_save_node_prompt_empty_clears_back_to_default(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-clear")
    save_node_prompt(job_db, workspace_id, "write_script", "Custom text")

    cleared = save_node_prompt(job_db, workspace_id, "write_script", "")

    assert cleared["is_default"] is True
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    assert draft is not None
    assert "prompt:" not in draft["definition_yaml"]
    payload = preview_node_prompt(job_db, workspace_id, "write_script", draft["definition_yaml"])
    assert payload["is_default"] is True


def test_save_node_prompt_edits_existing_draft_without_touching_other_nodes(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-keep")
    save_node_prompt(job_db, workspace_id, "write_script", "Script rules")

    save_node_prompt(job_db, workspace_id, "generate_questions", "Question rules")

    draft = job_db.get_workspace_workflow_draft(workspace_id)
    assert draft is not None
    assert "Script rules" in draft["definition_yaml"]
    assert "Question rules" in draft["definition_yaml"]


def test_save_node_prompt_unknown_targets_raise_not_found(job_db) -> None:
    workspace_id = _workspace(job_db, "ws-prompt-save-404")
    job_db.create_workspace("ws-prompt-bare", default_workflow_key="bare_flow")

    with pytest.raises(NotFoundError, match="Unknown workflow node"):
        save_node_prompt(job_db, workspace_id, "no_such_node", "x")
    # 无 draft 且无 active revision 的 workspace：没有可编辑的基线。
    with pytest.raises(NotFoundError, match="No workflow draft or active revision"):
        save_node_prompt(job_db, "ws-prompt-bare", "write_script", "x")
    with pytest.raises(NotFoundError, match="Workspace not found"):
        save_node_prompt(job_db, "ws-missing", "write_script", "x")
