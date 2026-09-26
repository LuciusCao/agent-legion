"""External artifact-access routes (#631): the local-fallback branch —
instances without a bucket list and serve job_dir copies, narrowed to the
job snapshot's declared outputs (#703 codex round 4).

Split from test_external_artifacts.py when it crossed the 800-line
test-file budget (#779 codex train review P1-3); cases migrated verbatim.
Shared seeding lives in external_artifact_testlib.py / the directory
conftest.
"""

from __future__ import annotations

from pathlib import Path

from tests.routes.jobs.external_artifact_testlib import (
    _create_job,
    _publish_subpath_revision,
    _seed_workspace,
)


def test_artifact_list_local_only_names_without_object_store(client_factory, monkeypatch):
    """Instance without a bucket: job_dir names still list (storage=local,
    no manifest metadata), object_storage_enabled flags the degradation."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "script.md").write_text("{}", encoding="utf-8")

        response = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts")

    assert response.status_code == 200
    body = response.json()
    assert body["object_storage_enabled"] is False
    entry = next(e for e in body["artifacts"] if e["name"] == "script.md")
    assert entry["storage"] == "local"
    assert entry["size_bytes"] is None
    assert entry["content_hash"] == ""
    assert entry["uploaded_at"] is None


def test_raw_downloads_text_artifact_from_local_cache(two_workspaces):
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    # 声明名（demo 快照 outputs）——#703 codex4 后未声明名不可下载。
    (storage / "script.md").write_text("# script", encoding="utf-8")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/script.md/raw")

    assert response.status_code == 200
    # 白名单外扩展名（含 .json/.html）按 raw 白名单策略强制下载。
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in response.headers.get("content-disposition", "")
    assert response.content == b"# script"


def test_subpath_artifact_local_only_roundtrip(client_factory, monkeypatch):
    """Local-only subpath artifacts (instance without a bucket): the deep
    listing finds DECLARED files under subdirectories (the root-only scan
    missed them) and the raw endpoint serves them from the local copy."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        _publish_subpath_revision(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        (storage / "reports").mkdir(parents=True, exist_ok=True)
        (storage / "reports" / "final.json").write_text('{"ok": 1}', encoding="utf-8")
        (storage / "script.md").write_text("top", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        entries = {e["name"]: e for e in listing["artifacts"]}
        assert entries["reports/final.json"]["storage"] == "local"
        assert entries["script.md"]["storage"] == "local"

        response = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/reports/final.json/raw")
        assert response.status_code == 200
        assert response.content == b'{"ok": 1}'

        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert "reports/final.json" in status["artifacts"]


def test_local_listing_prunes_runs_and_hidden_dirs(client_factory, monkeypatch):
    """The deep scan lists artifacts, not job_dir internals: ``runs/`` holds
    per-node run dirs (events.jsonl) and dot-directories are staging/trash —
    neither may surface as a downloadable artifact name."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        (storage / "runs" / "node_a" / "token1").mkdir(parents=True, exist_ok=True)
        (storage / "runs" / "node_a" / "token1" / "events.jsonl").write_text("{}", encoding="utf-8")
        (storage / ".result-staging-x").mkdir(parents=True, exist_ok=True)
        (storage / ".result-staging-x" / "leak.txt").write_text("x", encoding="utf-8")
        (storage / "script.md").write_text("{}", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()

        names = [e["name"] for e in listing["artifacts"]]
        assert names == ["script.md"]


# --- #703 codex round 4 (P2-1)：本地子路径面收窄到声明 outputs ----------------


def test_undeclared_nested_files_not_listed_not_downloadable(client_factory, monkeypatch):
    """code 节点在 job_dir 留下的未声明嵌套文件（scratch/debug.json）：修
    复前递归扫描把它当 local 产物公布、raw 端点按相对路径返回字节——中间
    文件被外部读取面暴露，且每次状态轮询都遍历非产物文件。现在列举与下
    载都收窄到 job 快照声明的 outputs（本用例的 workspace 只声明根级名，
    scratch/ 整棵子树既不列也不可达）。"""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        (storage / "scratch").mkdir(parents=True, exist_ok=True)
        (storage / "scratch" / "debug.json").write_text('{"internal": "debug"}', encoding="utf-8")
        (storage / "notes.txt").write_text("undeclared top-level", encoding="utf-8")
        (storage / "script.md").write_text("# declared", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == ["script.md"]

        scratch_read = c.get(
            f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/scratch/debug.json/raw"
        )
        notes_read = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/notes.txt/raw")
        assert scratch_read.status_code == 404
        assert b"debug" not in scratch_read.content
        assert notes_read.status_code == 404
        # 声明名照常（对照，判别力：门收窄不误伤合法产物）。
        declared = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/script.md/raw")
        assert declared.status_code == 200
        assert declared.content == b"# declared"


def test_no_snapshot_job_local_listing_empty_and_undeclared_404(
    client_factory, monkeypatch, job_db
):
    """无快照的 legacy job（snapshot 解析为 None → 声明集为空）：本地清单
    为空、未声明名下载 404——列举与下载同语义；manifest 行照常（enabled
    门控不变）。"""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "legacy.txt").write_text("old", encoding="utf-8")
        # 抹掉快照：模拟 v50 之前的 legacy 行。
        from server.app.db.transaction import write_transaction

        with write_transaction(job_db) as conn:
            conn.execute(
                "update jobs set workflow_definition_snapshot_json='' where id=%s",
                (job["id"],),
            )

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == []
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == []
        assert (
            c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/legacy.txt/raw").status_code
            == 404
        )


# --- #703 复审 MEDIUM-1：symlink 文件不进清单 ---------------------------------


def test_symlink_files_are_not_advertised(client_factory, monkeypatch, tmp_path):
    """#703 复审 MEDIUM-1：job_dir 内的 symlink 文件不进清单。
    ``os.walk(followlinks=False)`` 只剪枝目录链接；链接文件会以合法名过
    白名单进 list/status，而 raw 端包含性校验对指向 job_dir 外的目标答
    400——清单列了但 raw 拒绝，破坏 list→download 契约。链接整体跳过
    （与目录链接同语义），正常文件不受影响。"""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "script.md").write_text("{}", encoding="utf-8")
        outside = tmp_path / "outside.json"
        outside.write_text("outside-secret", encoding="utf-8")
        (storage / "link.json").symlink_to(outside)

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == ["script.md"]

        # raw 端语义：链接名要么被声明门拦（404——名字未声明），要么到包
        # 含性校验被拒（400）；无论哪条路，都绝不读到 job_dir 外的目标字节。
        raw = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/link.json/raw")
        assert raw.status_code in (400, 404)
        assert b"outside-secret" not in raw.content
