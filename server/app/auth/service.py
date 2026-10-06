from __future__ import annotations

from typing import Any

from server.app.auth import scoped_tokens
from server.app.auth.password_policy import WeakPasswordError, validate_new_password
from server.app.auth.passwords import hash_password, verify_password
from server.app.auth.rate_limit import LoginLockedError, LoginRateLimiter
from server.app.auth.sessions import hash_token, issue_token
from server.app.jobs.queries import JobQueries

_BOOTSTRAP_CLOSED = "Bootstrap is only available before the first user exists"


class AuthError(Exception):
    """Domain error carrying the HTTP status the route layer should return."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class InvalidCredentialsError(AuthError):
    def __init__(self) -> None:
        super().__init__("Invalid username or password", status_code=401)


def _new_password_hash(password: str) -> str:
    """Policy-check a NEW password (#970) and hash it; never used on login."""
    if not password:
        raise AuthError("Password is required", 400)
    try:
        validate_new_password(password)
    except WeakPasswordError as exc:
        raise AuthError(str(exc), 400) from exc
    return hash_password(password)


class AuthService:
    """User/session domain logic on top of the auth query mixins."""

    def __init__(self, queries: JobQueries, rate_limiter: LoginRateLimiter | None = None):
        self._queries = queries
        self._rate_limiter = rate_limiter or LoginRateLimiter()

    # --- sessions ----------------------------------------------------------

    def login(
        self, username: str, password: str, client_ip: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Verify credentials and issue a session; returns (token, user).

        ``client_ip`` feeds the (account, IP) and IP lockout keys (#970);
        the route passes the transport peer, never a raw forwarding header.
        """
        try:
            self._rate_limiter.check(username, client_ip)
        except LoginLockedError as exc:
            raise AuthError(str(exc), status_code=429) from exc
        creds = self._queries.get_user_credentials(username)
        if (
            creds is None
            or creds.get("disabled_at") is not None
            or not verify_password(password, creds.get("password_hash"))
        ):
            self._rate_limiter.record_failure(username, client_ip)
            raise InvalidCredentialsError()
        self._rate_limiter.record_success(username, client_ip)
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

    def bootstrap(
        self, username: str, password: str, display_name: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """Create the very first admin and its session; returns (token, user).

        #968: everything is prepared first (policy check, password hash,
        session token) and applied in ONE transaction that re-checks the
        no-users precondition under a lock — an interruption leaves no
        half-initialized state, and a retry is either a clean first run or
        a well-defined 409.
        """
        if not self.bootstrap_available():
            raise AuthError(_BOOTSTRAP_CLOSED, 409)
        password_hash = _new_password_hash(password)
        token = issue_token()
        user = self._queries.bootstrap_first_admin(
            username,
            display_name=display_name,
            password_hash=password_hash,
            session_token_hash=hash_token(token),
        )
        if user is None:
            raise AuthError(_BOOTSTRAP_CLOSED, 409)
        return token, user

    def seed_bootstrap_admin(self, password: str, username: str = "admin") -> bool:
        """Env-seeded first admin for unattended deploys; no-op once users exist.

        A seed password failing the policy (#970) is a startup error: the
        deploy must not come up with a weak admin, nor silently without one.
        """
        if not password or not self.bootstrap_available():
            return False
        try:
            password_hash = _new_password_hash(password)
        except AuthError as exc:
            raise ValueError(f"AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD rejected: {exc}") from exc
        user = self._queries.bootstrap_first_admin(
            username, display_name="Administrator", password_hash=password_hash
        )
        return user is not None

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
        password_hash = _new_password_hash(password)
        try:
            return self._queries.create_user(
                username,
                display_name=display_name,
                password_hash=password_hash,
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
        password_hash = _new_password_hash(password) if password else None
        try:
            return self._queries.update_user(
                user_id,
                display_name=display_name,
                role=role,
                password_hash=password_hash,
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
