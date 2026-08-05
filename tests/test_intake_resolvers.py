"""Declarative intake resolver registry: phase dispatch and lookup errors."""

from types import SimpleNamespace

import pytest

from server.app.services.job_errors import InvalidOperationError, UnsupportedOperationError
from server.app.services.job_intake_registry import RESOLVERS, ResolverSpec, resolve_candidates
from server.app.services.job_intake_video import resolve_cms_video_candidates


def _mode(key: str = "batch_by_knowledge") -> SimpleNamespace:
    return SimpleNamespace(key=key, label=key, input_field="knowledge_codes")


def test_video_knowledge_resolver_is_node_phase() -> None:
    spec = RESOLVERS[("video", "batch_by_knowledge")]
    assert spec.key == "cms.knowledge_video"
    assert spec.phase == "node"
    assert spec.resource_key == "knowledge_video"


def test_question_cms_resolvers_resolve_at_intake() -> None:
    for mode_key in ("by_knowledge", "batch_by_ids", "batch_by_knowledge"):
        assert RESOLVERS[("question", mode_key)].phase == "intake"


def test_direct_resolvers_have_no_phase() -> None:
    assert RESOLVERS[("video", "batch_by_urls")].phase is None
    assert RESOLVERS[("question", "direct_ids")].phase is None


def test_node_phase_dispatch_builds_candidates_without_cms(monkeypatch) -> None:
    def fail_on_cms(*args, **kwargs):
        raise AssertionError("node-phase intake must not call the CMS")

    monkeypatch.setattr("server.app.cms.knowledge.lookup_knowledge_video", fail_on_cms)
    spec = RESOLVERS[("video", "batch_by_knowledge")]

    candidates = resolve_candidates(
        spec, "video", ["K001", "K001", "K002"], "batch_by_knowledge", _mode(), None, {}, "ws"
    )

    assert [c["entity_id"] for c in candidates] == ["K001", "K002"]
    for c in candidates:
        assert c["source_ref"] == c["entity_id"]
        assert c["source_url"] == ""
        assert c["source_uuid"] == ""


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


def test_unknown_resolver_phase_raises() -> None:
    spec = ResolverSpec("cms.mystery", "video", "sideways", None, lambda *args: [])
    with pytest.raises(InvalidOperationError, match="Unsupported resolver"):
        resolve_candidates(spec, "video", ["K1"], "batch_by_knowledge", _mode(), None, {}, "ws")


def test_unknown_entity_mode_combination_is_not_registered() -> None:
    assert RESOLVERS.get(("audio", "batch_by_knowledge")) is None
    assert RESOLVERS.get(("video", "batch_by_ids")) is None


def test_video_candidates_reject_non_video_entity() -> None:
    with pytest.raises(UnsupportedOperationError):
        resolve_cms_video_candidates("question", ["K1"], "batch_by_knowledge")
