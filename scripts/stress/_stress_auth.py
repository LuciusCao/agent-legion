"""Deterministic admin session for stress tooling (test architecture plan, Phase 4C).

Workspace APIs require an authenticated session (server/app/auth/dependencies.py);
the stress runner and simulator share this bootstrap-or-login helper so they can
call readiness, SSE and stress-event endpoints against a fresh or reused database.
The session also carries the CSRF header required for cookie-authenticated
mutations (e.g. POST /workspaces/{id}/events/stress).
"""

from __future__ import annotations

import requests

SESSION_COOKIE_NAME = "agent_legion_session"
_CSRF_HEADERS = {"x-agent-legion-request": "1"}
_STRESS_ADMIN = {
    "username": "stress-admin",
    "password": "stress-admin-password-1",
    "display_name": "Stress Admin",
}


def ensure_admin_session(base_url: str, timeout: float = 10.0) -> requests.Session:
    """Return a session logged in as the deterministic stress admin.

    Bootstraps the first admin when the database is fresh, otherwise logs in
    with the shared credentials (mirrors frontend/e2e/helpers.ts).
    """
    session = requests.Session()
    session.headers.update(_CSRF_HEADERS)
    base = base_url.rstrip("/")
    status = session.get(f"{base}/api/auth/bootstrap", timeout=timeout).json()
    if status.get("available"):
        response = session.post(
            f"{base}/api/auth/bootstrap",
            json={
                "username": _STRESS_ADMIN["username"],
                "password": _STRESS_ADMIN["password"],
                "display_name": _STRESS_ADMIN["display_name"],
            },
            timeout=timeout,
        )
    else:
        response = session.post(
            f"{base}/api/auth/login",
            json={
                "username": _STRESS_ADMIN["username"],
                "password": _STRESS_ADMIN["password"],
            },
            timeout=timeout,
        )
    response.raise_for_status()
    return session


def session_cookie(session: requests.Session) -> str:
    """Extract the session cookie value for handoff to the browser context."""
    return session.cookies.get(SESSION_COOKIE_NAME) or ""
