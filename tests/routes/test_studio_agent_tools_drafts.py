"""Studio-agent tool surface: the workflow draft tools (read + CAS write,
issue #633; generation-based CAS since #1226 五轮收口).

Split from test_studio_agent_tools.py when it crossed the 800-line
test-file budget (#779 codex train review R3); cases migrated verbatim.
Shared scaffolding lives in tests/routes/studio_agent_tools_testlib.py;
the auth-scope matrix and the other tool families stay in
test_studio_agent_tools.py.
"""

from __future__ import annotations

import pytest

from tests.routes.studio_agent_tools_testlib import (
    _active_yaml,
    _create_workspace,
    _scoped_client,
    reset_create_count,
)


@pytest.fixture(autouse=True)
def _reset_create_count():
    reset_create_count()
    yield


def _draft_url(workspace_id: str) -> str:
    return f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft"


def _generation(scoped, workspace_id: str) -> int:
    return int(scoped.get(_draft_url(workspace_id)).json()["generation"])


def test_get_workflow_draft_empty_state(client, job_db) -> None:
    """#633：读工具的结构化空态与人侧 GET 一致——无草稿 200 + 内容字段全
    null + 当前代次（0），不是 404（agent 可据此走 from-scratch 流）。
    #1143：definition_hash 一并入空态；#1226 五轮收口：代次入空态。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(_draft_url(workspace_id))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "definition_yaml": None,
        "updated_at": None,
        "definition_hash": None,
        "generation": 0,
    }


def test_save_workflow_draft_roundtrip_with_canvas(client, job_db) -> None:
    """#633 核心验收：agent 保存的草稿落到画布同一份草稿行——人侧
    workflow-draft GET 读到同一 YAML/updated_at。#1143：两侧读到的语义
    身份 hash 一致（草稿卡核对用）。#1226：代次同读同写。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = _active_yaml(scoped, workspace_id).replace("label: ", "label: v2 ", 1)
    base = _generation(scoped, workspace_id)

    saved = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "base_generation": base},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["definition_yaml"] == draft_yaml
    assert saved.json()["updated_at"]
    assert saved.json()["definition_hash"]
    assert saved.json()["generation"] == base + 1
    # The human editor's draft store reads the SAME row (identity included).
    human = client.get(f"/api/workspaces/{workspace_id}/workflow-draft")
    assert human.status_code == 200
    assert human.json()["definition_yaml"] == draft_yaml
    assert human.json()["updated_at"] == saved.json()["updated_at"]
    assert human.json()["definition_hash"] == saved.json()["definition_hash"]
    assert human.json()["generation"] == saved.json()["generation"]

    got = scoped.get(_draft_url(workspace_id))
    assert got.json() == saved.json()


def test_save_workflow_draft_cas_roundtrip_and_conflict(client, job_db) -> None:
    """#633 并发保护（#1226 代次基线）：stale base_generation → 409 + 结构化
    payload 携带当前草稿与当前代次；用返回的新代次 rebase 后保存成功。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = _active_yaml(scoped, workspace_id)

    first = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "base_generation": 0},
    )
    assert first.status_code == 200
    base = int(first.json()["generation"])

    # A stale base (the draft moved on via the human editor's surface).
    human_edit = "key: other\nlabel: human\n"
    client.put(
        f"/api/workspaces/{workspace_id}/workflow-draft",
        json={"definition_yaml": human_edit},
    )

    conflict = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "base_generation": base},
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["base_generation"] == base
    assert detail["current_draft"]["definition_yaml"] == human_edit
    assert detail["current_draft"]["generation"] == base + 1
    assert detail["current_draft"]["updated_at"]
    # #1143：current_draft 携带语义身份（采用服务端版本的一侧恢复 savedHash）。
    from server.app.services.workflow_drafts import workflow_draft_identity_hash

    assert detail["current_draft"]["definition_hash"] == workflow_draft_identity_hash(human_edit)
    # #1177 codex P2：与人侧 PUT 同一契约——整个 409 响应体（含 FastAPI
    # 原生 handler 的 detail 外层）过封套模型校验。
    from server.app.routes.workflow_draft_store_contracts import (
        WorkflowDraftConflictResponse as ContractResponse,
    )

    ContractResponse.model_validate(conflict.json())
    # The stored draft was NOT overwritten.
    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] == human_edit

    # Rebase with the current generation from the conflict payload succeeds.
    rebased = scoped.put(
        _draft_url(workspace_id),
        json={
            "definition_yaml": draft_yaml,
            "base_generation": detail["current_draft"]["generation"],
        },
    )
    assert rebased.status_code == 200, rebased.text
    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] == draft_yaml


def test_save_workflow_draft_conflict_when_draft_created_after_empty_read(client, job_db) -> None:
    """空读（代次 0）后草稿已被创建（代次推进）→ 再用旧代次保存是冲突
    （#633 的 never-saved 守卫的代次形态，#1226 R6 同源），不得静默覆盖。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = _active_yaml(scoped, workspace_id)
    empty_base = _generation(scoped, workspace_id)

    client.put(
        f"/api/workspaces/{workspace_id}/workflow-draft",
        json={"definition_yaml": "key: human\nlabel: first\n"},
    )

    response = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "base_generation": empty_base},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["current_draft"]["definition_yaml"] == (
        "key: human\nlabel: first\n"
    )


def test_get_workflow_draft_empty_state_is_a_single_snapshot(client, job_db, monkeypatch) -> None:
    """#1226 六轮 codex P1：agent GET 的空态与人侧同一缺陷族——内容与代次
    拆两次读时，并发保存落进其间会产出「内容 null + 并发后的新代次」的
    错配响应；同快照语义要求代次与 null 内容同源（0）。"""
    from server.app.jobs import JobQueries

    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    real_get_generation = JobQueries.get_workspace_workflow_draft_generation

    def interleaved_save(self, ws_id: str) -> int:
        # 并发交错点：行读（无草稿）之后、代次读之时，另一会话落盘。
        self.upsert_workspace_workflow_draft(ws_id, "key: concurrent\nlabel: c\n")
        return real_get_generation(self, ws_id)

    monkeypatch.setattr(JobQueries, "get_workspace_workflow_draft_generation", interleaved_save)

    response = scoped.get(_draft_url(workspace_id))

    assert response.status_code == 200, response.text
    assert response.json()["definition_yaml"] is None
    assert response.json()["generation"] == 0


def test_workflow_draft_tools_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    assert scoped.get(_draft_url("ws-missing")).status_code == 404
    put = scoped.put(
        _draft_url("ws-missing"),
        json={"definition_yaml": "key: x\n", "base_generation": 0},
    )
    assert put.status_code == 404


def test_save_workflow_draft_rejects_blank_and_missing_base_generation(client, job_db) -> None:
    """空白草稿/缺失 base_generation 是 422（契约层拒绝），不落库。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    blank = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": "   \n", "base_generation": 0},
    )
    assert blank.status_code == 422

    no_base = scoped.put(_draft_url(workspace_id), json={"definition_yaml": "key: x\n"})
    assert no_base.status_code == 422

    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] is None


def test_save_workflow_draft_rejects_invalid_base_generation_with_422(client, job_db) -> None:
    """非法代次（负数/非整数）是契约层 422——不是 DB 错误（500）。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    for bad in (-1, "garbage", "never-saved"):
        response = scoped.put(
            _draft_url(workspace_id),
            json={"definition_yaml": "key: x\n", "base_generation": bad},
        )
        assert response.status_code == 422, bad

    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] is None


def test_save_workflow_draft_stale_base_gets_409_not_422(client, job_db) -> None:
    """合法但已过期的代次基线走正常 CAS 冲突（409 + current_draft）——
    422 只拦「非法值」。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    ok = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": "key: first\n", "base_generation": 0},
    )
    assert ok.status_code == 200, ok.text

    stale = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": "key: second\n", "base_generation": 0},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["current_draft"]["definition_yaml"] == "key: first\n"


def test_compare_workflow_without_baseline_returns_full_draft_preview(client, job_db) -> None:
    """Tool-surface compare on a never-published workflow (workspace key set,
    no revision): instead of a revision error the draft is diffed against an
    empty base, so the agent can preview the full from-scratch shape."""
    scoped, _ = _scoped_client(client, job_db)
    workspace = job_db.create_workspace("ws-fresh", workspace_id="studio_fresh_flow")
    workspace_id = str(workspace["id"])

    compare_yaml = (
        "key: studio_fresh_flow\n"
        "label: Studio Fresh Flow\n"
        "nodes:\n"
        "  publish_content:\n"
        "    capability: publish_content\n"
    )
    response = scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/compare",
        json={"definition_yaml": compare_yaml},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["base_revision"] is None
    # #1143：compare 响应携带草稿语义身份（与 validate 同源，草稿卡取
    # hash 的另一半入口）；无基线预览同样携带。
    from server.app.services.workflow_drafts import workflow_draft_identity_hash

    assert payload["definition_hash"] == workflow_draft_identity_hash(compare_yaml)
    assert payload["draft_workflow"] == {
        "key": "studio_fresh_flow",
        "label": "Studio Fresh Flow",
        "version": 0,
    }
    assert payload["creates_revision"] is True
    assert payload["summary"]["node_changes"] == [
        {
            # 草稿未声明 start：loader 注入合成 start（EXEC-WORKFLOW-START-001），
            # 无基线对比下它也作为新增节点出现。
            "type": "added",
            "node_key": "_start",
            "label": "Start",
            "node_type": "start",
            "fields": [],
            "risk": "info",
        },
        {
            "type": "added",
            "node_key": "publish_content",
            "label": "publish_content",
            # 草稿未声明 type：loader 归一化为 code（#284 显式节点类型）。
            "node_type": "code",
            "fields": [],
            "risk": "info",
        },
    ]
    assert any(flag["code"] == "no_baseline" for flag in payload["summary"]["risk_flags"])
