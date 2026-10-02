"""Exercise real HTTP authoring snapshots, not display-shaped mock payloads."""

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import mint_scoped_token
from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server
from server.app.mcp_server.tool_client import ToolClient


@pytest.fixture
def snapshot_channel(client, job_db, tmp_path, monkeypatch):
    workspace = "edit-snapshot"
    job_db.create_workspace("Snapshot", default_workflow_key=workspace, workspace_id=workspace)
    token = mint_scoped_token(
        job_db, str(job_db.get_user_credentials("admin")["id"]), workspace_id=workspace
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ".agents" / "skills" / workspace
    root.mkdir(parents=True)
    # The app constructs its catalog before this fixture changes HOME.
    # Relocate only the catalog root; exercise the real detail and HTTP paths.
    from server.app.services.skill_catalog import SkillCatalogService

    original = SkillCatalogService._skill_dir

    def skill_dir(catalog, key):
        catalog.base_dir = root.parent
        return original(catalog, key)

    monkeypatch.setattr(SkillCatalogService, "_skill_dir", skill_dir)
    headers = {"Authorization": f"Bearer {token}"}

    async def call(self, method, path, body=None, **kwargs):
        response = client.request(
            method, "/api/studio-agent/tools" + path, json=body, headers=headers
        )
        return (
            response.text
            if response.is_success
            else f"HTTP {response.status_code}: {response.text}"
        )

    monkeypatch.setattr(ToolClient, "call", call)
    server = create_mcp_server(McpServerConfig("http://backend", token))

    def run(name, **args):
        blocks = asyncio.run(server.call_tool(name, {"workspace_id": workspace, **args}))
        return "".join(block.text for block in blocks if block.type == "text")

    return run, root


def test_shared_full_snapshot_preserves_map_and_every_writable_file(snapshot_channel):
    run, root = snapshot_channel
    raw_map = '{\r\n  "version": 1, "materials": []\r\n}\r\n'
    contents = {
        "map.json": raw_map,
        "references/table.csv": "a,b\r\n1,2\r\n",
        "scripts/Makefile": "all:\r\n\ttrue\r\n",
        "references/style.md": "old\r\n",
    }
    json.loads(
        run(
            "save_shared_materials",
            files=[{"path": path, "content": content} for path, content in contents.items()],
        )
    )
    exported = json.loads(run("get_shared_materials", output_path="shared.json"))
    path = Path(exported["output_path"])
    snapshot = json.loads(path.read_bytes())
    assert {f["path"]: f["content"] for f in snapshot["files"]} == contents
    for edited in (False, True):
        if edited:
            for item in snapshot["files"]:
                if item["path"] == "references/style.md":
                    item["content"] = "new\r\n"
            contents["references/style.md"] = "new\r\n"
            path.write_bytes(json.dumps(snapshot).encode())
        receipt = json.loads(run("save_shared_materials", files_path=str(path)))
        assert "workspace_id" in receipt
        for relative, content in contents.items():
            assert hashlib.sha256((root / "_shared" / relative).read_bytes()).digest() == (
                hashlib.sha256(content.encode()).digest()
            )


@pytest.mark.parametrize("kind", ["skill", "shared"])
@pytest.mark.parametrize(
    "bad", [b"bad\xff", b"x" * (128 * 1024 + 1)], ids=["invalid-utf8", "oversized"]
)
def test_lossy_or_truncated_exports_fail_before_creating_staging_file(snapshot_channel, kind, bad):
    run, root = snapshot_channel
    folder = root / ("example" if kind == "skill" else "_shared")
    (folder / "references").mkdir(parents=True)
    (folder / "references" / "bad.md").write_bytes(bad)
    (folder / "map.json").write_text('{"version": 1, "materials": []}')
    kwargs = {"skill_key": "edit-snapshot/example"} if kind == "skill" else {}
    response = run(
        "get_skill" if kind == "skill" else "get_shared_materials", output_path="bad.json", **kwargs
    )
    assert response.startswith("HTTP 422:"), response
    assert not (Path.cwd() / "data/studio-mcp-files/edit-snapshot/bad.json").exists()
    assert (folder / "references/bad.md").read_bytes() == bad


@pytest.mark.parametrize("ref", [None, "v1"])
def test_skill_edit_export_includes_all_writable_paths_and_preserves_bytes(snapshot_channel, ref):
    run, root = snapshot_channel
    repo = root / "example"
    contents = {
        "README.md": "root\r\n",
        "scripts/data.csv": "a,b\r\n",
        "scripts/Makefile": "all:\r\n",
    }
    for relative, content in contents.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "snapshot",
        "--no-gpg-sign",
    )
    git("tag", "v1")
    exported = json.loads(
        run("get_skill", skill_key="edit-snapshot/example", ref=ref, output_path="skill.json")
    )
    files = json.loads(Path(exported["output_path"]).read_bytes())["files"]
    assert {f["path"]: f["content"] for f in files} == contents


@pytest.mark.parametrize("ref", [None, "v1"])
def test_group_skill_display_does_not_authorize_edit_export(snapshot_channel, ref, client, job_db):
    run, root = snapshot_channel
    repo = root.parent / "shared-group" / "example"
    repo.mkdir(parents=True)
    (repo / "SKILL.md").write_text("public skill")
    (repo / ".env").write_text("private sentinel")
    (repo / "internal.md").write_text("internal sentinel")
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "fixture",
            "--no-gpg-sign",
        ],
        ["tag", "v1"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    key = "shared-group/example"
    display = json.loads(run("get_skill", skill_key=key, ref=ref))
    assert [item["path"] for item in display["files"]] == ["SKILL.md"]
    rejected = run("get_skill", skill_key=key, ref=ref, output_path="group.json")
    assert rejected.startswith("HTTP 404:"), rejected
    assert "private sentinel" not in rejected
    assert "internal sentinel" not in rejected
    assert not (Path.cwd() / "data/studio-mcp-files/edit-snapshot/group.json").exists()
    response = client.get(
        f"/api/studio-agent/tools/workspaces/edit-snapshot/skills/{key}",
        params={"for_edit": "true", **({"ref": ref} if ref else {})},
        headers={
            "Authorization": "Bearer "
            + mint_scoped_token(
                job_db,
                str(job_db.get_user_credentials("admin")["id"]),
                workspace_id="edit-snapshot",
            )
        },
    )
    assert response.status_code == 404
