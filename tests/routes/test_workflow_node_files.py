from __future__ import annotations

import pytest

CSRF = {"x-agent-legion-request": "1"}
NODE_URL = "/api/workflow-nodes/files/question_intake.py"

VALID_CONTENT = "def run(job, job_dir, runtime):\n    return None\n"


def _member_client(client):
    client.post(
        "/api/users",
        json={"username": "member1", "password": "pw1", "role": "member"},
        headers=CSRF,
    )
    member = client.__class__(client.app)
    member.post("/api/auth/login", json={"username": "member1", "password": "pw1"})
    member.headers.update(CSRF)
    return member


def _redirect_nodes_dir(client, tmp_path):
    nodes_dir = tmp_path / "workflow_nodes"
    nodes_dir.mkdir()
    client.app.state.settings.root_dir = tmp_path
    return nodes_dir


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
    assert anon_client.put(NODE_URL, json={"content": VALID_CONTENT}).status_code == 401


def test_non_admin_forbidden(client) -> None:
    member = _member_client(client)

    assert member.get(NODE_URL).status_code == 403
    assert member.put(NODE_URL, json={"content": VALID_CONTENT}).status_code == 403


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
    assert (
        client.put(
            "/api/workflow-nodes/files/no_such_node.py", json={"content": VALID_CONTENT}
        ).status_code
        == 404
    )


def test_put_roundtrip(client, tmp_path) -> None:
    nodes_dir = _redirect_nodes_dir(client, tmp_path)
    target = nodes_dir / "question_intake.py"
    target.write_text(VALID_CONTENT, encoding="utf-8")

    updated = "async def run(job, job_dir, runtime):\n    return None\n"
    response = client.put(NODE_URL, json={"content": updated})

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "workflow_nodes/question_intake.py"
    assert body["capabilities"] == [
        {"executor_id": "code-default", "capability": "fetch_questions"}
    ]
    assert target.read_text(encoding="utf-8") == updated

    read_back = client.get(NODE_URL)
    assert read_back.status_code == 200
    assert read_back.json()["content"] == updated


def test_put_invalid_content_keeps_file(client, tmp_path) -> None:
    nodes_dir = _redirect_nodes_dir(client, tmp_path)
    target = nodes_dir / "question_intake.py"
    target.write_text(VALID_CONTENT, encoding="utf-8")

    syntax_error = client.put(NODE_URL, json={"content": "def run(:\n"})
    assert syntax_error.status_code == 422

    no_run = client.put(NODE_URL, json={"content": "X = 1\n"})
    assert no_run.status_code == 422

    assert target.read_text(encoding="utf-8") == VALID_CONTENT
    # Atomic write must not leave temp files behind.
    assert [p.name for p in nodes_dir.iterdir()] == ["question_intake.py"]
