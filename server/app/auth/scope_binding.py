"""作用域 token 的 workspace 绑定裁决——单一实现（#971）。

绑定判定此前散在多处（通用 workspace 守卫、job 守卫、studio-agent 工具面、
chat 读面、外部产物面、skill 目录面），各自手写 ``scoped_workspace_id``
比较，且通用守卫与 job 守卫的执行顺序不一致。本模块收口为：

- ``scoped_binding_mismatch``：唯一的判定谓词（绑定非空且 ≠ 目标 workspace）；
- ``refuse_foreign_binding``：workspace 作用域面的统一拒绝（403，detail 固定）。
  判定先于任何 DB 读取，与目标 workspace 是否存在无关，不构成枚举信号；
- ``resolve_job_workspace_scope``：job-id 路由的授权域解析（#710，自
  ``workspace_access`` 迁入）。job-id 面上绑定不符走 404——此处的 workspace
  来自 job 行反查，403 会泄露 job 存在性。

顺序约定（两个成员守卫共用）：api-scope 机器身份臂 → 绑定裁决 → admin
快速通道 → 成员/角色检查。scoped token 继承签发人角色，绑定必须先于
admin 快速通道，否则 admin 签发的绑定 token 不再受绑定约束。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import HTTPException

BOUND_ELSEWHERE_DETAIL = "Scoped token bound to another workspace"


def scoped_binding_mismatch(user: dict[str, Any], workspace_id: str | None) -> bool:
    """True 当且仅当身份带 workspace 绑定且目标 workspace 与之不符。

    无作用域（``workspace_id`` 为空）的路由不在这里裁决——可见性收窄由
    ``workspace_visibility`` 负责；未绑定的身份（完整会话、自助签发的
    unbound token）恒为 False，交给成员检查。
    """
    bound = user.get("scoped_workspace_id")
    return bool(bound) and workspace_id is not None and str(bound) != str(workspace_id)


def refuse_foreign_binding(user: dict[str, Any], workspace_id: str | None) -> None:
    """workspace 作用域面的绑定拒绝：403 + 固定 detail（与 studio-agent
    工具面、chat 读面的既有契约同形）。"""
    if scoped_binding_mismatch(user, workspace_id):
        raise HTTPException(status_code=403, detail=BOUND_ELSEWHERE_DETAIL)


def resolve_job_workspace_scope(request: Request, user: dict[str, Any]) -> str | None:
    """Resolve the workspace a job-id route actually addresses (#710).

    ``job_id`` embeds its workspace (``{workspace_id}_{workflow_key}_{source_id}``)
    but the separator is legal inside workspace ids too, so the scope cannot
    be parsed from the id — it is read from the job row itself (id-only
    projection; jobs rows carry KB-scale TEXT columns and this runs per
    request):

    - bare ``/jobs/{job_id}`` routes: the job's workspace is the scope;
    - ``/workspaces/{workspace_id}/jobs/{job_id}`` routes: the path scope must
      match the job's actual workspace, so one's own workspace prefix cannot
      borrow another workspace's job id (defense in depth ahead of the
      service-level per-item checks).

    Binding: on job-id routes a mismatch is the uniform "Job not found" 404
    (the scope came from the job row; a 403 would confirm the job exists);
    on job-group routes WITHOUT a job id (listings, snapshot) the workspace
    scope comes from the path/query and the shared 403 refusal applies —
    previously those routes skipped the binding entirely (#971).

    Unknown jobs 404 like unknown workspaces — enumeration-safe, and the
    detail text is uniform so the two cases are indistinguishable.
    """
    job_id = request.path_params.get("job_id")
    workspace_id = request.path_params.get("workspace_id") or request.query_params.get(
        "workspace_id"
    )
    workspace_id = str(workspace_id) if workspace_id else None
    if job_id is None:
        refuse_foreign_binding(user, workspace_id)
        return workspace_id
    job_workspace = request.app.state.job_db.get_job_workspace(str(job_id))
    if job_workspace is None or (workspace_id is not None and workspace_id != job_workspace):
        raise HTTPException(status_code=404, detail="Job not found")
    if scoped_binding_mismatch(user, job_workspace):
        raise HTTPException(status_code=404, detail="Job not found")
    return str(job_workspace)
