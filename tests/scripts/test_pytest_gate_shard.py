"""Contract tests for scripts/pytest_gate_shard.py (Phase 5C-2 hash sharding)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.pytest_gate_shard import (
    GateShardFilter,
    parse_shard_spec,
    pytest_configure,
    shard_index,
)

pytestmark = pytest.mark.no_db

_NODEIDS = [f"tests/mod{i}/test_thing.py::test_case_{i}" for i in range(200)]


def test_parse_shard_spec_accepts_valid_values() -> None:
    assert parse_shard_spec("1/2") == (1, 2)
    assert parse_shard_spec("2/2") == (2, 2)
    assert parse_shard_spec("3/4") == (3, 4)


@pytest.mark.parametrize("value", ["3/2", "0/2", "1/0", "abc", "1/2/3", "", "1.5/2"])
def test_parse_shard_spec_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="GATE_SHARD"):
        parse_shard_spec(value)


def test_shards_partition_nodeids_without_overlap() -> None:
    for count in (2, 3, 5):
        shards = [{n for n in _NODEIDS if shard_index(n, count) == i} for i in range(count)]
        union = set().union(*shards)
        assert union == set(_NODEIDS)
        for left in range(count):
            for right in range(left + 1, count):
                assert not (shards[left] & shards[right])


def test_shard_index_is_deterministic() -> None:
    first = {n: shard_index(n, 2) for n in _NODEIDS}
    second = {n: shard_index(n, 2) for n in _NODEIDS}
    assert first == second


def test_shards_are_roughly_balanced() -> None:
    sizes = [sum(1 for n in _NODEIDS if shard_index(n, 2) == i) for i in range(2)]
    assert abs(sizes[0] - sizes[1]) <= len(_NODEIDS) // 10


def _fake_config() -> SimpleNamespace:
    deselected: list = []
    hook = SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    return SimpleNamespace(hook=hook, _deselected=deselected)


def test_filter_keeps_only_own_shard_and_reports_deselected() -> None:
    items = [SimpleNamespace(nodeid=n) for n in _NODEIDS]
    config = _fake_config()

    GateShardFilter(1, 2).pytest_collection_modifyitems(None, config, items)

    assert {i.nodeid for i in items} == {n for n in _NODEIDS if shard_index(n, 2) == 0}
    assert {i.nodeid for i in config._deselected} == {n for n in _NODEIDS if shard_index(n, 2) == 1}


def test_filter_partitions_into_three_shards() -> None:
    kept: list[set[str]] = []
    for index in (1, 2, 3):
        items = [SimpleNamespace(nodeid=n) for n in _NODEIDS]
        GateShardFilter(index, 3).pytest_collection_modifyitems(None, _fake_config(), items)
        kept.append({i.nodeid for i in items})

    assert kept[0] | kept[1] | kept[2] == set(_NODEIDS)
    assert not (kept[0] & kept[1])
    assert not (kept[0] & kept[2])
    assert not (kept[1] & kept[2])


def test_pytest_configure_registers_filter_only_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list = []
    config = SimpleNamespace(
        pluginmanager=SimpleNamespace(register=lambda obj, name: registered.append(name))
    )

    monkeypatch.delenv("GATE_SHARD", raising=False)
    pytest_configure(config)
    assert registered == []

    monkeypatch.setenv("GATE_SHARD", "2/2")
    pytest_configure(config)
    assert registered == ["gate-shard-filter"]


def test_pytest_configure_rejects_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_SHARD", "3/2")
    config = SimpleNamespace(pluginmanager=SimpleNamespace(register=lambda obj, name: None))

    with pytest.raises(pytest.UsageError, match="GATE_SHARD"):
        pytest_configure(config)
