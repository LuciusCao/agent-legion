"""External artifact-access routes (#631): the access-control family —
cross-workspace 404 (no enumeration), scoped-token binding, the bare-route
parity after the #745 rebase, and the download-side name whitelist (#631
attack review M1/M2, #703 review MEDIUM-2).

Split from test_external_artifacts.py when it crossed the 800-line
test-file budget (#779 codex train review P1-3); cases migrated verbatim.
Shared seeding lives in external_artifact_testlib.py / the directory
conftest (``two_workspaces``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.fakes.storage import FakeObjectStorage
from tests.routes.jobs.external_artifact_testlib import (
    _create_job,
    _register_object_artifact,
    _seed_workspace,
)

# --- 跨 workspace / 未知对象：404 防枚举 --------------------------------------


def test_status_cross_workspace_is_404(two_workspaces):
    c, _, job_b = two_workspaces

    # ws-a member probing a ws-b job id: 404 (not 403), no enumeration.
    response = c.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}")

    assert response.status_code == 404


def test_status_unknown_job_is_404(two_workspaces):
    c, _, _ = two_workspaces

    assert c.get("/api/workspaces/ws-a/jobs/missing").status_code == 404


def test_status_unknown_workspace_is_404(two_workspaces):
    c, _, _ = two_workspaces

    # require_workspace_access rejects the non-member workspace first.
    assert c.get("/api/workspaces/ws-z/jobs/whatever").status_code == 404


def test_artifact_list_cross_workspace_is_404(two_workspaces):
    c, _, job_b = two_workspaces
    _register_object_artifact(c, job_b, "report.json", b"{}")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}/artifacts")

    assert response.status_code == 404


def test_raw_cross_workspace_is_404(two_workspaces):
    """The key security property: a ws-b artifact is unreadable through the
    ws-a prefix even though the bare /jobs/{job_id} routes exist."""
    c, _, job_b = two_workspaces
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}/artifacts/frame.png/raw")

    assert response.status_code == 404


def test_raw_missing_artifact_is_404(two_workspaces):
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/nope.png/raw")

    assert response.status_code == 404


def test_raw_rejects_traversal(two_workspaces):
    """Path-traversal immunity with the ``{artifact_name:path}`` converter
    (#631 review P2-1): the converter deliberately captures subpath names
    (``reports/final.json``), so the guard moved into the service — an
    absolute name, ``..`` segment or backslash is a 400, never a file read
    outside the job_dir (the bare ``/jobs/{job_id}/artifacts/{name:path}``
    route rejects the same family in the same place)."""
    c, job_a, _ = two_workspaces

    for name in ("..%2Fagent_legion.sqlite", "%2e%2e%2Fagent_legion.sqlite"):
        response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/{name}/raw")
        assert response.status_code == 400


# --- P1: scoped-token workspace 绑定 -----------------------------------------


def test_scoped_token_bound_to_other_workspace_is_404_on_all_three(two_workspaces, job_db):
    """#631 review P1: a Bearer token bound to scoped_workspace_id=ws-a must
    not read through ws-b even though the minting admin can see every
    workspace (require_workspace_access checks the user, not the binding).
    Mismatches are 404, not 403 — this surface answers cross-workspace probes
    with not-found, keeping the no-enumeration semantics of the job check."""
    from server.app.auth import scoped_tokens

    c, job_a, job_b = two_workspaces
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id="ws-a")
    scoped = c.__class__(c.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")

    # All three endpoints refuse the ws-b prefix for the ws-a-bound token.
    assert scoped.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}").status_code == 404
    assert scoped.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}/artifacts").status_code == 404
    assert (
        scoped.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}/artifacts/frame.png/raw").status_code
        == 404
    )
    # The bound workspace itself still reads normally (guard is a mismatch
    # check, not a scoped-token ban).
    assert scoped.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200


def test_unbound_scoped_token_still_reads_member_workspaces(two_workspaces, job_db):
    """Unbound scoped tokens keep the membership-only behaviour (schema v45):
    no scoped_workspace_id → nothing to compare, the parent membership guard
    decides."""
    from server.app.auth import scoped_tokens

    c, job_a, _ = two_workspaces
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = c.__class__(c.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    assert scoped.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200


# --- #631 codex round 3 (P2-1)：清单与下载白名单对称 ---------------------------


def test_list_and_status_only_advertise_downloadable_names(client_factory, monkeypatch, job_db):
    """#631 codex round 3 (P2-1)：清单与下载白名单对称。工作流可声明并生
    成下载侧必拒的名字（点前缀、``runs`` 段、超长段）——本地文件与对象
    manifest 行都不得把它们当产物列出（list 与 status 两个列举面同门），
    否则按清单调 raw 端点是 400，破坏 list→download 契约。声明期校验另
    立 issue，本用例只钉列举侧对齐。"""
    from server.app.db.transaction import write_transaction

    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", FakeObjectStorage())
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        # 本地落盘的必拒名：点前缀、runs 段、超长段（>200 字节）。
        (storage / ".report.json").write_text("{}", encoding="utf-8")
        (storage / "runs" / "node_a" / "token1").mkdir(parents=True, exist_ok=True)
        (storage / "runs" / "node_a" / "token1" / "events.jsonl").write_text("{}", encoding="utf-8")
        (storage / ("x" * 201 + ".json")).write_text("{}", encoding="utf-8")
        (storage / "script.md").write_text("{}", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == ["script.md"]

        # 对象 manifest 行同样过滤：绕过 Worker 校验直插一行点前缀名
        # （模拟未来写入方失守/行被污染），清单不列、按名 raw 仍 400。
        store: JobArtifactObjectStore = c.app.state.job_artifact_objects
        storage_key = f"jobs/ws-a/{job['id']}/.hidden.json"
        store.storage.objects[storage_key] = b"{}"
        with write_transaction(job_db) as conn:
            conn.execute(
                "insert into job_artifacts(job_id, node_key, name, storage_key,"
                " size_bytes, content_hash) values (%s, 'upstream', '.hidden.json',"
                " %s, 2, '')",
                (job["id"], storage_key),
            )

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        names = [e["name"] for e in listing["artifacts"]]
        assert ".hidden.json" not in names
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert ".hidden.json" not in status["artifacts"]
        assert (
            c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/.hidden.json/raw").status_code
            == 400
        )


# --- #703 复审 MEDIUM-2：越界 manifest 行不得以任何形态列出 --------------------


def test_prefix_refused_manifest_rows_are_not_advertised(two_workspaces, job_db):
    """#703 复审 MEDIUM-2：名字过白名单但 storage_key 越界的 manifest 行。
    raw 端 H1 兜底对越界行必 404（manifest-first 命中行就短路，本地副本
    不再兜底），所以 list/status 不得以任何形态（object 条目或本地条目）
    列出该名字——同名本地文件也不回填，否则清单照列、raw 照 404。"""
    from server.app.db.transaction import write_transaction

    c, job_a, job_b = two_workspaces
    # 同名本地文件：没有它只是「行被过滤」；有它，旧的本地回填会把名字
    # 以 storage=local 复活，同样破坏契约。
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "evil.json").write_text('{"local": true}', encoding="utf-8")
    key = f"jobs/ws-b/{job_b['id']}/evil.json"  # storage_key 指向别的 job
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    store.storage.objects[key] = b"{}"
    with write_transaction(job_db) as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'upstream', 'evil.json', %s, 2, '')",
            (job_a["id"], key),
        )

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    assert [e["name"] for e in listing["artifacts"]] == []
    status = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").json()
    assert status["artifacts"] == []
    # raw 语义不变（H1 兜底）：越界行 404，本地副本不接管。
    assert (
        c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/evil.json/raw").status_code == 404
    )

    # 最新行判定（lookup 同语义）：同名行先合法后越界（显式更晚的
    # uploaded_at），raw 端 lookup 取最新（越界）行必 404——清单也不得
    # 拿旧行复活该名字。
    _register_object_artifact(c, job_a, "stale.json", b'{"v": 1}')
    with write_transaction(job_db) as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash, uploaded_at)"
            " values (%s, 'evil_writer', 'stale.json', %s, 2, '', now() + interval '1 hour')",
            (job_a["id"], f"jobs/ws-b/{job_b['id']}/stale.json"),
        )

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    assert "stale.json" not in [e["name"] for e in listing["artifacts"]]
    status = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").json()
    assert "stale.json" not in status["artifacts"]
    assert (
        c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/stale.json/raw").status_code
        == 404
    )


# --- #631 攻击复审 M1/M2：下载侧名字白名单（与清单剪枝单一事实来源） --------


def test_raw_serves_runs_dir_file_only_via_manifest_row(two_workspaces):
    """M2 不对称收口：``runs/`` 是执行内部数据（events.jsonl），清单剪掉
    它；下载侧此前只做包含性校验、不认识 runs/——文件一旦落盘即可按名
    下载（清单不列但可达）。现在 ``_artifact_path`` 拒绝 runs/ 前缀段与
    点前缀段，与 ``artifact_names_deep`` 的剪枝规则同一份名单。"""
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    (storage / "runs" / "node_a" / "token1").mkdir(parents=True, exist_ok=True)
    (storage / "runs" / "node_a" / "token1" / "events.jsonl").write_text(
        '{"internal": "run-events"}', encoding="utf-8"
    )
    (storage / ".result-staging-x").mkdir(parents=True, exist_ok=True)
    (storage / ".result-staging-x" / "leak.txt").write_text("staged", encoding="utf-8")

    runs_read = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/runs/node_a/token1/events.jsonl/raw"
    )
    dot_read = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/.result-staging-x/leak.txt/raw"
    )

    assert runs_read.status_code == 400
    assert dot_read.status_code == 400


@pytest.mark.parametrize(
    "name",
    [
        "result.json%00",  # NUL：decode 后是控制字符
        "x" * 300,  # 超长段：ENAMETOOLONG 家族
        "y" * 201,  # 段上限（200 字节）刚过线
    ],
)
def test_raw_rejects_control_char_and_oversized_names(two_workspaces, name):
    """M1：畸形名字必须是 400，不是让 lstat/stat 炸 500（no-enumeration
    语义：500 vs 404 把「名字是否畸形」变成侧信道）。"""
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("{}", encoding="utf-8")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/{name}/raw")

    assert response.status_code == 400


def test_raw_null_byte_name_is_400_not_500(two_workspaces):
    """M1：``%00`` 解码后进 lstat 是 ValueError（embedded null）——修复前
    逃出端点成 500（TestClient 直接 raise），修复后名字白名单先拒。"""
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/result.json%00/raw")

    assert response.status_code == 400


def test_status_null_byte_job_id_is_400_not_500(two_workspaces):
    """M1：``%00`` 进 job_id 修复前让 psycopg 抛 DataError（500）；现在
    路由层早拒 400（status / artifacts / raw 三端点同门）。"""
    c, _, _ = two_workspaces

    status = c.get("/api/workspaces/ws-a/jobs/%00foo")
    listing = c.get("/api/workspaces/ws-a/jobs/%00foo/artifacts")
    raw = c.get("/api/workspaces/ws-a/jobs/%00foo/artifacts/x.json/raw")

    assert status.status_code == 400
    assert listing.status_code == 400
    assert raw.status_code == 400


# --- #631 攻击复审 H2：legacy 裸路由的 scoped 语义（#745 rebase 后） ----------


def test_bare_job_read_routes_scope_scoped_tokens_by_binding(two_workspaces, job_db):
    """H2 收口的 rebase 修正（#703 CI 失败）：裸路由（无 workspace 前缀）
    修复前对任意 scoped Bearer token 全开——绑定 ws-a 的 token 可读 ws-b
    的 job 详情、清单、raw 字节、日志与 token 用量，整体绕过 #631 的
    workspace 隔离。#631 曾以路由级 scoped-一律-404 收口；rebase #745 后
    job_group 的 require_job_workspace_access 按 job 行反查授权域，裸路
    由与前缀家族同一语义——绑定 token 读自己 workspace 的裸路由保持
    200（#745 IDOR 矩阵钉住的既有行为），跨 workspace 与未知 job 同为
    404（防枚举常量信号，与 #631 的收口强度一致）。"""
    from server.app.auth import scoped_tokens

    c, job_a, job_b = two_workspaces
    _register_object_artifact(c, job_a, "frame-a.png", b"\x89PNG-a")
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id="ws-a")
    scoped = c.__class__(c.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    # 绑定 ws-a 的 token 走裸路由读 ws-b 的产物字节：拒绝（404）。
    assert scoped.get(f"/api/jobs/{job_b['id']}").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/artifacts/frame.png/raw").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/artifacts/frame.json").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/runs/1/log").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/token-usage").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/runs/1/token-usage").status_code == 404
    # 不存在的 job 同样 404：常量信号，无探测差异。
    assert scoped.get("/api/jobs/nope").status_code == 404

    # 绑定 workspace 自己的 job：裸路由照常可读（#745 的既有行为——
    # 守卫是归属校验，不是 scoped 一刀切）。
    assert scoped.get(f"/api/jobs/{job_a['id']}").status_code == 200

    # 全会话用户不受影响（前端控制台在用的面）。
    assert c.get(f"/api/jobs/{job_a['id']}").status_code == 200

    # scoped 身份的 sanctioned 读面照常：绑定 ws-a 读 ws-a 前缀端点 200。
    assert scoped.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200


# --- #626/#631：workspace API token（actor_scope='api'）的外部读取面 ----------


def _issue_api_token(c, workspace_id: str, label: str = "external") -> str:
    created = c.post(f"/api/workspaces/{workspace_id}/api-tokens", json={"label": label})
    assert created.status_code == 201, created.text
    return str(created.json()["api_token"])


def _bearer(c, token: str):
    api = c.__class__(c.app)
    api.headers["authorization"] = f"Bearer {token}"
    return api


def test_workspace_api_token_reads_status_manifest_and_raw(two_workspaces):
    """#779 列车复审 P1-1：#626 的 workspace API token 的文档化面是
    submit → poll → download，但 #631 的三个外部读取端点挂在 job_group
    的 require_job_workspace_access 之下，api scope 白名单此前只列了
    runs 与 jobs 列表——三个已公开 GET 在到达 external_artifacts router
    前被统一 404。修复后白名单放行这三个 GET；raw 一条用子路径产物名
    （{artifact_name:path} 的多段匹配）钉住参数化部分的正确性。"""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "reports/final.json", b'{"final": true}')
    api = _bearer(c, _issue_api_token(c, "ws-a"))

    status = api.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}")
    assert status.status_code == 200
    assert status.json()["job_id"] == job_a["id"]

    listing = api.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts")
    assert listing.status_code == 200
    assert [e["name"] for e in listing.json()["artifacts"]] == ["reports/final.json"]

    raw = api.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/reports/final.json/raw")
    assert raw.status_code == 200
    assert raw.content == b'{"final": true}'

    # 未知 job：service 归属/存在性校验的 404（不是守卫误拦，也不是 5xx）。
    assert api.get("/api/workspaces/ws-a/jobs/missing").status_code == 404


def test_workspace_api_token_cross_workspace_reads_stay_404(two_workspaces):
    """放行不松绑定：绑定 ws-a 的 api token 读 ws-b 前缀的三个端点仍 404
    （绑定等值检查先于白名单放行）；ws-a 前缀借 ws-b 的 job id 同样 404
    （service 的归属校验，无枚举）。"""
    c, _, job_b = two_workspaces
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")
    api = _bearer(c, _issue_api_token(c, "ws-a"))

    assert api.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}").status_code == 404
    assert api.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}/artifacts").status_code == 404
    assert (
        api.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}/artifacts/frame.png/raw").status_code
        == 404
    )
    assert api.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}").status_code == 404
    assert api.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}/artifacts").status_code == 404


def test_workspace_api_token_write_methods_stay_refused(two_workspaces):
    """白名单只放行 GET：同一批路径上的写方法依旧不是 2xx（无路由 405，
    有守卫 403/404——写面零扩大）。"""
    c, job_a, _ = two_workspaces
    api = _bearer(c, _issue_api_token(c, "ws-a"))

    for method in ("POST", "PUT", "DELETE", "PATCH"):
        for suffix in ("", "/artifacts", "/artifacts/x.json/raw"):
            response = api.request(method, f"/api/workspaces/ws-a/jobs/{job_a['id']}{suffix}")
            assert response.status_code in (403, 404, 405), (
                f"{method} ...{suffix} -> {response.status_code}"
            )


def test_workspace_api_token_allowlist_matches_route_template_not_path_text(two_workspaces):
    """回归钉（/jobs/facets 冲突）：放行 ``/jobs/{job_id}`` 后，静态姊妹
    路由 ``/jobs/facets`` 不得被路径文本匹配误放行（job_id='facets' 的
    请求实际命中 facets 路由，它不在白名单）——白名单按解析出的路由模
    板精确匹配；真实 job 的状态读取照常 200（判别力对照）。"""
    c, job_a, _ = two_workspaces
    api = _bearer(c, _issue_api_token(c, "ws-a"))

    assert api.get("/api/workspaces/ws-a/jobs/facets").status_code == 404
    assert api.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200
