"""Byte-exact large node round trips through MCP, auth, routes and PostgreSQL."""

import asyncio
import hashlib
import json
from pathlib import Path

from server.app.auth.scoped_tokens import mint_scoped_token
from server.app.mcp_server import local_files
from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server
from server.app.mcp_server.tool_client import ToolClient
from server.app.services.node_codes import NodeCodeService
from server.app.services.preview_panels import MAX_BUNDLE_BYTES, PreviewPanelService


def test_large_published_node_roundtrip_and_three_line_edit(client, job_db, tmp_path, monkeypatch):
    workspace = "byte-exact-node"
    job_db.create_workspace("Byte-exact test", workspace_id=workspace)
    user_id = str(job_db.get_user_credentials("admin")["id"])
    token = mint_scoped_token(job_db, user_id, workspace_id=workspace)
    monkeypatch.setattr(
        client.app.state.settings.executor_runtime.workflows, "node_code_max_bytes", 512 * 1024
    )
    service = NodeCodeService(job_db, max_code_bytes=512 * 1024)
    source = 'CSS = "' + r"a\n\\b" * 39000 + '"\r\n'
    source += "A = 1\r\nB = 2\r\ndef run(ctx):\r\n    return A + B\r\n"
    published = service.save_draft(workspace, workspace, "render", source, user_id)
    service.publish(workspace, workspace, "render")

    async def call(self, method, path, body=None, **kwargs):
        response = client.request(
            method,
            "/api/studio-agent/tools" + path,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code < 300, response.text
        return response.text

    monkeypatch.setattr(ToolClient, "call", call)
    monkeypatch.chdir(tmp_path)
    server = create_mcp_server(McpServerConfig("http://backend", token))

    def run(name, **args):
        blocks = asyncio.run(
            server.call_tool(name, {"workspace_id": workspace, "node_key": "render", **args})
        )
        return json.loads("".join(block.text for block in blocks if block.type == "text"))

    exported = run("get_node_code", output_path="node.json")
    source_path = Path(exported["output_path"]).with_name("node.py")
    content = json.loads(Path(exported["output_path"]).read_bytes())["code"]
    assert content == source
    for edited in (
        content,
        content.replace("A = 1", "A = 3").replace("B = 2", "B = 4").replace("A + B", "A * B"),
    ):
        source_path.write_bytes(edited.encode())
        for _ in range(2):
            saved = run(
                "save_node_code_draft", code_path=str(source_path), expected_capability="render"
            )
            assert saved["code_hash"] == hashlib.sha256(edited.encode()).hexdigest()
            assert "code" not in saved
            if edited == content:
                assert saved["code_hash"] == published["code_hash"]
            draft = next(
                row
                for row in service.list_versions(workspace, workspace, "render")
                if row["status"] == "draft"
            )
            assert draft["code"].encode() == edited.encode()


def test_preview_panel_html_path_roundtrip(client, job_db, tmp_path, monkeypatch):
    """#1272: save_preview_panel_draft reads staged HTML byte-exact; the draft
    hash matches the file's sha256 and the state export round-trips."""
    workspace = "byte-exact-panel"
    job_db.create_workspace("Byte-exact panel", workspace_id=workspace)
    user_id = str(job_db.get_user_credentials("admin")["id"])
    token = mint_scoped_token(job_db, user_id, workspace_id=workspace)

    async def call(self, method, path, body=None, **kwargs):
        response = client.request(
            method,
            "/api/studio-agent/tools" + path,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code < 300, response.text
        return response.text

    monkeypatch.setattr(ToolClient, "call", call)
    monkeypatch.chdir(tmp_path)
    server = create_mcp_server(McpServerConfig("http://backend", token))

    def run(name, **args):
        blocks = asyncio.run(server.call_tool(name, {"workspace_id": workspace, **args}))
        return json.loads("".join(b.text for b in blocks if b.type == "text"))

    root = local_files.staging_root(workspace)
    root.mkdir(parents=True)
    # Near the 256 KiB ceiling (3-byte UTF-8 chars), CRLF and escapes intact.
    filler = "面板\\n\\\\\r\n" * (MAX_BUNDLE_BYTES // 24)
    html = f"<!doctype html>\r\n<html>\r\n<body>{filler}</body>\r\n</html>\r\n"
    assert len(html.encode()) < MAX_BUNDLE_BYTES
    (root / "panel.html").write_bytes(html.encode())

    saved = run("save_preview_panel_draft", html_path="panel.html", change_note="staged")
    assert saved["html_hash"] == hashlib.sha256(html.encode()).hexdigest()
    assert "html" not in saved
    draft = PreviewPanelService(job_db).get_draft(workspace)
    assert draft is not None
    assert draft["html"].encode() == html.encode()
    assert draft["change_note"] == "staged"

    exported = run("get_preview_panel", output_path="panel.json")
    payload = json.loads(Path(exported["output_path"]).read_bytes())
    assert payload["draft"]["html"] == html
    assert payload["draft"]["html_hash"] == saved["html_hash"]
