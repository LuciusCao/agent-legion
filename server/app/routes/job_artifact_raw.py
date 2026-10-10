"""Raw artifact bytes serving route registration.

Split from routes/job_artifacts.py for the architecture file budget; the
route registration follows the register_*_route pattern (see
package_clear_packed.py) so the router file stays at its baseline. The
response builders live in ``job_artifact_raw_response`` (same split).
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse

from server.app.routes.job_artifact_raw_response import raw_response
from server.app.services.job_artifact_media import raw_media_type
from server.app.services.job_artifacts import JobArtifactService
from server.app.settings import Settings

__all__ = ["raw_media_type", "raw_response", "register_raw_artifact_route"]


def register_raw_artifact_route(
    router: APIRouter,
    service: JobArtifactService,
    settings: Settings,
) -> None:
    # raw 必须先于 {artifact_name:path} 注册（在 job_artifacts.py 的
    # create_job_artifacts_router 里调用），否则 "foo.json/raw" 会被吞成
    # 名为 "foo.json/raw" 的 artifact 查询。
    # {artifact_name:path}（#1178 codex 复审 P2，与外部 raw 路由同修）：
    # 声明产物名可含 /（reports/final.mp4——Worker 解包与 promote 都保留
    # 子目录），单段参数连 URL 编码的斜杠也匹配不上。/raw 后缀在 :path
    # 贪婪匹配下取自尾部（fastapi 的 path convertor 按后缀截断），名字里
    # 的 / 不再破坏匹配；路径安全在 service 层（open_raw →
    # is_downloadable_artifact_name 拒绝 .. 段/绝对名，resolve_within 做
    # 包含性校验——见 job_artifact_names.py），路由形态不新增穿越面。
    # 206 声明（#703 codex round 4 P2-2，与外部 raw 路由同修）：Range 时
    # raw_response 答分段 206 + Content-Range，契约只写 200 会让生成客
    # 户端把分段下载当异常。
    @router.get(
        "/jobs/{job_id}/artifacts/{artifact_name:path}/raw",
        response_class=FileResponse,
        response_model=None,
        responses={
            200: {"content": {"application/octet-stream": {}}},
            206: {
                "description": "Partial Content (Range request)",
                "content": {"application/octet-stream": {}},
                "headers": {
                    "Content-Range": {"schema": {"type": "string"}},
                    "Content-Length": {"schema": {"type": "string"}},
                },
            },
        },
    )
    def get_artifact_raw(
        job_id: str,
        artifact_name: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> FileResponse | StreamingResponse:
        # Legacy bare route：scoped/成员/admin 语义由 job_group 的
        # require_job_workspace_access 统一裁决（#745 按 job 行反查授权域；
        # 见 jobs.py get_job 的注释——跨域与未知 job 同为 404，Range 行为
        # 不变）。
        # Range 解析在 service.open_raw 内（本地分支忽略，FileResponse 原生支持）。
        return raw_response(service.open_raw(job_id, artifact_name, range_header))
