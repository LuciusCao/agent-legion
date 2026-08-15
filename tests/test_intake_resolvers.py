"""Declarative intake resolver registry: phase dispatch and lookup errors."""

from types import SimpleNamespace

import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_registry import RESOLVERS, ResolverSpec, resolve_candidates


def _mode(key: str = "direct_ids") -> SimpleNamespace:
    return SimpleNamespace(key=key, label=key, input_field="ids")


def test_direct_resolvers_have_no_phase() -> None:
    assert RESOLVERS[("video", "batch_by_urls")].phase is None
    assert RESOLVERS[("video", "direct_ids")].phase is None
    assert RESOLVERS[("question", "direct_ids")].phase is None


def test_direct_phase_dispatch_builds_url_candidates() -> None:
    spec = RESOLVERS[("video", "batch_by_urls")]

    candidates = resolve_candidates(
        spec,
        "video",
        ["https://example.invalid/v.mp4"],
        "batch_by_urls",
        _mode("batch_by_urls"),
        None,
        {},
        "ws",
    )

    assert candidates[0]["source_url"] == "https://example.invalid/v.mp4"
    assert candidates[0]["entity_id"] == "https://example.invalid/v.mp4"


def test_direct_phase_dispatch_builds_id_candidates() -> None:
    spec = RESOLVERS[("question", "direct_ids")]

    candidates = resolve_candidates(
        spec, "question", ["Q1", "Q2"], "direct_ids", _mode(), None, {}, "ws"
    )

    assert [c["entity_id"] for c in candidates] == ["Q1", "Q2"]


def test_unknown_resolver_phase_raises() -> None:
    spec = ResolverSpec("mystery", "video", "sideways", None, lambda *args: [])
    with pytest.raises(InvalidOperationError, match="Unsupported resolver"):
        resolve_candidates(spec, "video", ["K1"], "direct_ids", _mode(), None, {}, "ws")


def test_unknown_entity_mode_combination_is_not_registered() -> None:
    assert RESOLVERS.get(("audio", "batch_by_knowledge")) is None
    assert RESOLVERS.get(("video", "batch_by_ids")) is None
    assert RESOLVERS.get(("question", "by_knowledge")) is None
