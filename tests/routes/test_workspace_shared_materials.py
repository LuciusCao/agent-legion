"""User-facing workspace shared-materials view endpoints (#643).

``GET /api/workspaces/{id}/skills-shared`` (map + content-free listing +
per-skill drift) and ``GET .../skills-shared/file`` (single-file text):
session auth (anonymous rejected), the structured empty state, all four
drift statuses against real git repos under a monkeypatched HOME, path
safety on the file read, and 404 on unknown workspaces. Fixture pattern
follows test_studio_agent_shared_tools.py / test_studio_agent_skill_tools.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_WS = "shared-view-ws"
_BASE = f"/api/workspaces/{_WS}/skills-shared"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _make_skill_repo(repo: Path, files: dict[str, str]) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ws_dir = home / ".agents" / "skills" / _WS
    ws_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return ws_dir


def _create_workspace(client) -> None:
    response = client.post("/api/workspaces", json={"id": _WS, "name": "Shared View WS"})
    assert response.status_code == 200, response.text


def _seed_shared(shared_home: Path, content: str = "# house style v2\n") -> None:
    """_shared with one material mapped to the four drift-state skills."""
    shared = shared_home / "_shared"
    (shared / "references").mkdir(parents=True)
    (shared / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [
                    {
                        "source": "references/style.md",
                        "skills": ["synced-skill", "drifted-skill", "missing-skill", "ghost-skill"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (shared / "references" / "style.md").write_text(content, encoding="utf-8")
    _make_skill_repo(shared_home / "synced-skill", {"references/style.md": content})
    _make_skill_repo(shared_home / "drifted-skill", {"references/style.md": "# old copy\n"})
    _make_skill_repo(shared_home / "missing-skill", {"SKILL.md": "# no materials\n"})
    # ghost-skill: no repo directory at all.


def test_anonymous_is_rejected(anon_client, shared_home) -> None:
    del shared_home
    assert anon_client.get(_BASE).status_code == 401
    assert anon_client.get(f"{_BASE}/file", params={"path": "references/a.md"}).status_code == 401


def test_empty_state(client, shared_home) -> None:
    del shared_home
    _create_workspace(client)
    response = client.get(_BASE)
    assert response.status_code == 200, response.text
    assert response.json() == {"workspace_id": _WS, "map": None, "files": []}


def test_map_listing_and_drift_statuses(client, shared_home) -> None:
    _create_workspace(client)
    _seed_shared(shared_home)
    response = client.get(_BASE)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["map"]["version"] == 1
    (material,) = payload["map"]["materials"]
    assert material["source"] == "references/style.md"
    statuses = {entry["skill"]: entry["status"] for entry in material["skills"]}
    assert statuses == {
        "synced-skill": "synced",
        "drifted-skill": "pending_sync",
        "missing-skill": "missing_in_skill",
        "ghost-skill": "skill_not_found",
    }
    # The listing carries metadata only — contents come from /file.
    (entry,) = payload["files"]
    assert entry["path"] == "references/style.md"
    assert entry["size"] == len(b"# house style v2\n")
    assert entry["modified_at"]
    assert "content" not in entry


def test_drift_tracks_shared_source_updates(client, shared_home) -> None:
    """A synced skill flips to pending_sync when the shared source changes
    (the fix is re-saving the skill, which re-runs the sync)."""
    _create_workspace(client)
    _seed_shared(shared_home)
    (shared_home / "_shared" / "references" / "style.md").write_text(
        "# house style v3\n", encoding="utf-8"
    )
    payload = client.get(_BASE).json()
    statuses = {
        entry["skill"]: entry["status"] for entry in payload["map"]["materials"][0]["skills"]
    }
    assert statuses["synced-skill"] == "pending_sync"
    assert statuses["drifted-skill"] == "pending_sync"


def test_file_endpoint_returns_text(client, shared_home) -> None:
    _create_workspace(client)
    _seed_shared(shared_home)
    response = client.get(f"{_BASE}/file", params={"path": "references/style.md"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "path": "references/style.md",
        "size": len(b"# house style v2\n"),
        "content": "# house style v2\n",
        "truncated": False,
    }


def test_file_endpoint_rejects_path_escape_and_git_components(client, shared_home) -> None:
    _create_workspace(client)
    _seed_shared(shared_home)
    for bad in ("../escape.md", "/abs/style.md", "references/../map.json", ".git/config"):
        response = client.get(f"{_BASE}/file", params={"path": bad})
        assert response.status_code == 422, bad


def test_listing_covers_whole_shared_dir_except_map(client, shared_home) -> None:
    """#643 分组反馈：用户态清单扩到 _shared 全量（除 map.json）——根
    文件与 references//scripts/ 之外的子目录文件都进清单（UI 归入
    「其他」组）；map.json 已由徽标可视化，不进清单。agent 版端点的
    files 语义保持两目录不变。"""
    _create_workspace(client)
    _seed_shared(shared_home)
    shared = shared_home / "_shared"
    (shared / "notes.md").write_text("root note\n", encoding="utf-8")
    (shared / "docs").mkdir()
    (shared / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    (shared / "docs" / "data.bin").write_bytes(b"\x00\x01")  # 非文本扩展名，不进清单

    payload = client.get(_BASE).json()
    paths = [f["path"] for f in payload["files"]]
    assert "references/style.md" in paths
    assert "notes.md" in paths
    assert "docs/guide.md" in paths
    assert "map.json" not in paths
    assert "docs/data.bin" not in paths

    # 「其他」组文件同样可读（路径安全规则放宽到 _shared 内任意相对路径）。
    note = client.get(f"{_BASE}/file", params={"path": "notes.md"})
    assert note.status_code == 200, note.text
    assert note.json()["content"] == "root note\n"
    guide = client.get(f"{_BASE}/file", params={"path": "docs/guide.md"})
    assert guide.status_code == 200, guide.text


def test_file_endpoint_refuses_intermediate_symlink_escape(client, shared_home, tmp_path) -> None:
    """codex P1：`_shared/docs -> 外部目录` 这类中间 symlink 能过词法
    校验，读取前必须 resolve 并验证仍在 _shared 内，否则可读到 skill
    root 外任意受支持扩展名文件。"""
    _create_workspace(client)
    _seed_shared(shared_home)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret.md").write_text("top secret\n", encoding="utf-8")
    (shared_home / "_shared" / "docs").symlink_to(outside)

    response = client.get(f"{_BASE}/file", params={"path": "docs/secret.md"})
    assert response.status_code == 404, response.text
    # 词法上的越界依然是 422（两类拒绝分工不变）。
    assert client.get(f"{_BASE}/file", params={"path": "../x.md"}).status_code == 422


def test_file_endpoint_missing_file_is_404(client, shared_home) -> None:
    _create_workspace(client)
    _seed_shared(shared_home)
    response = client.get(f"{_BASE}/file", params={"path": "references/nope.md"})
    assert response.status_code == 404, response.text


def test_unknown_workspace_is_404(client, shared_home) -> None:
    del shared_home
    _create_workspace(client)
    assert client.get("/api/workspaces/ws-missing/skills-shared").status_code == 404
    assert (
        client.get(
            "/api/workspaces/ws-missing/skills-shared/file",
            params={"path": "references/a.md"},
        ).status_code
        == 404
    )


def test_corrupted_map_is_structured_422(client, shared_home) -> None:
    _create_workspace(client)
    shared = shared_home / "_shared"
    (shared / "references").mkdir(parents=True)
    (shared / "map.json").write_text("{ not json", encoding="utf-8")
    response = client.get(_BASE)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["errors"][0]["path"] == "map.json"


def _seed_propagatable(shared_home: Path) -> None:
    """One material mapped to a drifted skill whose repo carries the
    runtime contract trio (save_version's post-write check enforces it)."""
    shared = shared_home / "_shared"
    (shared / "references").mkdir(parents=True)
    (shared / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/style.md", "skills": ["drifted-skill"]}],
            }
        ),
        encoding="utf-8",
    )
    (shared / "references" / "style.md").write_text("# v2\n", encoding="utf-8")
    _make_skill_repo(
        shared_home / "drifted-skill",
        {
            "SKILL.md": "# Skill\n",
            "references/style.md": "# v1\n",
            "references/output-contract.md": "# contract\n",
            "scripts/validate_output.py": "raise SystemExit(0)\n",
        },
    )


def test_propagate_anonymous_is_rejected(anon_client, shared_home) -> None:
    del shared_home
    assert anon_client.post(f"{_BASE}/propagate", json={}).status_code == 401


def test_propagate_syncs_and_reports_per_skill(client, shared_home) -> None:
    _create_workspace(client)
    _seed_propagatable(shared_home)
    response = client.post(f"{_BASE}/propagate", json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspace_id"] == _WS
    (entry,) = payload["results"]
    assert entry["skill"] == "drifted-skill"
    assert entry["status"] == "synced"
    assert entry["tag"] == "v0.1.0"
    assert entry["synced_files"] == ["references/style.md"]
    # The drift view flips to synced afterwards.
    view = client.get(_BASE).json()
    statuses = {s["skill"]: s["status"] for s in view["map"]["materials"][0]["skills"]}
    assert statuses == {"drifted-skill": "synced"}
    # A second propagate is a no-op skip.
    again = client.post(f"{_BASE}/propagate", json={"sources": ["references/style.md"]})
    assert again.json()["results"][0]["status"] == "skipped"


def test_propagate_unknown_source_is_422(client, shared_home) -> None:
    _create_workspace(client)
    _seed_propagatable(shared_home)
    response = client.post(f"{_BASE}/propagate", json={"sources": ["references/nope.md"]})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["errors"][0]["path"] == "references/nope.md"


def test_propagate_unknown_workspace_and_no_shared_are_404(client, shared_home) -> None:
    del shared_home
    _create_workspace(client)
    assert (
        client.post("/api/workspaces/ws-missing/skills-shared/propagate", json={}).status_code
        == 404
    )
    # Workspace exists but never opted into _shared.
    assert client.post(f"{_BASE}/propagate", json={}).status_code == 404


def test_scoped_token_rejected_on_user_facing_gets(client, job_db, shared_home) -> None:
    """codex P1（#674）：继承 admin 身份的 scoped token 对用户态 GET 也
    必须 403（require_workspace_access 对 admin 直接放行、不看
    scoped_workspace_id）——scoped identity 走它自己的 studio-agent
    工具端点。绑定其它 workspace 的 run token 同理。"""
    from server.app.auth import scoped_tokens

    _create_workspace(client)
    _seed_shared(shared_home)
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    for token in (
        scoped_tokens.mint_scoped_token(job_db, admin_id),
        scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id="other-ws"),
    ):
        scoped = client.__class__(client.app)
        scoped.headers["authorization"] = f"Bearer {token}"
        assert scoped.get(_BASE).status_code == 403
        assert (
            scoped.get(f"{_BASE}/file", params={"path": "references/style.md"}).status_code == 403
        )
    # 全量会话不受影响。
    assert client.get(_BASE).status_code == 200


def test_shared_dir_symlink_is_not_a_trusted_root(client, shared_home, tmp_path) -> None:
    """codex P1（#674 三轮）：`_shared -> 外部目录` 时 resolve 会把外部
    目录设为可信根、containment 必然通过——必须整体拒绝：GET 按无共享
    材料返回空态，/file 404，宿主文件不可读。"""
    _create_workspace(client)
    outside = tmp_path / "private"
    (outside / "references").mkdir(parents=True)
    (outside / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/secret.md", "skills": ["s"]}],
            }
        ),
        encoding="utf-8",
    )
    (outside / "references" / "secret.md").write_text("top secret\n", encoding="utf-8")
    (shared_home / "_shared").symlink_to(outside)

    assert client.get(_BASE).json() == {"workspace_id": _WS, "map": None, "files": []}
    assert client.get(f"{_BASE}/file", params={"path": "references/secret.md"}).status_code == 404


def test_file_endpoint_reads_size_and_content_from_one_open(
    client, shared_home, monkeypatch
) -> None:
    """codex P2（#674）：/file 在同一文件描述符上 fstat + read——目标
    文件只被 open 一次，size/truncated 与 content 不可能跨代。"""
    _create_workspace(client)
    _seed_shared(shared_home)
    real_open = Path.open
    opens: list[str] = []

    def counting_open(self, *args, **kwargs):
        if self.name == "style.md":
            opens.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    response = client.get(f"{_BASE}/file", params={"path": "references/style.md"})
    assert response.status_code == 200, response.text
    assert response.json()["size"] == len(b"# house style v2\n")
    assert len(opens) == 1


def test_workspace_dir_symlink_is_not_a_trusted_root(client, shared_home, tmp_path) -> None:
    """codex P1（#674 收尾）：`<skills_root>/<workspace_id>` 自身是
    symlink（指向其它 workspace/外部目录）时，resolve 后的 _shared 根
    会被信任、containment 必过——必须整体拒绝：GET 空态、/file 404。"""
    _create_workspace(client)
    outside = tmp_path / "other-place"
    (outside / "_shared" / "references").mkdir(parents=True)
    (outside / "_shared" / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/secret.md", "skills": ["s"]}],
            }
        ),
        encoding="utf-8",
    )
    (outside / "_shared" / "references" / "secret.md").write_text("top secret\n", encoding="utf-8")
    shared_home.rmdir()
    shared_home.symlink_to(outside)

    assert client.get(_BASE).json() == {"workspace_id": _WS, "map": None, "files": []}
    assert client.get(f"{_BASE}/file", params={"path": "references/secret.md"}).status_code == 404
