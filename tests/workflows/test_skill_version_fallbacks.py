from types import SimpleNamespace

from server.app.services.workflow_revision_format import serialize_definition
from server.app.workflows.skill_version_fallbacks import (
    UNAVAILABLE_SKILL_VERSION,
    configured_skill_fallbacks,
    job_node_fallbacks,
)
from tests.helpers import load_builtin_definition


def _job_with_snapshot() -> dict:
    definition = load_builtin_definition("education_video_problems_generation")
    return {"workflow_definition_snapshot_json": serialize_definition(definition)}


def _context_with_skill(capability: str, skill: str) -> dict:
    executor = SimpleNamespace(capabilities={capability: SimpleNamespace(skill=skill)})
    settings = SimpleNamespace(executor_definitions={"local": executor})
    return {"settings": settings}


def test_configured_skill_fallbacks_maps_skill_backed_nodes() -> None:
    definition = load_builtin_definition("education_video_problems_generation")
    node = next(iter(definition.nodes.values()))

    fallbacks = configured_skill_fallbacks(
        _job_with_snapshot(), _context_with_skill(node.capability, "skills/review")
    )

    expected = {
        item.key: "configured:skills/review"
        for item in definition.nodes.values()
        if item.capability == node.capability
    }
    assert expected
    assert fallbacks == expected


def test_configured_skill_fallbacks_skips_nodes_without_skill_mapping() -> None:
    fallbacks = configured_skill_fallbacks(
        _job_with_snapshot(), _context_with_skill("unrelated_capability", "skills/review")
    )

    assert fallbacks == {}


def test_configured_skill_fallbacks_ignores_executors_without_skill() -> None:
    definition = load_builtin_definition("education_video_problems_generation")
    node = next(iter(definition.nodes.values()))

    fallbacks = configured_skill_fallbacks(
        _job_with_snapshot(), _context_with_skill(node.capability, "")
    )

    assert fallbacks == {}


def test_configured_skill_fallbacks_returns_empty_without_snapshot_or_settings() -> None:
    assert configured_skill_fallbacks(None, {}) == {}
    assert configured_skill_fallbacks({"workflow_definition_snapshot_json": "not-json"}, {}) == {}
    assert configured_skill_fallbacks(_job_with_snapshot(), {}) == {}


class _JobDbStub:
    def __init__(self, nodes=None, error: bool = False) -> None:
        self._nodes = nodes or []
        self._error = error

    def list_job_nodes(self, job_id: str):
        if self._error:
            raise RuntimeError("database unavailable")
        return self._nodes


def test_job_node_fallbacks_marks_all_nodes_unavailable() -> None:
    job_db = _JobDbStub(nodes=[{"node_key": "fetch"}, {"node_key": ""}, {"node_key": "review"}])

    fallbacks = job_node_fallbacks("job-1", job_db)

    assert fallbacks == {"fetch": UNAVAILABLE_SKILL_VERSION, "review": UNAVAILABLE_SKILL_VERSION}


def test_job_node_fallbacks_returns_empty_on_database_error() -> None:
    assert job_node_fallbacks("job-1", _JobDbStub(error=True)) == {}
