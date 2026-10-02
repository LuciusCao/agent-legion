"""机器可对账的 api-scope 外部闭环端面权威清单（#734）。

外部系统的闭环是「提交 → 轮询 → 下载」：#626 的 workspace API token
（actor_scope='api'）提交 runs、读 run/job 状态，#631 再补三个产物端点。
#626 把准入面手抄成 workspace_access.py 里的 (method, path) 元组，#631
上线时没人记得同步——api token 打产物端点被自己的白名单 404（issue
#734），闭环断在最后一环。手抄清单对「新增即漂移」没有免疫力。

机制化（#678 tool_names.py 同款形态）：路由名（端点函数名）是唯一权威
常量；外部闭环路由模块注册路由时引用同一个 tag（FastAPI 原生路由
metadata），运行期守卫（workspace_api_scope.refuse_off_allowlist_api_
scope，#745 拆自 workspace_access）从请求实际命中的路由对象上按 tag +
名单判定准入——守卫不再维护第二份路径手抄本，路径形态（前缀拼装、
{job_id} 与 jobs/snapshot 的模板遮蔽）天然出局。契约测试（tests/routes/
test_workspace_api_tokens.py，#734 段）对账注册面与权威常量：app 实际
注册面中带 tag 的路由集合 == 权威常量按名字展开，漏挂 tag、漏登记名字
或私挂 tag 到名单外路由，测试必红。

路由名（而非路径模板）作权威键的理由：路径在 include_router 链上被
前缀层层拼装（/api + job_group + 子路由），分散在各处且无单一登记点；
路由名在路由定义处唯一（FastAPI 用它生成 operation id），新增闭环端点
必然新起一个函数名——权威常量按注册站点分组登记路由名，与 #678 按
register_* 模块登记工具名同构。

tag 与名单双条件（AND）而非单 tag：新端点只挂 tag 忘登记名单 → 名单
检查拒绝（fail-closed）；只登记名单忘挂 tag → tag 检查拒绝；两处都改
才能放行，契约测试把「两处一起改」钉成唯一合法路径。
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

# FastAPI 路由 metadata tag：挂在外部闭环路由模块的路由注册上，
# 运行期 workspace_api_scope 的 api-scope 守卫按它从 scope["route"]
# 派生准入面。
API_SCOPE_INTAKE_TAG = "api-scope-intake"

# 唯一权威常量（#678 tool_names.py 形态）：按注册站点分组登记路由名。
# runs.py：提交（POST 是 #626 唯一 effecting 面，require_workspace_api_
# intake 把关）+ run 状态两读。jobs.py：jobs 列表。job_list.py：分页
# snapshot（codex3 P1——机器调用方必须越过 legacy 列表上限读全量 job
# 状态面；同模块的 facets 是前端聚合端点，刻意不收）。external_artifacts.py
# （#631）：job 状态、产物清单、raw 下载——#734 修的就是这三个当初被
# 手抄白名单漏掉的端点。
API_SCOPE_INTAKE_ROUTE_NAMES: frozenset[str] = frozenset(
    {
        # routes/runs.py
        "create_run",
        "list_runs",
        "get_run",
        # routes/jobs.py
        "list_workspace_jobs",
        # routes/job_list.py
        "snapshot_workspace_jobs",
        # routes/external_artifacts.py（#631）
        "get_external_job_status",
        "list_external_artifacts",
        "get_external_artifact_raw",
    }
)


def api_scope_route_allowed(route: Any) -> bool:
    """运行期准入判定：请求实际命中的路由必须同时带 intake tag 且在名单内。

    workspace_api_scope 的 api-scope 守卫在路由匹配之后运行（Starlette
    先解析出 scope["route"] 再进依赖），所以准入检查不需要任何路径手抄：请求带着
    它命中了哪个 APIRoute 进来，未挂 tag 的路由（secrets/materials/
    metrics/jobs-facets…整个 app 的其余表面，无论路径形态多接近）天然
    出局。路由对象缺席（非 APIRoute 的挂载形态）同样拒绝——tag 派生没有
    兜底路径匹配，#734 的教训正是兜底模板会把 {job_id} 遮蔽面放进白名单。
    """
    return (
        isinstance(route, APIRoute)
        and API_SCOPE_INTAKE_TAG in route.tags
        and route.name in API_SCOPE_INTAKE_ROUTE_NAMES
    )
