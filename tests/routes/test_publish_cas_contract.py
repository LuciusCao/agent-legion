"""#841: every versioned-entity publish entry is CAS-bound (#692 / #749 closure).

Pins the end state of the expected_hash rollout so a new hash-less publish
entry cannot slip back in:

- the store's ``publish`` has no hash-less (None) default — every write is a
  compare-and-swap on the draft's ``definition_hash``;
- every HTTP ``POST .../publish`` route either requires ``expected_hash`` in
  its (required) body, or is listed in ``_NOT_VERSIONED_ENTITY_PUBLISH`` with
  the reason it is not a ``versioned_entities`` publish at all. A new publish
  route fails here until it takes a stance.
"""

from __future__ import annotations

import inspect

from server.app.services.versioned_entities import VersionedEntityStore

# POST .../publish routes that do NOT publish a versioned_entities draft, so
# the draft-hash CAS does not apply. Keep the reason next to each entry.
_NOT_VERSIONED_ENTITY_PUBLISH = {
    # Workflow revision publish (workflow_revisions, not versioned_entities):
    # it validates and publishes the workspace's workflow draft; its
    # concurrency contract is the draft store's expected_updated_at (#633).
    "/api/workspaces/{workspace_id}/workflow-drafts/publish",
}

# versioned_entities publish routes (agent / node_code / preview_panel).
_CAS_PUBLISH_ROUTES = {
    "/api/agent-definitions/{agent_id}/publish",
    "/api/workspaces/{workspace_id}/nodes/{node_key}/code/publish",
    "/api/workspaces/{workspace_id}/preview-panel/publish",
}


def test_store_publish_has_no_hash_less_default() -> None:
    parameter = inspect.signature(VersionedEntityStore.publish).parameters["expected_hash"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation in (str, "str")


def _resolve(schema: dict, node: dict) -> dict:
    ref = node.get("$ref")
    if ref is None:
        return node
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]


def test_every_publish_route_requires_expected_hash(tmp_path) -> None:
    from server.app.main import create_app

    schema = create_app(data_dir=tmp_path, start_worker=False).openapi()
    publish_routes = {
        path
        for path, operations in schema["paths"].items()
        if path.endswith("/publish") and "post" in operations
    }
    unclassified = publish_routes - _CAS_PUBLISH_ROUTES - _NOT_VERSIONED_ENTITY_PUBLISH
    assert not unclassified, (
        "new POST .../publish route(s) without a CAS stance — require expected_hash"
        f" or list them with a reason: {sorted(unclassified)}"
    )
    assert publish_routes >= _CAS_PUBLISH_ROUTES

    for path in sorted(_CAS_PUBLISH_ROUTES):
        body = schema["paths"][path]["post"].get("requestBody")
        assert body is not None and body.get("required") is True, path
        model = _resolve(schema, body["content"]["application/json"]["schema"])
        assert "expected_hash" in model.get("required", []), path
        assert model["properties"]["expected_hash"].get("minLength") == 1, path
