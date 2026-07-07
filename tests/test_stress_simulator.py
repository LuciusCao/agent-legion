from pathlib import Path

import pytest

# Importing the script module requires adding the scripts directory to sys.path.
pytest.importorskip("scripts.stress.simulate_agents")

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "stress"))

from simulate_agents import (
    StressMetrics,
    StressSimulator,
    _parse_args,
    _stress_workflow_definition,
)


def test_stress_metrics_summary_computes_percentiles():
    metrics = StressMetrics()
    metrics.patch_batch_sizes = [1, 2, 3, 4, 5]
    metrics.flush_latencies_ms = [10.0, 20.0, 30.0, 40.0, 50.0]
    metrics.stats_query_latencies_ms = [5.0, 15.0, 25.0]

    summary = metrics.summary()

    assert summary["patch_batch_size_p50"] == 3
    assert summary["patch_batch_size_p95"] == 5
    assert summary["flush_latency_p50_ms"] == 30.0
    assert summary["flush_latency_p95_ms"] == 50.0


def test_parse_args_uses_defaults():
    args = _parse_args([])

    assert args.workspace == "ws-stress"
    assert args.agents == 100
    assert args.jobs == 5000
    assert args.event_rate == 500
    assert args.duration == 600
    assert args.base_url is None


def test_stress_workflow_definition_has_nodes():
    definition = _stress_workflow_definition()

    assert definition.key == "stress_concurrency"
    assert set(definition.nodes.keys()) == {"step_1", "step_2", "step_3"}


def test_stress_simulator_builds_event_pipeline(tmp_path):
    simulator = StressSimulator(
        workspace_id="ws-smoke",
        agents=2,
        jobs=2,
        event_rate=10,
        duration=1,
        base_url=None,
        results_dir=tmp_path,
    )

    buffer, aggregator = simulator._build_event_pipeline()

    assert buffer is not None
    assert aggregator is not None
