"""Studio-agent tool surface: the workflow draft tools (read + CAS write,
issue #633).

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


def test_get_workflow_draft_empty_state(client, job_db) -> None:
    """#633：读工具的结构化空态与人侧 GET 一致——无草稿 200 + 双 null，
    不是 404（agent 可据此走 from-scratch 流）。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(_draft_url(workspace_id))

    assert response.status_code == 200, response.text
    assert response.json() == {"definition_yaml": None, "updated_at": None}


def test_save_workflow_draft_roundtrip_with_canvas(client, job_db) -> None:
    """#633 核心验收：agent 保存的草稿落到画布同一份草稿行——人侧
    workflow-draft GET 读到同一 YAML/updated_at。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = _active_yaml(scoped, workspace_id).replace("label: ", "label: v2 ", 1)

    saved = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "expected_updated_at": "never-saved"},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["definition_yaml"] == draft_yaml
    assert saved.json()["updated_at"]
    # The human editor's draft store reads the SAME row.
    human = client.get(f"/api/workspaces/{workspace_id}/workflow-draft")
    assert human.status_code == 200
    assert human.json()["definition_yaml"] == draft_yaml
    assert human.json()["updated_at"] == saved.json()["updated_at"]

    got = scoped.get(_draft_url(workspace_id))
    assert got.json() == saved.json()


def test_save_workflow_draft_cas_roundtrip_and_conflict(client, job_db) -> None:
    """#633 并发保护：stale expected_updated_at → 409 + 结构化 payload 携带
    当前草稿；用返回的新 updated_at rebase 后保存成功。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = _active_yaml(scoped, workspace_id)

    first = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "expected_updated_at": "never-saved"},
    )
    assert first.status_code == 200
    updated_at = first.json()["updated_at"]

    # A stale token (the draft moved on via the human editor's surface).
    human_edit = "key: other\nlabel: human\n"
    client.put(
        f"/api/workspaces/{workspace_id}/workflow-draft",
        json={"definition_yaml": human_edit},
    )

    conflict = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "expected_updated_at": updated_at},
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["expected_updated_at"] == updated_at
    assert detail["current_draft"]["definition_yaml"] == human_edit
    assert detail["current_draft"]["updated_at"]
    # The stored draft was NOT overwritten.
    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] == human_edit

    # Rebase with the current timestamp from the conflict payload succeeds.
    rebased = scoped.put(
        _draft_url(workspace_id),
        json={
            "definition_yaml": draft_yaml,
            "expected_updated_at": detail["current_draft"]["updated_at"],
        },
    )
    assert rebased.status_code == 200, rebased.text
    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] == draft_yaml


def test_save_workflow_draft_conflict_when_draft_created_after_never_saved(client, job_db) -> None:
    """never-saved 基线只在草稿确实仍不存在时成立：草稿已被创建后再用
    never-saved 保存是冲突，不得静默覆盖。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = _active_yaml(scoped, workspace_id)

    client.put(
        f"/api/workspaces/{workspace_id}/workflow-draft",
        json={"definition_yaml": "key: human\nlabel: first\n"},
    )

    response = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": draft_yaml, "expected_updated_at": "never-saved"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["current_draft"]["definition_yaml"] == (
        "key: human\nlabel: first\n"
    )


def test_workflow_draft_tools_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    assert scoped.get(_draft_url("ws-missing")).status_code == 404
    put = scoped.put(
        _draft_url("ws-missing"),
        json={"definition_yaml": "key: x\n", "expected_updated_at": "never-saved"},
    )
    assert put.status_code == 404


def test_save_workflow_draft_rejects_blank_and_missing_cas_token(client, job_db) -> None:
    """空白草稿/缺失 CAS 时间戳是 422（契约层拒绝），不落库。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    blank = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": "   \n", "expected_updated_at": "never-saved"},
    )
    assert blank.status_code == 422

    no_token = scoped.put(_draft_url(workspace_id), json={"definition_yaml": "key: x\n"})
    assert no_token.status_code == 422

    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] is None


def test_save_workflow_draft_rejects_unparseable_cas_token_with_422(client, job_db) -> None:
    """#633 codex review P2-2：非法 CAS 时间戳（既非 never-saved 也非 ISO）是
    契约层 422——否则 timestamptz cast 会以 500 DB 错误暴露，而不是清晰的
    "no match → conflict"。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    for bad in ("garbage", "2026-13-45T99:99:99+00:00", "yesterday"):
        response = scoped.put(
            _draft_url(workspace_id),
            json={"definition_yaml": "key: x\n", "expected_updated_at": bad},
        )
        assert response.status_code == 422, bad
        assert "expected_updated_at must be an ISO timestamp" in response.text

    assert scoped.get(_draft_url(workspace_id)).json()["definition_yaml"] is None


def test_save_workflow_draft_valid_but_stale_timestamp_gets_409_not_422(client, job_db) -> None:
    """合法 ISO 但已过期的基线走正常 CAS 冲突（409 + current_draft），
    never-saved 在草稿确实不存在时成功——422 只拦「非法格式」。"""
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    ok = scoped.put(
        _draft_url(workspace_id),
        json={"definition_yaml": "key: first\n", "expected_updated_at": "never-saved"},
    )
    assert ok.status_code == 200, ok.text

    stale = scoped.put(
        _draft_url(workspace_id),
        json={
            "definition_yaml": "key: second\n",
            "expected_updated_at": "2020-01-01T00:00:00+00:00",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["current_draft"]["definition_yaml"] == "key: first\n"


def test_compare_workflow_without_baseline_returns_full_draft_preview(client, job_db) -> None:
    """Tool-surface compare on a never-published workflow (workspace key set,
    no revision): instead of a revision error the draft is diffed against an
    empty base, so the agent can preview the full from-scratch shape."""
    scoped, _ = _scoped_client(client, job_db)
    workspace = job_db.create_workspace("ws-fresh", default_workflow_key="studio_fresh_flow")
    workspace_id = str(workspace["id"])

    response = scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/compare",
        json={
            "definition_yaml": (
                "key: studio_fresh_flow\n"
                "label: Studio Fresh Flow\n"
                "nodes:\n"
                "  publish_content:\n"
                "    capability: publish_content\n"
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["base_revision"] is None
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
