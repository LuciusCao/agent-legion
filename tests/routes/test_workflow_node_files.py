from __future__ import annotations

import pytest

NODE_URL = "/api/workflow-nodes/files/question_intake.py"


def _member_client(client):
    csrf = {"x-agent-legion-request": "1"}
    client.post(
        "/api/users",
        json={"username": "member1", "password": "pw1", "role": "member"},
        headers=csrf,
    )
    member = client.__class__(client.app)
    member.post("/api/auth/login", json={"username": "member1", "password": "pw1"})
    member.headers.update(csrf)
    return member


def test_admin_reads_node_file(client) -> None:
    response = client.get(NODE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "workflow_nodes/question_intake.py"
    assert "def run(" in body["content"]
    assert {"executor_id": "code-default", "capability": "fetch_questions"} in body["capabilities"]


def test_accepts_workflow_nodes_prefix(client) -> None:
    response = client.get("/api/workflow-nodes/files/workflow_nodes/question_intake.py")

    assert response.status_code == 200
    assert response.json()["path"] == "workflow_nodes/question_intake.py"


def test_anonymous_access_rejected(anon_client) -> None:
    assert anon_client.get(NODE_URL).status_code == 401


def test_non_admin_forbidden(client) -> None:
    member = _member_client(client)

    assert member.get(NODE_URL).status_code == 403


@pytest.mark.parametrize(
    "url_path",
    [
        "..%2F..%2Fx.py",  # decoded to ../../x.py: contains '..'
        "%2Fetc%2Fpasswd",  # decoded to /etc/passwd: absolute
        "__init__.py",  # dunder-prefixed
        "notes.txt",  # not a Python file
        "sub%2Fdir.py",  # decoded to sub/dir.py: not a single file name
    ],
)
def test_rejected_paths(client, url_path) -> None:
    response = client.get(f"/api/workflow-nodes/files/{url_path}")

    assert response.status_code == 422


def test_missing_file_is_404(client) -> None:
    assert client.get("/api/workflow-nodes/files/no_such_node.py").status_code == 404


def test_put_write_endpoint_is_gone(client) -> None:
    """EXEC-CODE-001: no runtime API may rewrite repo node files (removed in M1)."""
    response = client.put(
        "/api/workflow-nodes/files/question_intake.py",
        json={"content": "def run(job, job_dir, runtime):\n    pass\n"},
    )
    # GET-only route remains; the write verb is unhandled (404/405 by mount).
    assert response.status_code in (404, 405)
