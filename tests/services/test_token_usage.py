import json
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_database
from server.app.db.schema import init_db
from server.app.services.token_usage import (
    calculate_cost,
    load_pricing_config,
    persist_node_run_usage,
)
from server.app.services.token_usage_parse import TokenUsageSummary, parse_run_usage
from tests.postgres_support import TEST_DATABASE_URL


def _write_events(run_dir: Path, events: list[dict]) -> None:
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def test_parse_sums_message_end_usage(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"model": {"provider": "gateway", "model": "your-model-a"}})
    )
    events = [
        {"type": "message_start"},
        {
            "type": "message_end",
            "message": {"usage": {"input": 100, "output": 50, "cacheRead": 10}},
        },
        {
            "type": "message_end",
            "message": {"usage": {"input": 200, "output": 100, "cacheRead": 20}},
        },
    ]
    _write_events(run_dir, events)
    summary = parse_run_usage(run_dir, {"skill_version": "v1"})
    assert summary is not None
    assert summary.input_tokens == 300
    assert summary.output_tokens == 150
    assert summary.cache_read_tokens == 30
    assert summary.total_tokens == 480
    assert summary.message_count == 2
    assert summary.provider == "gateway"
    assert summary.model == "your-model-a"
    assert summary.skill_version == "v1"


def test_parse_returns_none_when_events_missing(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert parse_run_usage(run_dir, {"id": 1}) is None


def test_parse_falls_back_to_event_provider_and_model(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events = [
        {
            "type": "message_end",
            "message": {
                "provider": "event-provider",
                "model": "event-model",
                "usage": {"input": 10, "output": 5, "cacheRead": 1},
            },
        },
    ]
    _write_events(run_dir, events)
    summary = parse_run_usage(run_dir, {"id": 1})
    assert summary is not None
    assert summary.provider == "event-provider"
    assert summary.model == "event-model"


def test_parse_run_json_takes_precedence_over_event_provider_model(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"model": {"provider": "gateway", "model": "your-model-a"}})
    )
    events = [
        {
            "type": "message_end",
            "message": {
                "provider": "event-provider",
                "model": "event-model",
                "usage": {"input": 10, "output": 5, "cacheRead": 1},
            },
        },
    ]
    _write_events(run_dir, events)
    summary = parse_run_usage(run_dir, {"id": 1})
    assert summary is not None
    assert summary.provider == "gateway"
    assert summary.model == "your-model-a"


def test_parse_latest_event_provider_model_wins_when_run_json_empty(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({}))
    events = [
        {
            "type": "message_end",
            "message": {
                "provider": "first-provider",
                "model": "first-model",
                "usage": {"input": 10, "output": 5, "cacheRead": 1},
            },
        },
        {
            "type": "message_end",
            "message": {
                "provider": "latest-provider",
                "model": "latest-model",
                "usage": {"input": 20, "output": 10, "cacheRead": 2},
            },
        },
    ]
    _write_events(run_dir, events)
    summary = parse_run_usage(run_dir, {"id": 1})
    assert summary is not None
    assert summary.provider == "latest-provider"
    assert summary.model == "latest-model"
    assert summary.input_tokens == 30
    assert summary.message_count == 2


def test_parse_skill_version_falls_back_to_run_json(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"skill_version": "run-json-version"}))
    events = [
        {"type": "message_end", "message": {"usage": {"input": 10, "output": 5, "cacheRead": 1}}},
    ]
    _write_events(run_dir, events)
    summary = parse_run_usage(run_dir, {"id": 1})
    assert summary is not None
    assert summary.skill_version == "run-json-version"


def test_parse_node_run_skill_version_takes_precedence(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"skill_version": "run-json-version"}))
    events = [
        {"type": "message_end", "message": {"usage": {"input": 10, "output": 5, "cacheRead": 1}}},
    ]
    _write_events(run_dir, events)
    summary = parse_run_usage(run_dir, {"id": 1, "skill_version": "node-version"})
    assert summary is not None
    assert summary.skill_version == "node-version"


def test_parse_ignores_malformed_lines_and_marks_incomplete(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "message_end",
                "message": {"usage": {"input": 10, "output": 5, "cacheRead": 1}},
            }
        )
        + "\n{not valid json}\n"
        + json.dumps(
            {
                "type": "message_end",
                "message": {"usage": {"input": 20, "output": 10, "cacheRead": 2}},
            }
        ),
        encoding="utf-8",
    )
    summary = parse_run_usage(run_dir, {"id": 1})
    assert summary is not None
    assert summary.input_tokens == 30
    assert summary.message_count == 2
    assert summary.is_complete is False
    assert summary.parse_error != ""


def test_parse_returns_none_when_only_malformed_lines(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text("{not valid json}\n{more bad json}", encoding="utf-8")
    assert parse_run_usage(run_dir, {"id": 1}) is None


def test_persist_node_run_usage_creates_row(tmp_path):
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    summary = TokenUsageSummary(
        node_run_id=1,
        job_id="job-1",
        workspace_id="ws-1",
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        message_count=2,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        total_tokens=160,
    )
    with closing(connect_database(db_path)) as conn:
        # Insert required parent rows.
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict (id) do nothing",
            ("ws-1", "Test"),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values (%s, %s, %s, %s, %s) on conflict (id) do nothing",
            ("job-1", "ws-1", "wf", "source", "id"),
        )
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status) values (%s, %s, %s, %s)"
            " on conflict (id) do nothing",
            (1, "job-1", "node-a", "completed"),
        )
        persist_node_run_usage(conn, summary)
        row = conn.execute(
            "select * from node_run_token_usage where node_run_id=%s", (1,)
        ).fetchone()
        assert row is not None
        assert row["input_tokens"] == 100
        assert row["is_complete"] == 1


def test_load_pricing_config_flattens_by_provider_model():
    config = {
        "token_usage": {
            "currency": "CNY",
            "pricing": [
                {
                    "provider": "gateway",
                    "model": "your-model-a",
                    "input_per_1m": 3.0,
                    "output_per_1m": 15.0,
                    "cache_read_per_1m": 0.6,
                }
            ],
        }
    }
    pricing = load_pricing_config(config)
    assert pricing == {
        ("gateway", "your-model-a"): {
            "input_per_1m": 3.0,
            "output_per_1m": 15.0,
            "cache_read_per_1m": 0.6,
        }
    }


def test_cost_for_known_model():
    config = {
        "token_usage": {
            "currency": "CNY",
            "pricing": [
                {
                    "provider": "gateway",
                    "model": "your-model-a",
                    "input_per_1m": 3.0,
                    "output_per_1m": 15.0,
                    "cache_read_per_1m": 0.6,
                }
            ],
        }
    }
    cost = calculate_cost(1000000, 500000, 300000, 200000, "gateway", "your-model-a", config)
    assert cost.pricing_missing is False
    assert cost.total == pytest.approx(1.5 + 4.5 + 0.12)
    assert cost.currency == "CNY"


def test_cost_for_unknown_model():
    config = {"token_usage": {"currency": "CNY", "pricing": []}}
    cost = calculate_cost(100, 50, 30, 20, "unknown", "model", config)
    assert cost is None
