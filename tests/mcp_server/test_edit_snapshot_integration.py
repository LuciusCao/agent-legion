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
from tests.helpers.skill_snapshot import commit, git


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
    from server.app.services.skill_editing import SkillEditingService

    original_repo_dir = SkillEditingService._skill_dir

    def editing_repo_dir(service, key):
        service.base_dir = root.parent
        return original_repo_dir(service, key)

    monkeypatch.setattr(SkillEditingService, "_skill_dir", editing_repo_dir)
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


@pytest.mark.parametrize("failure", ["deadline", "tree-budget", "batch-protocol"])
@pytest.mark.parametrize("ref", [None, "v1"])
def test_git_resource_failures_never_create_export(snapshot_channel, monkeypatch, failure, ref):
    from server.app.services import skill_commit_snapshot, skill_snapshot_git

    run, root = snapshot_channel
    repo = root / "example"
    repo.mkdir()
    (repo / "SKILL.md").write_text("committed")
    git(repo, "init", "-q")
    commit(repo)
    git(repo, "tag", "v1")
    if failure == "deadline":
        monkeypatch.setattr(skill_snapshot_git, "SNAPSHOT_SECONDS", 0)
    elif failure == "tree-budget":
        monkeypatch.setattr(skill_commit_snapshot, "MAX_SNAPSHOT_BYTES", 1)
    else:
        original = skill_snapshot_git.SnapshotGit.run

        def broken_batch(self, args, *pos, **kwargs):
            return b"missing\n" if args[0] == "cat-file" else original(self, args, *pos, **kwargs)

        monkeypatch.setattr(skill_snapshot_git.SnapshotGit, "run", broken_batch)
    result = run("get_skill", skill_key="edit-snapshot/example", ref=ref, output_path="failed.json")
    assert "422" in result
    assert not (Path.cwd() / "data/studio-mcp-files/edit-snapshot/failed.json").exists()
    assert (repo / "SKILL.md").read_text() == "committed"


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


def test_maximum_shared_snapshot_round_trip_with_worst_case_json_escaping(snapshot_channel):
    from server.app.services.skill_repo import MAX_FILE_BYTES

    run, root = snapshot_channel
    # One control byte becomes six JSON bytes: 99 legal full-size files
    # plus map.json need over 74 MiB on the wire, despite <13 MiB on disk.
    content = "\x00" * MAX_FILE_BYTES
    files = [{"path": "map.json", "content": '{"version":1,"materials":[]}'}]
    files.extend({"path": f"references/{i}.txt", "content": content} for i in range(99))
    initial = json.loads(run("save_shared_materials", files=files))
    assert initial["workspace_id"] == "edit-snapshot"
    exported = json.loads(run("get_shared_materials", output_path="maximum.json"))
    assert exported["size"] > 74 * 1024 * 1024
    snapshot = Path(exported["output_path"])
    assert exported["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    receipt = json.loads(run("save_shared_materials", files_path=str(snapshot)))
    assert receipt["workspace_id"] == "edit-snapshot"
    expected = hashlib.sha256(content.encode()).hexdigest()
    assert len(receipt["files"]) == 99  # map is returned separately by the display response
    assert all(item["content_sha256"] == expected for item in receipt["files"])
    for item in files:
        assert (root / "_shared" / item["path"]).read_bytes() == item["content"].encode()


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
    if kind == "skill":
        git(folder, "init", "-q")
        commit(folder)
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


@pytest.mark.parametrize("ref", [None, "v1"])
def test_large_skill_snapshot_selected_save_preserves_other_files(snapshot_channel, ref):
    from mcp.server.fastmcp.exceptions import ToolError

    run, root = snapshot_channel
    repo = root / "example"
    (repo / "references").mkdir(parents=True)
    (repo / "scripts").mkdir()
    contents = {
        "SKILL.md": "# Skill\r\n",
        "references/output-contract.md": "# Output\r\n",
        "scripts/validate_output.py": "raise SystemExit(0)\r\n",
        ".gitignore": ".env\n",
        **{f"references/{i}.txt": f"unchanged {i}\r\n" for i in range(97)},
    }
    for path, content in contents.items():
        (repo / path).write_bytes(content.encode())
    git(repo, "init", "-q")
    commit(repo)
    git(repo, "tag", "v1")
    (repo / ".env").write_text("host-secret")
    exported = json.loads(
        run("get_skill", skill_key="edit-snapshot/example", ref=ref, output_path="large.json")
    )
    path = Path(exported["output_path"])
    document = json.loads(path.read_bytes())
    assert {f["path"]: f["content"] for f in document["files"]} == contents
    head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ToolError, match="1–100"):
        run(
            "save_skill_version",
            skill_key="edit-snapshot/example",
            files_path=str(path),
            new_tag="v2",
            message="too many",
        )
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "tag", "--list", "v2") == b""
    document["files"] = [{"path": "SKILL.md", "content": "# Changed\r\n"}]
    path.write_bytes(json.dumps(document).encode())
    saved = json.loads(
        run(
            "save_skill_version",
            skill_key="edit-snapshot/example",
            files_path=str(path),
            new_tag="v2",
            message="one change",
        )
    )
    assert saved["tag"] == "v2"
    contents["SKILL.md"] = "# Changed\r\n"
    for relative, content in contents.items():
        assert (repo / relative).read_bytes() == content.encode()
    assert (repo / ".env").read_text() == "host-secret"
    assert b".env" not in git(repo, "ls-tree", "--name-only", "HEAD")


@pytest.mark.parametrize("kind", ["symlink", "gitlink", "invalid-utf8"])
@pytest.mark.parametrize("ref", [None, "v1"])
def test_invalid_git_snapshot_never_creates_export(snapshot_channel, kind, ref):
    run, root = snapshot_channel
    repo = root / "example"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "SKILL.md").write_text("valid")
    commit(repo)
    if kind == "gitlink":
        head = git(repo, "rev-parse", "HEAD").decode().strip()
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},submodule")
        git(repo, "commit", "-qm", "gitlink", "--no-gpg-sign")
    else:
        if kind == "symlink":
            (repo / "bad").symlink_to("SKILL.md")
        else:
            (repo / "bad").write_bytes(b"\xff")
        commit(repo)
    git(repo, "tag", "v1")
    response = run("get_skill", skill_key="edit-snapshot/example", ref=ref, output_path="bad.json")
    assert response.startswith("HTTP 422:"), response
    assert not (Path.cwd() / "data/studio-mcp-files/edit-snapshot/bad.json").exists()


@pytest.mark.parametrize("ref", [None, "v1"])
@pytest.mark.parametrize("kind", ["foreign", "binding", "missing"])
def test_edit_export_authorization_precedes_git_and_staging(
    snapshot_channel, client, job_db, monkeypatch, ref, kind
):
    from server.app.services.skill_catalog import SkillCatalogService

    run, root = snapshot_channel
    job_db.create_workspace("Other", default_workflow_key="other", workspace_id="other")
    key = "other/example" if kind == "foreign" else "edit-snapshot/example"
    workspace = "other" if kind == "binding" else "edit-snapshot"
    if kind == "missing":
        key = "unknown/example"

    def forbidden(*args, **kwargs):
        pytest.fail("Git content lookup must follow edit authorization")

    monkeypatch.setattr(SkillCatalogService, "detail", forbidden)
    expected = 403 if kind == "binding" else 404
    result = run(
        "get_skill", workspace_id=workspace, skill_key=key, ref=ref, output_path="denied.json"
    )
    assert result.startswith(f"HTTP {expected}:"), result
    token = mint_scoped_token(
        job_db, str(job_db.get_user_credentials("admin")["id"]), workspace_id="edit-snapshot"
    )
    response = client.get(
        f"/api/studio-agent/tools/workspaces/{workspace}/skills/{key}",
        params={"for_edit": "true", **({"ref": ref} if ref else {})},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == expected
    assert not (Path.cwd() / "data/studio-mcp-files").exists()


@pytest.mark.parametrize("ref", [None, "v1"])
@pytest.mark.parametrize("character", ["é", "中", "😀"])
def test_accepted_unicode_skill_save_can_be_exported_and_saved_again(
    snapshot_channel, ref, character
):
    run, root = snapshot_channel
    repo = root / "example"
    (repo / "references").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "SKILL.md").write_text("# Skill")
    (repo / "references/output-contract.md").write_text("# Output")
    (repo / "scripts/validate_output.py").write_text("raise SystemExit(0)")
    git(repo, "init", "-q")
    commit(repo)
    content = character * (128 * 1024)
    saved = json.loads(
        run(
            "save_skill_version",
            skill_key="edit-snapshot/example",
            new_tag="v1",
            message="unicode",
            files=[{"path": "references/unicode.txt", "content": content}],
        )
    )
    assert saved["tag"] == "v1"
    exported = json.loads(
        run("get_skill", skill_key="edit-snapshot/example", ref=ref, output_path="unicode.json")
    )
    path = Path(exported["output_path"])
    files = json.loads(path.read_bytes())["files"]
    selected = next(f for f in files if f["path"] == "references/unicode.txt")
    assert selected["content"] == content
    content = "x" + content[1:]
    selected["content"] = content
    path.write_bytes(json.dumps([selected], ensure_ascii=False).encode())
    saved = json.loads(
        run(
            "save_skill_version",
            skill_key="edit-snapshot/example",
            new_tag="v2",
            message="round trip",
            files_path=str(path),
        )
    )
    assert saved["tag"] == "v2"
    assert (repo / "references/unicode.txt").read_bytes() == content.encode()
