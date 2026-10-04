"""#738 per-token request rate limit at the route level (lean scope).

Proves the guardrail's contract end to end: an over-limit token gets 429 +
Retry-After (and a coalesced structured log keyed by token_id) while a
sibling token and a studio cookie session on the same workspace stay
unaffected; the window recovers on its own; one HTTP request is one debit;
only a verified secret is charged. Helpers mirror the #626 sibling file
(test modules must not import each other).
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from server.app.auth.api_token_limits import ApiTokenLimits, InMemoryApiTokenLimiter
from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "api-token-rate-ws"
_RUNS = f"/api/workspaces/{WORKSPACE}/runs"
_LOGGER = "server.app.auth.api_token_identity"


def _create_workspace(client: TestClient, ws_id: str) -> str:
    response = client.post("/api/workspaces", json={"id": ws_id, "name": ws_id})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, ws_id)
    return ws_id


def _issue(client: TestClient, workspace_id: str, label: str) -> dict:
    created = client.post(f"/api/workspaces/{workspace_id}/api-tokens", json={"label": label})
    assert created.status_code == 201, created.text
    return created.json()


def _bearer_client(client: TestClient, api_token: str) -> TestClient:
    """A cookie-less client with only the Bearer credential set."""
    api_client = client.__class__(client.app)
    api_client.headers["authorization"] = f"Bearer {api_token}"
    return api_client


def _insert_material(client: TestClient, workspace_id: str, material_id: str) -> None:
    with client.app.state.job_db.connect() as conn:
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


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _install_limiter(client: TestClient, monkeypatch, rpm: int, burst: int) -> _Clock:
    # monkeypatch: the client fixture's app can outlive this test, so the
    # tight limiter must be restored afterwards.
    clock = _Clock()
    limiter = InMemoryApiTokenLimiter(
        ApiTokenLimits(requests_per_minute=rpm, burst=burst), monotonic=lambda: clock.now
    )
    monkeypatch.setattr(client.app.state.workspace_api_token_store, "limiter", limiter)
    return clock


def test_app_wires_the_default_limits(client) -> None:
    limiter = client.app.state.workspace_api_token_store.limiter
    assert isinstance(limiter, InMemoryApiTokenLimiter)
    assert limiter.limits == ApiTokenLimits()


def test_token_list_exposes_the_effective_limits_read_only(client, monkeypatch) -> None:
    # #870: the 外部对接 console section shows the bucket a caller runs into;
    # it must be the live limiter's parameters, not a re-read of the env.
    _create_workspace(client, WORKSPACE)
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens")
    assert listed.status_code == 200, listed.text
    assert listed.json()["rate_limit"] == {
        "requests_per_minute": ApiTokenLimits().requests_per_minute,
        "burst": ApiTokenLimits().burst,
    }
    _install_limiter(client, monkeypatch, rpm=7, burst=3)
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()
    assert listed["rate_limit"] == {"requests_per_minute": 7, "burst": 3}


def test_over_limit_token_gets_429_others_unaffected(client, caplog, monkeypatch) -> None:
    _create_workspace(client, WORKSPACE)
    clock = _install_limiter(client, monkeypatch, rpm=6, burst=2)
    noisy = _issue(client, WORKSPACE, "noisy")
    quiet = _issue(client, WORKSPACE, "quiet")
    noisy_api = _bearer_client(client, noisy["api_token"])
    quiet_api = _bearer_client(client, quiet["api_token"])

    assert noisy_api.get(_RUNS).status_code == 200
    assert noisy_api.get(_RUNS).status_code == 200
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        refused = noisy_api.get(_RUNS)
    assert refused.status_code == 429
    # 6/min with an empty bucket: 10 s until the next token.
    assert refused.headers["retry-after"] == "10"
    assert refused.json()["detail"] == "API token rate limit exceeded"
    assert any(f"token_id={noisy['token_id']}" in r.getMessage() for r in caplog.records)

    # A sibling token on the same workspace keeps its own bucket.
    assert quiet_api.get(_RUNS).status_code == 200
    # The studio cookie session is never limited, however hard it polls.
    for _ in range(20):
        assert client.get(_RUNS).status_code == 200

    # Window recovery: once Retry-After elapses the token works again.
    clock.advance(int(refused.headers["retry-after"]))
    assert noisy_api.get(_RUNS).status_code == 200
    assert noisy_api.get(_RUNS).status_code == 429


def test_refusal_log_is_coalesced_per_token(client, caplog, monkeypatch) -> None:
    _create_workspace(client, WORKSPACE)
    _install_limiter(client, monkeypatch, rpm=1, burst=1)
    issued = _issue(client, WORKSPACE, "storm")
    api = _bearer_client(client, issued["api_token"])
    assert api.get(_RUNS).status_code == 200
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(10):
            assert api.get(_RUNS).status_code == 429
    records = [r for r in caplog.records if f"token_id={issued['token_id']}" in r.getMessage()]
    # A refused storm is one warning per minute, not one per request.
    assert len(records) == 1


def test_one_debit_per_request_on_the_submit_path(client, monkeypatch) -> None:
    # POST /runs resolves the identity through two dependencies (the intake
    # guard and the handler's user); with burst 1 it must still pass once.
    _create_workspace(client, WORKSPACE)
    _install_limiter(client, monkeypatch, rpm=1, burst=1)
    _insert_material(client, WORKSPACE, "mat-1")
    api = _bearer_client(client, _issue(client, WORKSPACE, "submit")["api_token"])
    response = api.post(_RUNS, json={"items": [{"type": "material", "material_id": "mat-1"}]})
    assert response.status_code == 200, response.text
    assert api.get(_RUNS).status_code == 429


def test_rate_limit_counts_off_surface_requests(client, monkeypatch) -> None:
    _create_workspace(client, WORKSPACE)
    _install_limiter(client, monkeypatch, rpm=1, burst=1)
    api = _bearer_client(client, _issue(client, WORKSPACE, "probe")["api_token"])
    # Off-surface read: refused by the api-scope guard (404) but still
    # charged — a probing storm is a storm too.
    assert api.get(f"/api/workspaces/{WORKSPACE}/secrets").status_code == 404
    assert api.get(_RUNS).status_code == 429


def test_invalid_secret_does_not_drain_the_real_tokens_bucket(client, monkeypatch) -> None:
    _create_workspace(client, WORKSPACE)
    _install_limiter(client, monkeypatch, rpm=1, burst=1)
    issued = _issue(client, WORKSPACE, "victim")
    forged = _bearer_client(client, f"{issued['token_id']}.not-the-secret")
    for _ in range(5):
        assert forged.get(_RUNS).status_code == 401
    # Knowing the public token_id alone cannot spend the victim's budget.
    assert _bearer_client(client, issued["api_token"]).get(_RUNS).status_code == 200
