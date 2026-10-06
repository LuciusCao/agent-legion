"""Bootstrap is prepare-then-apply in one transaction (#968).

The first admin used to be created by a check-then-insert followed by a
separate login: an interruption between the steps left an admin without a
session (the retry then hit 409 with no defined recovery), and two racing
bootstraps could both pass the "no users yet" check. These tests inject a
failure inside the apply step and race two bootstraps.
"""

from __future__ import annotations

import threading

import pytest

from server.app.auth.service import AuthError

PASSWORD = "first-admin-password"


def _boom(*_args, **_kwargs):
    raise RuntimeError("injected interruption")


def test_interruption_after_user_insert_leaves_nothing_behind(anon_client, monkeypatch) -> None:
    """Fail while writing the first session — i.e. AFTER the user row was
    inserted in the same transaction: the whole bootstrap rolls back."""
    job_db = anon_client.app.state.job_db
    with monkeypatch.context() as patch:
        patch.setattr("server.app.jobs.queries.auth.session_expiry", _boom)
        with pytest.raises(RuntimeError, match="injected interruption"):
            anon_client.post(
                "/api/auth/bootstrap", json={"username": "admin", "password": PASSWORD}
            )
    assert job_db.count_users() == 0
    assert anon_client.get("/api/auth/bootstrap").json() == {"available": True}

    # Safe re-entry: the retry is a clean first run that ends logged in.
    retry = anon_client.post(
        "/api/auth/bootstrap", json={"username": "admin", "password": PASSWORD}
    )
    assert retry.status_code == 200, retry.text
    assert anon_client.get("/api/auth/me").json()["user"]["username"] == "admin"
    assert job_db.count_users() == 1


def test_interruption_before_apply_leaves_nothing_behind(anon_client, monkeypatch) -> None:
    """Fail while preparing (password hashing): no write was attempted."""
    with monkeypatch.context() as patch:
        patch.setattr("server.app.auth.service.hash_password", _boom)
        with pytest.raises(RuntimeError):
            anon_client.post(
                "/api/auth/bootstrap", json={"username": "admin", "password": PASSWORD}
            )
    assert anon_client.app.state.job_db.count_users() == 0
    ok = anon_client.post("/api/auth/bootstrap", json={"username": "admin", "password": PASSWORD})
    assert ok.status_code == 200, ok.text


def test_completed_bootstrap_rerun_is_a_defined_409(anon_client) -> None:
    first = anon_client.post(
        "/api/auth/bootstrap", json={"username": "admin", "password": PASSWORD}
    )
    assert first.status_code == 200
    again = anon_client.post(
        "/api/auth/bootstrap", json={"username": "other", "password": PASSWORD}
    )
    assert again.status_code == 409
    assert anon_client.app.state.job_db.count_users() == 1


def test_concurrent_bootstraps_create_exactly_one_admin(anon_client, monkeypatch) -> None:
    """Both callers pass the pre-check (forced open), then race the apply
    step: the advisory lock + in-transaction re-check admits exactly one."""
    service = anon_client.app.state.auth_service
    monkeypatch.setattr(type(service), "bootstrap_available", lambda _self: True)
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def attempt(username: str) -> None:
        barrier.wait()
        try:
            outcomes.append(service.bootstrap(username, PASSWORD)[1]["username"])
        except AuthError as exc:
            outcomes.append(exc.status_code)

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(map(str, outcomes)) in (["409", "alpha"], ["409", "beta"])
    users = anon_client.app.state.job_db.list_users()
    assert [user["role"] for user in users] == ["admin"]


def test_env_seed_shares_the_atomic_apply(client_factory, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)
    with client_factory(authenticated=False, fresh=True) as seeded:
        assert seeded.get("/api/auth/bootstrap").json() == {"available": False}
        service = seeded.app.state.auth_service
        # Re-running the seed once a user exists is a no-op, not a second admin.
        assert service.seed_bootstrap_admin(PASSWORD) is False
        assert len(seeded.app.state.job_db.list_users()) == 1
