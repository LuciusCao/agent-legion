import pytest

from tests.helpers import publish_builtin_revision

WORKFLOW_KEY = "education_video_problems_generation"


def _make_workspace(job_db, slug):
    workspace = job_db.create_workspace(slug, default_workflow_key=WORKFLOW_KEY)
    publish_builtin_revision(job_db, workspace["id"])
    return workspace


def _make_job(job_db, workspace_id, source_id, title="", run_id="", node_keys=("n1", "n2")):
    return job_db.create_job(
        workspace_id=workspace_id,
        workflow_key=WORKFLOW_KEY,
        source_type="question_id",
        source_id=source_id,
        run_id=run_id,
        title=title,
        node_keys=list(node_keys),
    )


def _execute(job_db, sql, params=()):
    with job_db.connect() as conn:
        conn.execute(sql, params)


def _set_status(job_db, job_id, status):
    _execute(job_db, "update jobs set status = %s where id = %s", (status, job_id))


def _set_node_status(job_db, job_id, node_key, status):
    _execute(
        job_db,
        "update job_nodes set status = %s where job_id = %s and node_key = %s",
        (status, job_id, node_key),
    )


def _snapshot(client, workspace_id, query=""):
    response = client.get(f"/api/workspaces/{workspace_id}/jobs/snapshot{query}")
    assert response.status_code == 200
    return response.json()


def _facets(client, workspace_id, query=""):
    response = client.get(f"/api/workspaces/{workspace_id}/jobs/facets{query}")
    assert response.status_code == 200
    return response.json()


def test_status_filter_folds_unknown_statuses_into_pending(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-status-ws")
        queued = _make_job(job_db, workspace["id"], "q-queued")
        running = _make_job(job_db, workspace["id"], "q-running")
        unknown = _make_job(job_db, workspace["id"], "q-unknown")
        _set_status(job_db, running["id"], "running")
        _set_status(job_db, unknown["id"], "mystery_state")

        pending = _snapshot(client, workspace["id"], "?status=pending")
        assert pending["total"] == 2
        assert {job["id"] for job in pending["jobs"]} == {queued["id"], unknown["id"]}

        running_data = _snapshot(client, workspace["id"], "?status=running")
        assert running_data["total"] == 1
        assert running_data["jobs"][0]["id"] == running["id"]


def test_search_filter_matches_across_fields_case_insensitively(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-search-ws")
        by_title = _make_job(job_db, workspace["id"], "q-title", title="Algebra Question")
        by_source = _make_job(job_db, workspace["id"], "q-SourceId")
        by_batch = _make_job(job_db, workspace["id"], "q-batch", run_id="Batch-77")
        _make_job(job_db, workspace["id"], "q-other", title="Geometry")

        by_title_hit = _snapshot(client, workspace["id"], "?search=algebra")
        assert [job["id"] for job in by_title_hit["jobs"]] == [by_title["id"]]

        by_source_hit = _snapshot(client, workspace["id"], "?search=sourceid")
        assert [job["id"] for job in by_source_hit["jobs"]] == [by_source["id"]]

        by_batch_hit = _snapshot(client, workspace["id"], "?search=batch-77")
        assert [job["id"] for job in by_batch_hit["jobs"]] == [by_batch["id"]]

        by_id_hit = _snapshot(client, workspace["id"], "?search=q-other")
        assert len(by_id_hit["jobs"]) == 1

        trimmed = _snapshot(client, workspace["id"], "?search=  algebra  ")
        assert trimmed["total"] == 1


def test_search_filter_escapes_like_wildcards(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-escape-ws")
        percent = _make_job(job_db, workspace["id"], "q-percent", title="100% legit")
        _make_job(job_db, workspace["id"], "q-plain", title="1000x legit")

        data = _snapshot(client, workspace["id"], "?search=100%25")
        assert [job["id"] for job in data["jobs"]] == [percent["id"]]


def test_workflow_version_filters(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-version-ws")
        v1 = _make_job(job_db, workspace["id"], "q-v1")
        v2 = _make_job(job_db, workspace["id"], "q-v2")
        none_v = _make_job(job_db, workspace["id"], "q-vnone")
        _execute(job_db, "update jobs set workflow_version = 1 where id = %s", (v1["id"],))
        _execute(job_db, "update jobs set workflow_version = 2 where id = %s", (v2["id"],))
        _execute(job_db, "update jobs set workflow_version = null where id = %s", (none_v["id"],))

        by_version = _snapshot(client, workspace["id"], "?workflow_version=1")
        assert [job["id"] for job in by_version["jobs"]] == [v1["id"]]

        by_none = _snapshot(client, workspace["id"], "?workflow_version_none=true")
        assert [job["id"] for job in by_none["jobs"]] == [none_v["id"]]

        conflict = client.get(
            f"/api/workspaces/{workspace['id']}/jobs/snapshot"
            "?workflow_version=1&workflow_version_none=true"
        )
        assert conflict.status_code == 400


def test_active_node_key_prefers_running_then_first_failed(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-node-ws")
        failed_job = _make_job(job_db, workspace["id"], "q-failed")
        running_job = _make_job(job_db, workspace["id"], "q-running")
        # No running node: active node is the first failed node by id.
        _set_node_status(job_db, failed_job["id"], "n1", "failed")
        # A running node wins over an earlier failed node.
        _set_node_status(job_db, running_job["id"], "n1", "failed")
        _set_node_status(job_db, running_job["id"], "n2", "running")

        by_n1 = _snapshot(client, workspace["id"], "?active_node_key=n1")
        assert [job["id"] for job in by_n1["jobs"]] == [failed_job["id"]]

        by_n2 = _snapshot(client, workspace["id"], "?active_node_key=n2")
        assert [job["id"] for job in by_n2["jobs"]] == [running_job["id"]]


def test_packed_filter(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-packed-ws")
        packed = _make_job(job_db, workspace["id"], "q-packed")
        _make_job(job_db, workspace["id"], "q-unpacked")
        _execute(job_db, "update jobs set packed = 1 where id = %s", (packed["id"],))

        data = _snapshot(client, workspace["id"], "?packed=1")
        assert [job["id"] for job in data["jobs"]] == [packed["id"]]

        unpacked = _snapshot(client, workspace["id"], "?packed=0")
        assert unpacked["total"] == 1
        assert unpacked["jobs"][0]["id"] != packed["id"]


def test_filtered_pagination_returns_total_only_on_first_page(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-page-ws")
        created = []
        for i in range(5):
            job = _make_job(job_db, workspace["id"], f"q-page-{i}")
            created.append(job["id"])
        for job_id in created[:2]:
            _set_status(job_db, job_id, "running")

        first = _snapshot(client, workspace["id"], "?status=pending&limit=2")
        assert first["total"] == 3
        assert first["stats"] == {"pending": 3, "running": 2}
        assert len(first["jobs"]) == 2
        assert first["next_cursor"] is not None

        second = _snapshot(
            client, workspace["id"], f"?status=pending&limit=2&cursor={first['next_cursor']}"
        )
        assert second["total"] is None
        assert second["stats"] == {}
        assert len(second["jobs"]) == 1
        assert second["next_cursor"] is None

        ids = [job["id"] for job in first["jobs"] + second["jobs"]]
        assert sorted(ids) == sorted(created[2:])


def test_facets_exclude_own_dimension(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "facets-ws")
        job_a = _make_job(job_db, workspace["id"], "q-a", title="alpha one")
        job_b = _make_job(job_db, workspace["id"], "q-b", title="alpha two")
        _make_job(job_db, workspace["id"], "q-c", title="beta")
        _set_status(job_db, job_b["id"], "running")
        _set_node_status(job_db, job_a["id"], "n1", "failed")
        _set_node_status(job_db, job_b["id"], "n2", "running")
        _execute(job_db, "update jobs set workflow_version = 1 where id = %s", (job_a["id"],))

        data = _facets(client, workspace["id"], "?search=alpha&status=pending")
        # total applies every filter.
        assert data["total"] == 1
        # status_counts ignores the status filter but applies the search.
        assert data["status_counts"] == {"pending": 1, "running": 1}
        # version_counts applies both search and status filters.
        assert data["version_counts"] == {"1": 1}
        # node_counts applies both search and status filters.
        assert data["node_counts"] == {"n1": 1}


def test_facets_null_keys_and_unfiltered_counts(client_factory):
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "facets-null-ws")
        job_a = _make_job(job_db, workspace["id"], "q-a")
        _make_job(job_db, workspace["id"], "q-b")
        _set_node_status(job_db, job_a["id"], "n1", "failed")

        data = _facets(client, workspace["id"])
        assert data["total"] == 2
        assert data["status_counts"] == {"pending": 2}
        # Jobs without a workflow version are keyed "none".
        assert data["version_counts"] == {"none": 2}
        # Jobs without a running/failed node are keyed "".
        assert data["node_counts"] == {"n1": 1, "": 1}

        by_node = _facets(client, workspace["id"], "?active_node_key=n1")
        assert by_node["total"] == 1
        # node_counts ignores the active_node_key filter itself.
        assert by_node["node_counts"] == {"n1": 1, "": 1}


@pytest.mark.parametrize("endpoint", ["snapshot", "facets"])
def test_filtered_endpoints_reject_conflicting_version_params(client_factory, endpoint):
    with client_factory() as client:
        response = client.get(
            f"/api/workspaces/any/jobs/{endpoint}?workflow_version=1&workflow_version_none=true"
        )
    assert response.status_code == 400


@pytest.mark.parametrize("endpoint", ["snapshot", "facets"])
@pytest.mark.parametrize("param", ["status", "active_node_key", "run_id"])
def test_filtered_endpoints_reject_empty_string_filters(client_factory, endpoint, param):
    """#735 review P2 簇面清扫：snapshot/facets 与 jobs 列表同一约定——可选
    过滤参数的空串形态（`?status=` 等）是调用错误 → 422，绝不被查询层的
    `if val` 吞成「不过滤」。search/cursor 豁免：空串对它们是恒等 no-op
    （匹配一切 / 第一页），不是静默放宽的过滤。"""
    with client_factory() as client:
        rejected = client.get(f"/api/workspaces/any/jobs/{endpoint}?{param}=")
        assert rejected.status_code == 422, (endpoint, param, rejected.text)
        # 豁免面对照：空 search / 空 cursor 仍是恒等 no-op（400 家族之外的
        # 非 422——workspace 不存在时由业务层决定 404/200，这里只钉「不 422」）。
        for exempt in ("search", "cursor") if endpoint == "snapshot" else ("search",):
            allowed = client.get(f"/api/workspaces/any/jobs/{endpoint}?{exempt}=")
            assert allowed.status_code != 422, (endpoint, exempt, allowed.text)


@pytest.mark.parametrize(("limit", "expected"), [(0, 422), (501, 422), (1, 200), (500, 200)])
def test_snapshot_limit_out_of_range_is_422(client_factory, limit, expected):
    """#852：snapshot 的 limit 与 /runs、/jobs 同一约定——越界 422，不在函数
    体内静默钳制后照常 200（调用方会误以为拿到了请求的页大小）。"""
    with client_factory() as client:
        workspace = _make_workspace(client.app.state.job_db, f"snapshot-limit-{limit}-ws")
        response = client.get(f"/api/workspaces/{workspace['id']}/jobs/snapshot?limit={limit}")
    assert response.status_code == expected, response.text


@pytest.mark.parametrize(
    "cursor",
    [
        "garbage",
        "notadate|x",
        "2026-13-01 00:00:00|job-1",
        "2026-10-05 00:00:00|",
        "|job-1",
        # fromisoformat 接受任意单字符分隔符，PostgreSQL 不接受（#974 review）
        "2026-10-05\U0001f40d00:00:00|job-1",
        "2026-10-05x00:00:00|job-1",
        # job_id 半段的控制字符：NUL 绑定进 SQL 会让 psycopg 抛 DataError（#974 R3）
        "2026-10-05 00:00:00|job\x00x",
        "2026-10-05 00:00:00|job\x1fx",
    ],
)
def test_snapshot_malformed_cursor_is_422(client_factory, cursor):
    """#891：cursor 解析失败与 limit 越界同一约定——422 + 可读 detail，不再
    落到 SQL 抛未处理异常成 5xx（错误码表让调用方对 5xx 退避重试）。"""
    with client_factory() as client:
        workspace = _make_workspace(client.app.state.job_db, "snapshot-cursor-ws")
        response = client.get(
            f"/api/workspaces/{workspace['id']}/jobs/snapshot", params={"cursor": cursor}
        )
    assert response.status_code == 422, response.text
    [error] = response.json()["detail"]
    assert error["loc"] == ["query", "cursor"]
    assert "cursor" in error["msg"]


def test_snapshot_cursor_binds_parsed_utc_timestamp(client_factory):
    """#974 review：SQL 绑定的是解析后的 datetime（naive 视为 UTC，与 next_cursor
    生成形态一致），不是原字符串——合法 next_cursor 及其 `T` / `+00:00` 等价写法
    翻到同一页。"""
    with client_factory() as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "snapshot-cursor-bind-ws")
        for i in range(3):
            _make_job(job_db, workspace["id"], f"q-bind-{i}")
        first = _snapshot(client, workspace["id"], "?limit=1")
        cursor = first["next_cursor"]
        stamp, _, job_id = cursor.partition("|")
        variants = [cursor, f"{stamp.replace(' ', 'T')}|{job_id}", f"{stamp}+00:00|{job_id}"]
        pages = [
            client.get(
                f"/api/workspaces/{workspace['id']}/jobs/snapshot",
                params={"limit": 1, "cursor": variant},
            )
            for variant in variants
        ]
    assert all(page.status_code == 200 for page in pages), [p.text for p in pages]
    ids = [[job["id"] for job in page.json()["jobs"]] for page in pages]
    assert ids[0] and ids[0] != [first["jobs"][0]["id"]]
    assert ids == [ids[0]] * len(ids)
