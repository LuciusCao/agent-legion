"""Login brute-force matrix: (account, IP) and account lockout keys (#970).

The limiter used to key on the username alone: one source could lock the
real admin out for everyone. These tests pin the dual-dimension behavior
both at the limiter (fake clock, exact thresholds) and end-to-end through
POST /api/auth/login with distinct transport peers — including the shared
peer address of Docker port mapping / host reverse proxies, where there
must be no instance-wide lockout: there is deliberately no IP-only key.
"""

from __future__ import annotations

import threading
import time

import pytest

from server.app.auth.rate_limit import LoginLockedError, LoginRateLimiter

CSRF = {"x-agent-legion-request": "1"}
ATTACKER = "198.51.100.7"
OWNER = "203.0.113.20"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


def _limiter() -> LoginRateLimiter:
    return LoginRateLimiter(
        max_failures=3, lock_seconds=60, max_account_failures=6, failure_window=120,
    )  # fmt: skip


def _fail(limiter: LoginRateLimiter, username: str, ip: str | None, times: int) -> None:
    for _ in range(times):
        limiter.check(username, ip)
        limiter.record_failure(username, ip)


def _locked(limiter: LoginRateLimiter, username: str, ip: str | None) -> bool:
    try:
        limiter.check(username, ip)
    except LoginLockedError:
        return True
    return False


# --- limiter matrix --------------------------------------------------------


@pytest.mark.no_db
def test_pair_lock_does_not_lock_the_account_owner_elsewhere(clock) -> None:
    limiter = _limiter()
    _fail(limiter, "admin", ATTACKER, 3)
    assert _locked(limiter, "admin", ATTACKER)
    assert not _locked(limiter, "admin", OWNER)


@pytest.mark.no_db
def test_failures_across_accounts_never_lock_other_accounts(clock) -> None:
    """One source (or every client behind one NAT address) failing on many
    usernames locks none of the others — no IP-only key exists."""
    limiter = _limiter()
    for index in range(50):
        _fail(limiter, f"user{index}", ATTACKER, 1)
    assert not _locked(limiter, "admin", ATTACKER)
    # ...while the (account, IP) key still bites per account.
    _fail(limiter, "user0", ATTACKER, 2)
    assert _locked(limiter, "user0", ATTACKER)
    assert not _locked(limiter, "admin", ATTACKER)


@pytest.mark.no_db
def test_distributed_guessing_on_one_account_locks_the_account(clock) -> None:
    limiter = _limiter()
    for index in range(6):
        _fail(limiter, "admin", f"192.0.2.{index}", 1)
    assert _locked(limiter, "admin", OWNER)
    assert not _locked(limiter, "someone-else", OWNER)


@pytest.mark.no_db
def test_concurrent_burst_from_one_source_cannot_lock_the_account(clock) -> None:
    """Login runs check() -> PBKDF2 -> record_failure(), so a burst of
    concurrent wrong passwords from ONE source all pass check() before any
    failure is recorded. Failures landing after that source's pair lock must
    not keep feeding the account key (20 in flight would lock the owner out
    from every other source)."""
    limiter = LoginRateLimiter()  # production thresholds: pair 5, account 20
    in_flight = 20
    checked = threading.Barrier(in_flight)
    errors: list[BaseException] = []

    def wrong_password() -> None:
        try:
            limiter.check("admin", ATTACKER)
            checked.wait(timeout=10)  # every request is past check() (the PBKDF2 gap)
            limiter.record_failure("admin", ATTACKER)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=wrong_password) for _ in range(in_flight)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert _locked(limiter, "admin", ATTACKER)
    assert not _locked(limiter, "admin", OWNER)
    assert limiter._entries[("account", "admin")][0] == 5


@pytest.mark.no_db
def test_single_source_cannot_reach_the_account_threshold(clock) -> None:
    """The account key sits above the pair key: one source is stopped at the
    pair threshold before its failures could lock the account for others."""
    limiter = _limiter()
    _fail(limiter, "admin", ATTACKER, 3)
    with pytest.raises(LoginLockedError):
        _fail(limiter, "admin", ATTACKER, 1)
    assert not _locked(limiter, "admin", OWNER)


@pytest.mark.no_db
def test_success_clears_the_pair_and_account_counters(clock) -> None:
    limiter = _limiter()
    _fail(limiter, "admin", OWNER, 2)
    limiter.record_success("admin", OWNER)
    _fail(limiter, "admin", OWNER, 2)
    assert not _locked(limiter, "admin", OWNER)


@pytest.mark.no_db
def test_failures_outside_the_window_do_not_accumulate(clock) -> None:
    """Sub-threshold failures expire with the window (the old table kept
    unknown-username entries forever)."""
    limiter = _limiter()
    _fail(limiter, "ghost", ATTACKER, 2)
    clock[0] += 121
    _fail(limiter, "ghost", ATTACKER, 2)
    assert not _locked(limiter, "ghost", ATTACKER)
    assert len(limiter._entries) == 2


@pytest.mark.no_db
def test_lock_expires_and_reports_the_longest_wait(clock) -> None:
    limiter = _limiter()
    _fail(limiter, "admin", ATTACKER, 3)
    with pytest.raises(LoginLockedError) as locked:
        limiter.check("admin", ATTACKER)
    assert locked.value.retry_after_seconds == 61
    clock[0] += 61
    assert not _locked(limiter, "admin", ATTACKER)


@pytest.mark.no_db
def test_username_normalization_shares_one_counter(clock) -> None:
    limiter = _limiter()
    _fail(limiter, "Admin", ATTACKER, 1)
    _fail(limiter, " admin ", ATTACKER, 1)
    _fail(limiter, "ADMIN", ATTACKER, 1)
    assert _locked(limiter, "admin", ATTACKER)


@pytest.mark.no_db
def test_table_prunes_stale_entries_at_capacity(clock, monkeypatch) -> None:
    monkeypatch.setattr("server.app.auth.rate_limit._MAX_ENTRIES", 6)
    limiter = LoginRateLimiter(failure_window=10)
    for index in range(3):
        _fail(limiter, f"u{index}", None, 1)
    assert len(limiter._entries) == 6
    clock[0] += 11
    _fail(limiter, "late", None, 1)
    assert len(limiter._entries) == 2


@pytest.mark.no_db
def test_flooding_the_table_never_resets_a_live_count(clock, monkeypatch) -> None:
    """Junk usernames filling the table must not evict an in-progress count:
    4 failures, flood past the cap, then the 5th failure still locks."""
    monkeypatch.setattr("server.app.auth.rate_limit._MAX_ENTRIES", 6)
    limiter = LoginRateLimiter(max_failures=5, max_account_failures=20, failure_window=900)
    _fail(limiter, "admin", ATTACKER, 4)
    for index in range(30):
        clock[0] += 1
        _fail(limiter, f"junk{index}", ATTACKER, 1)
    assert limiter._entries[("pair", "admin", ATTACKER)][0] == 4
    _fail(limiter, "admin", ATTACKER, 1)
    with pytest.raises(LoginLockedError):
        limiter.check("admin", ATTACKER)


@pytest.mark.no_db
def test_sweep_watermark_tracks_the_live_table(clock, monkeypatch) -> None:
    """Over the cap with only live entries, the table keeps growing (bounded
    by request rate x window) and sweeps stay amortized, not per-request."""
    monkeypatch.setattr("server.app.auth.rate_limit._MAX_ENTRIES", 4)
    limiter = LoginRateLimiter(failure_window=900)
    for index in range(10):
        _fail(limiter, f"live{index}", None, 1)
    assert len(limiter._entries) == 20
    assert limiter._sweep_at >= len(limiter._entries)
    clock[0] += 901
    _fail(limiter, "late", None, 1)
    while len(limiter._entries) < limiter._sweep_at:
        _fail(limiter, f"fill{len(limiter._entries)}", None, 1)
    _fail(limiter, "trigger", None, 1)
    assert ("account", "live0") not in limiter._entries


# --- end to end through the login route ----------------------------------


def _peer(client, ip: str):
    return client.__class__(client.app, client=(ip, 40000))


def _login(peer, username: str = "admin", password: str = "wrong", headers=None):
    return peer.post(
        "/api/auth/login", json={"username": username, "password": password}, headers=headers
    )


def test_attacker_lockout_leaves_the_owner_able_to_log_in(client) -> None:
    attacker = _peer(client, ATTACKER)
    for _ in range(5):
        assert _login(attacker).status_code == 401
    assert _login(attacker, password="admin-pw").status_code == 429
    assert _login(_peer(client, OWNER), password="admin-pw").status_code == 200


def test_spoofed_forwarded_for_does_not_open_a_fresh_key(client) -> None:
    """The route keys on the transport peer; a client-supplied
    X-Forwarded-For from an untrusted peer is ignored."""
    attacker = _peer(client, ATTACKER)
    for _ in range(5):
        assert _login(attacker).status_code == 401
    spoofed = _login(attacker, headers={"x-forwarded-for": "192.0.2.99"})
    assert spoofed.status_code == 429


def test_shared_peer_address_has_no_instance_wide_lockout(client) -> None:
    """Docker port mapping / a host reverse proxy can hand every connection
    the same peer address. Failures with arbitrary usernames from that one
    address must not lock out anyone else — including the admin."""
    shared = _peer(client, "172.17.0.1")
    for index in range(40):
        assert _login(shared, username=f"guess{index}").status_code == 401
    assert _login(shared, password="admin-pw").status_code == 200
    # Per-account limiting still holds behind the shared address.
    for _ in range(5):
        assert _login(shared, username="guess0").status_code in (401, 429)
    assert _login(shared, username="guess0").status_code == 429
