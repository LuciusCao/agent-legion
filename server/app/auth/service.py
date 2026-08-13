from __future__ import annotations

from typing import Any

from server.app.auth import scoped_tokens
from server.app.auth.passwords import hash_password, verify_password
from server.app.auth.rate_limit import LoginLockedError, LoginRateLimiter
from server.app.auth.sessions import hash_token, issue_token
from server.app.jobs.queries import JobQueries


class AuthError(Exception):
    """Domain error carrying the HTTP status the route layer should return."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class InvalidCredentialsError(AuthError):
    def __init__(self) -> None:
        super().__init__("Invalid username or password", status_code=401)


class AuthService:
    """User/session domain logic on top of the auth query mixins."""

    def __init__(self, queries: JobQueries, rate_limiter: LoginRateLimiter | None = None):
        self._queries = queries
        self._rate_limiter = rate_limiter or LoginRateLimiter()

    # --- sessions ----------------------------------------------------------

    def login(self, username: str, password: str) -> tuple[str, dict[str, Any]]:
        """Verify credentials and issue a session; returns (token, user)."""
        try:
            self._rate_limiter.check(username)
        except LoginLockedError as exc:
            raise AuthError(str(exc), status_code=429) from exc
        creds = self._queries.get_user_credentials(username)
        if (
            creds is None
            or creds.get("disabled_at") is not None
            or not verify_password(password, creds.get("password_hash"))
        ):
            self._rate_limiter.record_failure(username)
            raise InvalidCredentialsError()
        self._rate_limiter.record_success(username)
        token = issue_token()
        self._queries.create_session(hash_token(token), str(creds["id"]))
        user = dict(creds)
        user.pop("password_hash", None)
        return token, user

    def logout(self, token: str) -> None:
        self._queries.revoke_session(hash_token(token))

    def authenticate(self, token: str) -> dict[str, Any] | None:
        """Resolve a raw bearer token to its user (sliding expiry), or None."""
        return self._queries.get_session_user(hash_token(token))

    # --- scoped tokens (STUDIO-AGENT-001) ------------------------------------

    def mint_scoped_token(
        self, user_id: str, *, scope: str = scoped_tokens.STUDIO_AGENT_SCOPE
    ) -> str:
        """Mint a short-lived scoped token for a server-side agent run."""
        return scoped_tokens.mint_scoped_token(self._queries, user_id, scope=scope)

    def authenticate_scoped(self, token: str) -> dict[str, Any] | None:
        """Resolve a scoped bearer token to its user plus actor_scope, or None."""
        return scoped_tokens.authenticate_scoped_token(self._queries, token)

    # --- bootstrap -----------------------------------------------------------

    def bootstrap_available(self) -> bool:
        return self._queries.count_users() == 0

    def bootstrap(self, username: str, password: str, display_name: str = "") -> dict[str, Any]:
        """Create the very first admin; only while no users exist."""
        if not self.bootstrap_available():
            raise AuthError("Bootstrap is only available before the first user exists", 409)
        if not password:
            raise AuthError("Password is required", 400)
        return self._queries.create_user(
            username,
            display_name=display_name,
            password_hash=hash_password(password),
            role="admin",
        )

    def seed_bootstrap_admin(self, password: str, username: str = "admin") -> bool:
        """Env-seeded first admin for unattended deploys; no-op once users exist."""
        if not password or not self.bootstrap_available():
            return False
        self._queries.create_user(
            username,
            display_name="Administrator",
            password_hash=hash_password(password),
            role="admin",
        )
        return True

    # --- admin user management ----------------------------------------------

    def list_users(self) -> list[dict[str, Any]]:
        return self._queries.list_users()

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        role: str = "member",
    ) -> dict[str, Any]:
        if not password:
            raise AuthError("Password is required", 400)
        try:
            return self._queries.create_user(
                username,
                display_name=display_name,
                password_hash=hash_password(password),
                role=role,
            )
        except ValueError as exc:
            raise AuthError(str(exc), 400) from exc

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        password: str | None = None,
        disabled: bool | None = None,
    ) -> dict[str, Any]:
        try:
            return self._queries.update_user(
                user_id,
                display_name=display_name,
                role=role,
                password_hash=hash_password(password) if password else None,
                disabled=disabled,
            )
        except ValueError as exc:
            raise AuthError(str(exc), 404 if "not found" in str(exc).lower() else 400) from exc

    # --- workspace membership --------------------------------------------------

    def list_workspace_members(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._queries.list_workspace_members(workspace_id)

    def set_workspace_member(self, workspace_id: str, user_id: str, role: str) -> None:
        try:
            self._queries.upsert_workspace_member(workspace_id, user_id, role)
        except ValueError as exc:
            raise AuthError(str(exc), 404 if "not found" in str(exc).lower() else 400) from exc

    def remove_workspace_member(self, workspace_id: str, user_id: str) -> None:
        try:
            self._queries.delete_workspace_member(workspace_id, user_id)
        except ValueError as exc:
            raise AuthError(str(exc), 404) from exc


def build_auth_service(queries: JobQueries, config: dict[str, Any]) -> AuthService:
    """Compose the AuthService and env-seed the first admin when configured."""
    service = AuthService(queries)
    auth_config = config.get("auth", {})
    password = (
        str(auth_config.get("bootstrap_admin_password", ""))
        if isinstance(auth_config, dict)
        else ""
    )
    if password:
        service.seed_bootstrap_admin(password)
    return service
