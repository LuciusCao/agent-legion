"""Exercise byte-exact authoring through the real MCP registration."""

import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from server.app.mcp_server import local_files
from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server
from server.app.mcp_server.tool_client import ToolClient

pytestmark = pytest.mark.no_db


@pytest.fixture
def authoring(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    state = {"read": {}, "authorized": True}

    async def call(self, method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if path.endswith("/workflow/active"):
            return json.dumps({"state": "empty"}) if state["authorized"] else "HTTP 403: denied"
        if method == "GET":
            return json.dumps(state["read"], ensure_ascii=False)
        return json.dumps({"ok": True, **(body or {})})

    monkeypatch.setattr(ToolClient, "call", call)
    server = create_mcp_server(McpServerConfig("http://backend", "token"))

    def run(name, **args):
        blocks = asyncio.run(server.call_tool(name, {"workspace_id": "ws", **args}))
        return json.loads("".join(b.text for b in blocks if b.type == "text"))

    return run, state, calls


def test_large_node_export_edit_save_preserves_every_byte(authoring):
    run, state, calls = authoring
    source = 'CSS = "' + r"a\n\\b" * 39000 + '"\r\n'
    source += "A = 1\r\nB = 2\r\ndef run(ctx):\r\n    return A + B\r\n"
    assert len(source.encode()) > 225 * 1024
    ast.parse(source)
    state["read"] = {"code": source, "draft_code": None}
    exported = run("get_node_code", node_key="render", output_path="node.json")
    payload = Path(exported["output_path"]).read_bytes()
    assert exported["sha256"] == hashlib.sha256(payload).hexdigest()
    code = json.loads(payload)["code"]
    source_path = Path(exported["output_path"]).with_name("node.py")
    source_path.write_bytes(code.encode())
    for edited in (
        code,
        code.replace("A = 1", "A = 3").replace("B = 2", "B = 4").replace("A + B", "A * B"),
    ):
        source_path.write_bytes(edited.encode())
        ast.parse(edited)
        for _ in range(2):
            saved = run("save_node_code_draft", node_key="render", code_path=str(source_path))
            assert "code" not in saved
            assert saved["code_sha256"] == hashlib.sha256(edited.encode()).hexdigest()
            assert calls[-1][0] == "PUT"
            assert calls[-1][2]["code"].encode() == edited.encode()
    with pytest.raises(Exception, match="exists"):
        run("get_node_code", node_key="render", output_path="node.json")
    assert Path(exported["output_path"]).read_bytes() == payload


@pytest.mark.parametrize("kind", ["skill", "shared"])
def test_full_package_export_and_one_line_edit_without_model_payload(authoring, kind):
    run, state, calls = authoring
    files = [
        {
            "path": f"references/{i}.md",
            "content": ("中文\\n\\\\\r\n" * 500) + "before",
            "size": 1,
            "truncated": False,
        }
        for i in range(17)
    ]
    state["read"] = {"files": files}
    extra = {"skill_key": "ws/skill"} if kind == "skill" else {}
    exported = run(
        "get_skill" if kind == "skill" else "get_shared_materials",
        output_path="files.json",
        **extra,
    )
    path = Path(exported["output_path"])
    if kind == "skill":
        extra.update(new_tag="v2", message="precise edit")
    save = "save_skill_version" if kind == "skill" else "save_shared_materials"
    saved = run(save, files_path=str(path), **extra)
    if kind == "shared":
        assert "content" not in saved["files"][0]
        assert (
            saved["files"][0]["content_sha256"]
            == hashlib.sha256(files[0]["content"].encode()).hexdigest()
        )
    assert calls[-1][2]["files"] == [{"path": f["path"], "content": f["content"]} for f in files]
    edited = json.loads(path.read_bytes())
    edited["files"][0]["content"] = edited["files"][0]["content"].replace("before", "after")
    path.write_bytes(json.dumps(edited, ensure_ascii=False).encode())
    run(save, files_path=str(path), **extra)
    assert calls[-1][2]["files"][0]["content"].endswith("after")
    assert calls[-1][2]["files"][1]["content"] == files[1]["content"]


def test_per_file_path_and_inline_content_can_mix(authoring):
    run, _, calls = authoring
    root = local_files.staging_root("ws")
    root.mkdir(parents=True)
    data = (r'print("\n\\")' + "\r\n") * 350
    (root / "validator.py").write_bytes(data.encode())
    run(
        "save_skill_version",
        skill_key="ws/skill",
        new_tag="v2",
        message="edit",
        files=[
            {"path": "scripts/validate_output.py", "file_path": "validator.py"},
            {"path": "SKILL.md", "content": "# Skill"},
        ],
    )
    assert calls[-1][2]["files"][0]["content"].encode() == data.encode()


def test_failed_authorization_prevents_filesystem_access(authoring, monkeypatch):
    run, state, calls = authoring
    state["authorized"] = False

    def forbidden(*args):
        pytest.fail("read before workspace authorization")

    monkeypatch.setattr(local_files, "read_text", forbidden)
    with pytest.raises(Exception, match="403"):
        run("save_node_code_draft", node_key="n", code_path="secret.py")
    assert not any(method != "GET" for method, _, _ in calls)


@pytest.mark.parametrize(
    "bad",
    [
        {"path": "a", "content": "x", "file_path": "a"},
        {"path": "a", "content": "x", "truncated": True},
        {"path": "a", "file_path": "../outside"},
        {"path": "a", "file_path": "missing"},
    ],
)
def test_bad_file_in_batch_prevents_any_mutation(authoring, bad):
    run, _, calls = authoring
    root = local_files.staging_root("ws")
    root.mkdir(parents=True)
    (root / "files.json").write_text(json.dumps([{"path": "good", "content": "ok"}, bad]))
    with pytest.raises(ToolError):
        run("save_shared_materials", files_path="files.json")
    assert not any(method != "GET" for method, _, _ in calls)


@pytest.mark.parametrize("component", ["workspace", "directory", "file", "hardlink", "fifo"])
def test_links_and_nonregular_files_are_rejected(tmp_path, monkeypatch, component):
    monkeypatch.chdir(tmp_path)
    root = local_files.staging_root("ws")
    root.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret")
    if component == "workspace":
        root.symlink_to(outside, target_is_directory=True)
        path = "secret"
    else:
        root.mkdir()
        path = "secret"
        if component == "directory":
            (root / "nested").symlink_to(outside, target_is_directory=True)
            path = "nested/secret"
        elif component == "file":
            (root / path).symlink_to(outside / "secret")
        elif component == "hardlink":
            os.link(outside / "secret", root / path)
        else:
            os.mkfifo(root / path)
    with pytest.raises((ValueError, OSError)):
        local_files.read_text("ws", path)


def test_traversal_foreign_absolute_invalid_utf8_and_size(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = local_files.staging_root("ws")
    root.mkdir(parents=True)
    for path in ("../other/file", str(tmp_path / "outside"), str(root.parent / "foreign" / "file")):
        with pytest.raises((ValueError, OSError)):
            local_files.read_text("ws", path)
    with pytest.raises(ValueError):
        local_files.staging_root("../ws")
    (root / "bad").write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        local_files.read_text("ws", "bad")
    monkeypatch.setattr(local_files, "MAX_BYTES", 3)
    (root / "big").write_bytes(b"1234")
    with pytest.raises(ValueError, match="exceeds"):
        local_files.read_text("ws", "big")


def test_export_http_failure_does_not_create_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = asyncio.run(local_files.export_response("ws", "error.json", "HTTP 404: missing"))
    assert response == "HTTP 404: missing"
    assert not local_files.staging_root("ws").exists()
