"""Unit tests for workflow_nodes/question_intake.py (fetch_questions node)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from workflow_nodes import question_intake
from workspace_libs.cms.client import CmsClientError


@dataclass(frozen=True)
class _FakeDetail:
    question_id: str
    title: str
    normalized: dict[str, Any]
    payload: dict[str, Any] | None


@dataclass(frozen=True)
class _FakeSummary:
    question_id: str
    title: str
    payload: dict[str, Any]


class _FakeJobDb:
    def __init__(self, source_payload: dict[str, Any]) -> None:
        self._source_payload = source_payload

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        if not batch_id:
            return None
        return {"source_payload_json": json.dumps(self._source_payload)}


def _job(source_id: str = "q-1", batch_id: str = "") -> dict[str, Any]:
    return {
        "id": "job-1",
        "workspace_id": "ws-a",
        "source_id": source_id,
        "batch_id": batch_id,
        "title": "Question q-1",
    }


def _context(
    monkeypatch: pytest.MonkeyPatch,
    cms_config: dict[str, Any],
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Node-level CMS config arrives via runtime["node_config"]; the dispatch
    # layer would already have resolved any vault secret_ref into plaintext.
    monkeypatch.setattr(question_intake, "get_token", lambda env, cfg: "token")
    context: dict[str, Any] = {"node_config": cms_config}
    if source_payload is not None:
        context["job_db"] = _FakeJobDb(source_payload)
    return context


def test_by_id_fetches_detail_and_writes_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(monkeypatch, {"api_url": "https://cms.example.com/detail"})
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(qid, "Title", {"stem": "1+1=?"}, {"data": {}}),
    )

    question_intake.run(_job(), tmp_path, context)

    data = json.loads((tmp_path / "questions.json").read_text(encoding="utf-8"))
    assert data["questions"] == [
        {
            "question_id": "q-1",
            "title": "Title",
            "normalized": {"stem": "1+1=?"},
            "cms_payload": {"data": {}},
        }
    ]


def test_by_id_cms_error_payload_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In-band CMS errors (e.g. code=10015 auth failure) must fail the node."""
    context = _context(monkeypatch, {"api_url": "https://cms.example.com/detail"})
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(
            qid, qid, {}, {"code": 10015, "message": "JWT验证失败", "data": None}
        ),
    )

    with pytest.raises(RuntimeError, match="code=10015"):
        question_intake.run(_job(), tmp_path, context)

    assert not (tmp_path / "questions.json").exists()


def test_by_id_blank_stem_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful-looking response without a usable stem is garbage input:
    business/source_missing via CmsEmptyStemError, not an auth failure."""
    context = _context(monkeypatch, {"api_url": "https://cms.example.com/detail"})
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(qid, "Title", {"stem": "  "}, {"code": 0}),
    )

    with pytest.raises(question_intake.CmsEmptyStemError, match="缺少题干"):
        question_intake.run(_job(), tmp_path, context)

    assert not (tmp_path / "questions.json").exists()


def test_by_id_missing_normalized_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """code=0 with an absent normalized map (stem key missing) must also fail."""
    context = _context(monkeypatch, {"api_url": "https://cms.example.com/detail"})
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(qid, "Title", {}, {"code": 0, "data": {}}),
    )

    with pytest.raises(RuntimeError, match="缺少题干"):
        question_intake.run(_job(), tmp_path, context)


def test_by_knowledge_partial_failure_fails_whole_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad detail fails the batch before anything is written (atomicity)."""
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )
    monkeypatch.setattr(
        question_intake,
        "list_questions_by_knowledge",
        lambda code, url, token: [_FakeSummary("q-1", "T1", {}), _FakeSummary("q-2", "T2", {})],
    )

    def _fetch(qid: str, url: str, token: str) -> _FakeDetail:
        if qid == "q-2":
            return _FakeDetail(qid, qid, {}, {"code": 10015, "message": "JWT验证失败"})
        return _FakeDetail(qid, f"Title {qid}", {"stem": qid}, {"code": 0})

    monkeypatch.setattr(question_intake, "fetch_question_detail", _fetch)

    with pytest.raises(RuntimeError, match="code=10015"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)

    assert not (tmp_path / "questions.json").exists()


def test_by_knowledge_cms_error_payload_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )
    monkeypatch.setattr(
        question_intake,
        "list_questions_by_knowledge",
        lambda code, url, token: [_FakeSummary("q-1", "T1", {})],
    )
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(
            qid, qid, {}, {"code": 10015, "message": "JWT验证失败", "data": None}
        ),
    )

    with pytest.raises(RuntimeError, match="code=10015"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)

    assert not (tmp_path / "questions.json").exists()


def test_by_id_without_cms_writes_base_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(monkeypatch, {})

    question_intake.run(_job(), tmp_path, context)

    data = json.loads((tmp_path / "questions.json").read_text(encoding="utf-8"))
    assert data["questions"] == [
        {"question_id": "q-1", "title": "Question q-1", "normalized": {}, "cms_payload": None}
    ]


def test_by_id_prefers_injected_connection_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatch-injected connection supplies the token and endpoint; legacy
    node-level connection keys are ignored while a connection is present."""
    context = {
        "node_config": {
            "connection": "cms-internal",
            "connection_config": {
                "token": "conn-token",
                "api_url": "https://conn.example.com/detail",
            },
            "token": "legacy-token",
            "api_url": "https://legacy.example.com/detail",
        }
    }
    captured: dict[str, Any] = {}

    def _fetch(qid: str, url: str, token: str) -> _FakeDetail:
        captured["url"] = url
        captured["token"] = token
        return _FakeDetail(qid, "Title", {"stem": "1+1=?"}, {"data": {}})

    monkeypatch.setattr(question_intake, "fetch_question_detail", _fetch)

    question_intake.run(_job(), tmp_path, context)

    # Real workspace_libs get_token reads config["token"]: the injected
    # connection token wins over the legacy node token.
    assert captured == {"url": "https://conn.example.com/detail", "token": "conn-token"}


def test_by_id_cms_error_reports_auth_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auth-class CMS failure must invalidate the cached connection token
    via report_node_auth_failure before the error propagates."""
    context = _context(monkeypatch, {"api_url": "https://cms.example.com/detail"})
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(
            qid, qid, {}, {"code": 10015, "message": "JWT验证失败"}
        ),
    )
    reported: list[dict[str, Any]] = []
    monkeypatch.setattr(
        question_intake, "report_node_auth_failure", lambda ctx: reported.append(ctx)
    )

    with pytest.raises(RuntimeError, match="code=10015"):
        question_intake.run(_job(), tmp_path, context)

    assert reported == [context]


def test_by_knowledge_cms_error_reports_auth_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )
    monkeypatch.setattr(
        question_intake,
        "list_questions_by_knowledge",
        lambda code, url, token: [_FakeSummary("q-1", "T1", {})],
    )
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(
            qid, qid, {}, {"code": 10015, "message": "JWT验证失败"}
        ),
    )
    reported: list[dict[str, Any]] = []
    monkeypatch.setattr(
        question_intake, "report_node_auth_failure", lambda ctx: reported.append(ctx)
    )

    with pytest.raises(RuntimeError, match="code=10015"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)

    assert reported == [context]


def test_by_knowledge_list_in_band_auth_reports_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list endpoint's in-band auth code must fail the node and invalidate
    the cached token — not be parsed as an empty list (business/source_missing)."""
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )

    def _list(code: str, url: str, token: str) -> list[Any]:
        raise CmsClientError(
            "CMS 返回错误: code=10015 message=JWT验证失败 (question list knowledge=K001)",
            auth_failure=True,
        )

    monkeypatch.setattr(question_intake, "list_questions_by_knowledge", _list)
    reported: list[dict[str, Any]] = []
    monkeypatch.setattr(
        question_intake, "report_node_auth_failure", lambda ctx: reported.append(ctx)
    )

    with pytest.raises(CmsClientError, match="code=10015"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)

    assert reported == [context]
    assert not (tmp_path / "questions.json").exists()


def test_by_knowledge_list_transport_error_keeps_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transport/non-auth list failures (5xx/timeout/DNS, non-auth in-band
    codes) must NOT invalidate the healthy cached token."""
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )

    def _list(code: str, url: str, token: str) -> list[Any]:
        raise CmsClientError("CMS request failed: 500 Server Error")

    monkeypatch.setattr(question_intake, "list_questions_by_knowledge", _list)
    reported: list[dict[str, Any]] = []
    monkeypatch.setattr(
        question_intake, "report_node_auth_failure", lambda ctx: reported.append(ctx)
    )

    with pytest.raises(CmsClientError, match="CMS request failed"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)

    assert reported == []


def test_by_id_transport_error_keeps_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detail-endpoint transport failures must NOT invalidate the token."""
    context = _context(monkeypatch, {"api_url": "https://cms.example.com/detail"})

    def _fetch(qid: str, url: str, token: str) -> Any:
        raise CmsClientError("CMS request failed: 503 Server Error")

    monkeypatch.setattr(question_intake, "fetch_question_detail", _fetch)
    reported: list[dict[str, Any]] = []
    monkeypatch.setattr(
        question_intake, "report_node_auth_failure", lambda ctx: reported.append(ctx)
    )

    with pytest.raises(CmsClientError, match="CMS request failed"):
        question_intake.run(_job(), tmp_path, context)

    assert reported == []


def test_by_knowledge_expands_code_into_multiple_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )
    monkeypatch.setattr(
        question_intake,
        "list_questions_by_knowledge",
        lambda code, url, token: [
            _FakeSummary("q-1", "T1", {}),
            _FakeSummary("q-2", "T2", {}),
        ],
    )
    monkeypatch.setattr(
        question_intake,
        "fetch_question_detail",
        lambda qid, url, token: _FakeDetail(qid, f"Title {qid}", {"stem": qid}, None),
    )

    question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)

    data = json.loads((tmp_path / "questions.json").read_text(encoding="utf-8"))
    assert [q["question_id"] for q in data["questions"]] == ["q-1", "q-2"]


def test_by_knowledge_requires_list_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(monkeypatch, {}, source_payload=payload)

    with pytest.raises(RuntimeError, match="question list URL"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)


def test_by_knowledge_empty_list_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"intake_mode": {"key": "batch_by_knowledge", "input_field": "knowledge_codes"}}
    context = _context(
        monkeypatch,
        {
            "question_list_url": "https://cms.example.com/list",
            "api_url": "https://cms.example.com/detail",
        },
        source_payload=payload,
    )
    monkeypatch.setattr(question_intake, "list_questions_by_knowledge", lambda *a: [])

    with pytest.raises(RuntimeError, match="no questions found"):
        question_intake.run(_job(source_id="K001", batch_id="batch-1"), tmp_path, context)


def test_intake_input_field_prefers_prefetched_batch() -> None:
    """Custom sandboxed children have no job_db; the prefetched batch row wins."""
    batch = {"source_payload_json": json.dumps({"intake_mode": {"input_field": "question_ids"}})}
    assert (
        question_intake._intake_input_field({"batch_id": "b1"}, {"job_batch": batch})
        == "question_ids"
    )
    # No batch and no job_db → empty (builtin fallback path unchanged).
    assert question_intake._intake_input_field({"batch_id": "b1"}, {}) == ""
