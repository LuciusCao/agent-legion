from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException

from ..auth.dependencies import SESSION_COOKIE, extract_session_token, require_admin, require_user
from ..auth.service import AuthError, AuthService
from ..auth.sessions import SESSION_TTL
from .auth_contracts import (
    BootstrapRequest,
    BootstrapStatusResponse,
    LoginRequest,
    LoginResponse,
    MemberPutRequest,
    MemberResponse,
    MembersResponse,
    MeResponse,
    UserCreateRequest,
    UserPatchRequest,
    UserResponse,
    UsersResponse,
)

_COOKIE_MAX_AGE = int(SESSION_TTL.total_seconds())


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def create_auth_router(auth_service: AuthService) -> APIRouter:
    """Session lifecycle plus admin user/member management."""
    router = APIRouter(tags=["auth"])
    router.include_router(_build_session_router(auth_service))
    router.include_router(_build_users_router(auth_service))
    return router


def _build_session_router(auth_service: AuthService) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post("/login", response_model=LoginResponse)
    def login(payload: LoginRequest, response: Response) -> LoginResponse:
        try:
            token, user = auth_service.login(payload.username, payload.password)
        except AuthError as exc:
            raise _auth_error(exc) from exc
        _set_session_cookie(response, token)
        return LoginResponse(user=UserResponse(**user))

    @router.post("/logout", response_model=MeResponse)
    def logout(
        request: Request,
        response: Response,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> MeResponse:
        token, _ = extract_session_token(request)
        if token is not None:
            auth_service.logout(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return MeResponse(user=UserResponse(**user))

    @router.get("/me", response_model=MeResponse)
    def me(user: Annotated[dict[str, Any], Depends(require_user)]) -> MeResponse:
        return MeResponse(user=UserResponse(**user))

    @router.get("/bootstrap", response_model=BootstrapStatusResponse)
    def bootstrap_status() -> BootstrapStatusResponse:
        return BootstrapStatusResponse(available=auth_service.bootstrap_available())

    @router.post("/bootstrap", response_model=LoginResponse)
    def bootstrap(payload: BootstrapRequest, response: Response) -> LoginResponse:
        try:
            user = auth_service.bootstrap(payload.username, payload.password, payload.display_name)
            token, _ = auth_service.login(payload.username, payload.password)
        except AuthError as exc:
            raise _auth_error(exc) from exc
        _set_session_cookie(response, token)
        return LoginResponse(user=UserResponse(**user))

    return router


def _build_users_router(auth_service: AuthService) -> APIRouter:
    """Admin-only user management and workspace membership."""
    router = APIRouter(tags=["users"], dependencies=[Depends(require_admin)])

    @router.get("/users", response_model=UsersResponse)
    def list_users() -> UsersResponse:
        return UsersResponse(users=[UserResponse(**u) for u in auth_service.list_users()])

    @router.post("/users", response_model=UserResponse, status_code=201)
    def create_user(payload: UserCreateRequest) -> UserResponse:
        try:
            user = auth_service.create_user(
                payload.username, payload.password, payload.display_name, payload.role
            )
        except AuthError as exc:
            raise _auth_error(exc) from exc
        return UserResponse(**user)

    @router.patch("/users/{user_id}", response_model=UserResponse)
    def update_user(user_id: str, payload: UserPatchRequest) -> UserResponse:
        try:
            user = auth_service.update_user(
                user_id,
                display_name=payload.display_name,
                role=payload.role,
                password=payload.password,
                disabled=payload.disabled,
            )
        except AuthError as exc:
            raise _auth_error(exc) from exc
        return UserResponse(**user)

    @router.get("/workspaces/{workspace_id}/members", response_model=MembersResponse)
    def list_members(workspace_id: str) -> MembersResponse:
        return MembersResponse(
            members=[MemberResponse(**m) for m in auth_service.list_workspace_members(workspace_id)]
        )

    @router.put("/workspaces/{workspace_id}/members", response_model=MembersResponse)
    def put_member(workspace_id: str, payload: MemberPutRequest) -> MembersResponse:
        try:
            auth_service.set_workspace_member(workspace_id, payload.user_id, payload.role)
        except AuthError as exc:
            raise _auth_error(exc) from exc
        return list_members(workspace_id)

    @router.delete("/workspaces/{workspace_id}/members/{user_id}", response_model=MembersResponse)
    def delete_member(workspace_id: str, user_id: str) -> MembersResponse:
        try:
            auth_service.remove_workspace_member(workspace_id, user_id)
        except AuthError as exc:
            raise _auth_error(exc) from exc
        return list_members(workspace_id)

    return router
