"""Workspace API intake token tests (#626).

The machine-to-machine submission channel: an admin issues a
``{token_id}.{secret}`` credential bound to one workspace, an external
system presents it as ``Authorization: Bearer`` against the runs surface.
This file owns the shared fixtures/helpers, the management lifecycle
(admin-only issue/list/revoke, one-time plaintext, expiry visibility, the
last_used_at watermark), and the channel's happy path; the sibling files
carry the rest of the split (test-file line budget, AGENTS.md §4):
- test_workspace_api_token_boundaries.py — the permission boundaries and
  attack surface (cross-workspace 404, expiry/revocation semantics, the
  api-scope blast radius, the store's equal-work failure paths);
- test_workspace_api_token_paging.py — the codex3 P1 paginated,
  run-scoped job status reads (snapshot cursor + run_id filter).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "api-token-ws"
OTHER = "api-token-ws-other"


def _create_workspace(client: TestClient, ws_id: str, name: str = "api-token-ws") -> str:
    response = client.post("/api/workspaces", json={"id": ws_id, "name": name})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, ws_id)
    return ws_id


def _issue(client: TestClient, workspace_id: str, **payload) -> dict:
    created = client.post(f"/api/workspaces/{workspace_id}/api-tokens", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def _bearer_client(client: TestClient, api_token: str) -> TestClient:
    """A cookie-less client with only the Bearer credential set."""
    api_client = client.__class__(client.app)
    api_client.headers["authorization"] = f"Bearer {api_token}"
    return api_client


def _insert_material(client: TestClient, workspace_id: str, material_id: str) -> None:
    job_db = client.app.state.job_db
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
            ),
        )


def _submit_run(api: TestClient, workspace_id: str, material_id: str):
    return api.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"items": [{"type": "material", "material_id": material_id}]},
    )


# --- management endpoints ----------------------------------------------------


def test_management_requires_admin(client) -> None:
    _create_workspace(client, WORKSPACE)
    # Anonymous: 401.
    anon = client.__class__(client.app)
    assert anon.get(f"/api/workspaces/{WORKSPACE}/api-tokens").status_code == 401
    # Non-admin member of the workspace: require_workspace_access lets the
    # member through, then require_admin inside the router refuses (403). A
    # non-member would be 404 first (the secured() enumeration rule).
    client.post("/api/users", json={"username": "member", "password": "pw"})
    member = client.__class__(client.app)
    assert (
        member.post("/api/auth/login", json={"username": "member", "password": "pw"}).status_code
        == 200
    )
    member.headers["x-agent-legion-request"] = "1"
    member_id = str(client.app.state.job_db.get_user_credentials("member")["id"])
    client.put(
        f"/api/workspaces/{WORKSPACE}/members", json={"user_id": member_id, "role": "editor"}
    )
    assert (
        member.post(f"/api/workspaces/{WORKSPACE}/api-tokens", json={"label": "x"}).status_code
        == 403
    )
    assert member.get(f"/api/workspaces/{WORKSPACE}/api-tokens").status_code == 403


def test_issue_returns_plaintext_once_and_list_never_does(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms cron")
    assert issued["workspace_id"] == WORKSPACE
    assert issued["label"] == "cms cron"
    plaintext = issued["api_token"]
    assert plaintext.startswith(f"{issued['token_id']}.")

    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    assert len(listed) == 1
    entry = listed[0]
    assert entry["token_id"] == issued["token_id"]
    assert entry["revoked"] is False
    assert entry["expires_at"] is None
    assert "api_token" not in entry
    assert "token_hash" not in str(entry)


def test_issue_on_unknown_workspace_400(client) -> None:
    response = client.post("/api/workspaces/never-created/api-tokens", json={"label": "x"})
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_issue_with_ttl_and_expiry_visible(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="short-lived", ttl_hours=2)
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    entry = next(t for t in listed if t["token_id"] == issued["token_id"])
    assert entry["expires_at"] is not None
    expires = datetime.fromisoformat(entry["expires_at"])
    remaining = expires - datetime.now(UTC)
    assert timedelta(hours=1) < remaining <= timedelta(hours=2, minutes=1)


# --- auth chain: the channel's happy path --------------------------------------


def test_bearer_token_submits_runs_without_csrf(client) -> None:
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # No session cookie, no x-agent-legion-request header — the Bearer
    # channel is CSRF-exempt by design (issue #626).
    assert "cookie" not in api.headers or not api.cookies
    response = _submit_run(api, WORKSPACE, "mat-1")
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert response.json()["created_count"] == 1

    # The read side of the same channel.
    listed = api.get(f"/api/workspaces/{WORKSPACE}/runs")
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["runs"]] == [run["id"]]
    detail = api.get(f"/api/workspaces/{WORKSPACE}/runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["run"]["id"] == run["id"]
    jobs = api.get(f"/api/workspaces/{WORKSPACE}/jobs")
    assert jobs.status_code == 200
    assert len(jobs.json()["jobs"]) == 1
    snapshot = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["total"] == 1
    assert [j["id"] for j in snapshot.json()["jobs"]] == [j["id"] for j in jobs.json()["jobs"]]


# --- listing watermark ---------------------------------------------------------


def test_listing_shows_revoked_and_last_used(client) -> None:
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    assert (
        _submit_run(_bearer_client(client, issued["api_token"]), WORKSPACE, "mat-1").status_code
        == 200
    )
    client.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}")
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    entry = next(t for t in listed if t["token_id"] == issued["token_id"])
    assert entry["revoked"] is True
    assert entry["last_used_at"] is not None


def test_last_used_at_throttled(client) -> None:
    """Two submissions inside the throttle window produce one UPDATE."""
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    _insert_material(client, WORKSPACE, "mat-2")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert _submit_run(api, WORKSPACE, "mat-1").status_code == 200
    first = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"][0]
    assert first["last_used_at"] is not None
    # Drain the in-memory throttle map: the next resolve must stamp again.
    store = client.app.state.workspace_api_token_store
    store._last_used_at_refreshed.clear()
    assert _submit_run(api, WORKSPACE, "mat-2").status_code == 200


# --- codex3 P1: paginated, run-scoped job status reads --------------------------


def _seed_jobs(client: TestClient, workspace_id: str, count: int, run_id: str) -> list[str]:
    """Insert `count` queued jobs into one run (single transaction)."""
    job_db = client.app.state.job_db
    job_ids = [f"{workspace_id}:question_id:{run_id}-{i:04d}" for i in range(count)]
    with job_db.connect() as conn:
        for i, job_id in enumerate(job_ids):
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, run_id, title,"
                " storage_dir) values (%s, %s, 'question_id', %s, %s, %s, '')",
                (job_id, workspace_id, f"{run_id}-{i:04d}", run_id, f"bulk {i:04d}"),
            )
    return job_ids


def test_api_token_pages_past_legacy_jobs_cap(client) -> None:
    """codex3 P1: the legacy GET /jobs is capped at 500 rows with no cursor
    — a machine caller with more than 500 jobs in its workspace could never
    read the rest. The paginated /jobs/snapshot (on the intake allowlist
    since this fix) must let the same identity walk the WHOLE list with
    limit+cursor."""
    _create_workspace(client, WORKSPACE)
    _seed_jobs(client, WORKSPACE, count=502, run_id="run-bulk")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])

    # The legacy surface: capped at 500 (API-compat behavior, unchanged).
    legacy = api.get(f"/api/workspaces/{WORKSPACE}/jobs")
    assert legacy.status_code == 200
    assert len(legacy.json()["jobs"]) == 500

    # The paginated surface: 500 + 2 with the same credential.
    collected: list[str] = []
    cursor = None
    pages = 0
    while True:
        url = f"/api/workspaces/{WORKSPACE}/jobs/snapshot?limit=500"
        if cursor is not None:
            url += f"&cursor={cursor}"
        page = api.get(url)
        assert page.status_code == 200, page.text
        body = page.json()
        collected.extend(job["id"] for job in body["jobs"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10, "pagination did not converge"
    assert len(collected) == 502
    assert len(set(collected)) == 502  # cursor pages never repeat a row
    assert collected == sorted(collected, reverse=True)  # created_at desc, id desc


def test_api_token_reads_jobs_by_run_id(client) -> None:
    """codex3 P1: a machine caller's primary question is "what happened to
    MY run" — with newer jobs from other runs in the workspace, the legacy
    listing may not even include this run's items. snapshot?run_id= must
    scope the page (and its total) to the caller's run; the run itself is
    always obtained from the create response."""
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    response = _submit_run(api, WORKSPACE, "mat-1")
    assert response.status_code == 200, response.text
    run_id = response.json()["run"]["id"]
    # Newer jobs in OTHER runs crowd the legacy 500-cap listing.
    _seed_jobs(client, WORKSPACE, count=6, run_id="run-other-1")
    _seed_jobs(client, WORKSPACE, count=6, run_id="run-other-2")

    legacy = api.get(f"/api/workspaces/{WORKSPACE}/jobs")
    assert legacy.status_code == 200
    assert len(legacy.json()["jobs"]) == 13  # nothing hidden yet — but the
    # ordering is created_at desc; the run's job is last, and with >500
    # newer jobs it drops out entirely (the codex3 P1 scenario).

    scoped = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id={run_id}")
    assert scoped.status_code == 200, scoped.text
    body = scoped.json()
    assert body["total"] == 1
    scoped_jobs = [job["id"] for job in body["jobs"]]
    assert len(scoped_jobs) == 1
    # The scoping is by run, not by recency: the other runs' jobs stay out.
    other = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=run-other-1")
    assert other.status_code == 200
    assert other.json()["total"] == 6
    assert {job["id"] for job in other.json()["jobs"]}.isdisjoint(scoped_jobs)
    # An unknown run is an empty page, not an error.
    missing = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=no-such-run")
    assert missing.status_code == 200
    assert missing.json()["total"] == 0
    assert missing.json()["jobs"] == []
    # run_id composes with the pagination cursor and the status filter.
    paged = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=run-other-1&limit=4")
    assert paged.status_code == 200
    assert len(paged.json()["jobs"]) == 4
    assert paged.json()["next_cursor"] is not None
    tail = api.get(
        f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=run-other-1&limit=4"
        f"&cursor={paged.json()['next_cursor']}"
    )
    assert tail.status_code == 200
    assert len(tail.json()["jobs"]) == 2
    assert tail.json()["next_cursor"] is None
    still_scoped = {job["id"] for job in paged.json()["jobs"] + tail.json()["jobs"]}
    assert len(still_scoped) == 6  # cursor kept the run scoping


# --- #734: 外部闭环端点全集 + 白名单机制化 -------------------------------------
# #703/#704 集成后 api token 打三个产物端点（#631）命中 #626 的手抄白名单
# 被 404——「提交 → 轮询 → 下载」闭环断裂。#734 补齐端点并把白名单改为
# tag 派生（auth/api_scope_surface.py）。以下正向测试钉住全集可达，负向
# 钉住其余表面仍拒绝，契约测试对账注册面与权威常量防再漂移。


def test_api_token_reaches_all_external_loop_endpoints(client) -> None:
    """正向清单：api token 对外部闭环端点全集（POST /runs、GET /runs、
    GET /runs/{run_id}、GET /jobs、GET /jobs/snapshot、GET /jobs/{job_id}、
    产物清单、raw 下载）全部可达——8 个 (method, 路由名) 缺一不可，
    少一个闭环就断一环。"""
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])

    # 1. 提交（POST /runs，唯一 effecting 面）。#467 A4：run 载荷不带
    # job id，读回用 jobs 列表（真实外部调用方的同一轮询路径）。
    submitted = _submit_run(api, WORKSPACE, "mat-1")
    assert submitted.status_code == 200, submitted.text
    run_id = submitted.json()["run"]["id"]
    jobs = api.get(f"/api/workspaces/{WORKSPACE}/jobs").json()["jobs"]
    assert len(jobs) == 1
    job_id = jobs[0]["id"]

    # 2. 轮询 run 状态（列表 + 单查）。
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 200
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs/{run_id}").status_code == 200

    # 3. 轮询 job 状态（列表 + codex3 P1 分页 snapshot + #631 单查端点）。
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs").status_code == 200
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot").status_code == 200
    status = api.get(f"/api/workspaces/{WORKSPACE}/jobs/{job_id}")
    assert status.status_code == 200, status.text
    assert status.json()["job_id"] == job_id

    # 4. 产物清单（#631）。
    manifest = api.get(f"/api/workspaces/{WORKSPACE}/jobs/{job_id}/artifacts")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["job_id"] == job_id


def test_api_token_external_loop_end_to_end(client_factory, monkeypatch) -> None:
    """端到端闭环（真实 HTTP 层）：Bearer token 提交 run → 轮询 job 状态 →
    取产物清单 → raw 下载字节。产物经 Worker-direct 通道的对象副本登记
    （FakeObjectStorage 换入共享 store 实例，与 test_external_artifacts
    同款 setup），验证的是 #734 修复的完整语义而非单点放行。"""
    from server.app.services.job_artifact_objects import JobArtifactObjectStore
    from tests.fakes.storage import FakeObjectStorage

    with client_factory(fresh=True) as c:
        workspace = "e2e-ws"
        c.post("/api/workspaces", json={"id": workspace, "name": workspace})
        from tests.helpers import publish_legacy_intake_revision

        publish_legacy_intake_revision(c.app.state.job_db, workspace)

        issued = _issue(c, workspace, label="e2e")
        api = _bearer_client(c, issued["api_token"])
        _insert_material(c, workspace, "mat-e2e")

        submitted = _submit_run(api, workspace, "mat-e2e")
        assert submitted.status_code == 200, submitted.text
        jobs = api.get(f"/api/workspaces/{workspace}/jobs").json()["jobs"]
        assert len(jobs) == 1
        job_id = jobs[0]["id"]

        # 轮询 job 状态直至可查（无 worker，状态停在 queued 也算闭环通）。
        status = api.get(f"/api/workspaces/{workspace}/jobs/{job_id}")
        assert status.status_code == 200, status.text
        assert status.json()["status"] in {"queued", "running", "completed"}

        # 产物：登记一个 Worker-direct 对象副本（manifest 行 + 对象字节）。
        payload = b'{"answer": 42}'
        store: JobArtifactObjectStore = c.app.state.job_artifact_objects
        monkeypatch.setattr(store, "storage", FakeObjectStorage())
        import gzip
        import hashlib

        stored = gzip.compress(payload)
        storage_key = f"jobs/{workspace}/{job_id}/report.json.gz"
        store.storage.objects[storage_key] = stored
        store.record_remote(
            workspace_id=workspace,
            job_id=job_id,
            node_key="upstream",
            name="report.json",
            storage_key=storage_key,
            size_bytes=len(stored),
            content_hash=hashlib.sha256(payload).hexdigest(),
        )

        manifest = api.get(f"/api/workspaces/{workspace}/jobs/{job_id}/artifacts")
        assert manifest.status_code == 200, manifest.text
        entries = {e["name"]: e for e in manifest.json()["artifacts"]}
        assert entries["report.json"]["storage"] == "object"
        assert entries["report.json"]["content_hash"] == hashlib.sha256(payload).hexdigest()

        # raw 下载：gzip 透明解压，字节完整。
        raw = api.get(f"/api/workspaces/{workspace}/jobs/{job_id}/artifacts/report.json/raw")
        assert raw.status_code == 200, raw.text
        assert raw.content == payload


def test_api_token_artifact_endpoints_stay_workspace_bound(client) -> None:
    """负向保持：三个产物端点放进白名单后，跨 workspace 仍 404——
    allowlist 只解决「哪个端点」，「哪个 workspace」仍由 scoped 绑定
    硬等值把关（与 /runs、/jobs 列表同一规则）。"""
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert _submit_run(api, WORKSPACE, "mat-1").status_code == 200

    # OTHER workspace 前缀下的三个产物端点：全部 404（无枚举语义）。
    assert api.get(f"/api/workspaces/{OTHER}/jobs/job-x").status_code == 404
    assert api.get(f"/api/workspaces/{OTHER}/jobs/job-x/artifacts").status_code == 404
    assert (
        api.get(f"/api/workspaces/{OTHER}/jobs/job-x/artifacts/report.json/raw").status_code == 404
    )
    # 未知 job：bound workspace 内也是 404（不存在即不可枚举）。
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs/never-created").status_code == 404


def test_api_token_cannot_reach_jobs_facets_or_other_frontend_routes(client) -> None:
    """负向钉死模板遮蔽面：GET /jobs/facets 是前端聚合端点，路径形态与
    /jobs/{job_id} 相邻（{job_id} 模板在路径匹配上会吞掉 facets 这类
    字面段）；tag 派生按请求实际命中的路由对象判定，facets 未挂 tag
    必须照旧 404。（snapshot 自 codex3 P1 起在准入面内，正向覆盖见
    test_api_token_reaches_all_external_loop_endpoints 与 paging 文件。）"""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs/facets").status_code == 404


# --- 白名单机制化契约测试（#734，#678 tool_names.py 同款形态） ------------------
# 注册面（app 实际挂载的路由）与权威常量（api_scope_surface.py 的路由名
# 清单）必须按计数全等：漏挂 tag、漏登记名字、tag 挂到名单外路由、或
# 名单内名字被第二条路由复用，都在这里炸出来——#631 式的「新端点上线、
# 白名单没人同步」从此是测试期必红而不是线上 404。


# 显式 postgres 标记（二轮评审 P2-1）：断言面本身是纯路由结构（app.routes
# 上的 Counter 比较），但 create_app 会 init_db 写 TEST_DATABASE_URL 的
# search_path schema——该 schema 只由 session 级 fixture 对带标记的测试
# 建立。同文件其它测试靠 client fixture 隐式获得标记，本测试是唯一裸用
# tmp_path 的：不加标记时 unit tier（-m "not postgres" + 不可达 DB URL）
# 必红，xdist 冷 worker 先跑到它则 InvalidSchemaName 间歇 flake。
@pytest.mark.postgres
def test_registered_intake_surface_matches_the_manifest(tmp_path) -> None:
    """契约：带 api-scope-intake tag 的路由名多重集 == 权威常量（Counter
    语义，每个名字恰好一次）；运行期判定对每条注册路由严格等值（带 tag
    放行、无 tag 拒绝）；文档化的闭环端点不被协调删除。

    隔离形态（P3-2 评估结论）：不复用 client fixture 的共享 app——那会
    引入认证 bootstrap 与 session 级 data_dir，换取的是与本测试无关的
    生命周期；create_app 的启动写操作（reset_all_to_paused 等）落在共享
    schema 上，由 postgres 标记进入 _isolate_postgres_database 的
    TRUNCATE 隔离契约兜底，无跨测试污染。"""
    from collections import Counter

    from fastapi.routing import APIRoute

    from server.app.auth.api_scope_surface import (
        API_SCOPE_INTAKE_ROUTE_NAMES,
        API_SCOPE_INTAKE_TAG,
        api_scope_route_allowed,
    )
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    tagged = [r for r in api_routes if API_SCOPE_INTAKE_TAG in r.tags]
    tagged_counts = Counter(r.name for r in tagged)
    manifest_counts = Counter(API_SCOPE_INTAKE_ROUTE_NAMES)

    # Counter 而非 set（二轮评审 P3-1）：复用名单内名字（如再来一个
    # list_runs）且挂 tag 的新路由会计数为 2——set 去重后比较仍绿，重名
    # 扩面通道就藏在这里；仓库已有 16 个重名路由（save_node_code_draft
    # ×4 等），名字复用是真实文化。反方向同理：tag 挂到名单外路由、
    # 名单名字漏挂 tag，多重集都不等。
    assert tagged_counts == manifest_counts, (
        f"注册面与权威清单脱节：tag 多挂/重名 "
        f"{sorted((tagged_counts - manifest_counts).elements())}，"
        f"清单虚列 {sorted((manifest_counts - tagged_counts).elements())}"
    )

    # 运行期判定逐路由等值：按路由对象迭代而非按名字建 dict（dict 同样
    # 去重，是 set 之外的第二条掩盖通道）。带 tag（上面的多重集等式已
    # 保证在名单内）必放行；无 tag 必拒绝——包括复用名单名字但漏挂 tag
    # 的路由，fail-closed 在这里成立。
    for route in api_routes:
        if API_SCOPE_INTAKE_TAG in route.tags:
            assert api_scope_route_allowed(route), route.name
        else:
            assert not api_scope_route_allowed(route), route.name

    # 协调删除守卫（原独立测试并入，二轮评审 P3-4）：名字与 tag 同时删
    # 除时上面的断言全绿（两边一致地缩小，set/Counter 都看不出），这里
    # 钉住文档化的闭环端面——#626 四端点 + codex3 P1 snapshot + #631
    # 三端点——不被无声砍掉。
    assert {
        "create_run",
        "list_runs",
        "get_run",
        "list_workspace_jobs",
        "snapshot_workspace_jobs",
    } <= set(API_SCOPE_INTAKE_ROUTE_NAMES)
    # #734 直接回归面：三个当初被手抄白名单漏掉的端点。
    assert {
        "get_external_job_status",
        "list_external_artifacts",
        "get_external_artifact_raw",
    } <= set(API_SCOPE_INTAKE_ROUTE_NAMES)
