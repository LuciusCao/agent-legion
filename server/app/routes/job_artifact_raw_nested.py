"""Nested-name raw artifact bytes route (#1178 codex 复审 P2，第 6 轮收口).

产物名可含 ``/``（``reports/final.mp4``——Worker 解包与 promote 都保留
子目录），需要一条能承载多段名的 raw 下载路由。本路由刻意使用与文本
路由完全错开的静态前缀 ``/jobs/{job_id}/raw-artifacts/``：第 5 轮曾把
``/artifacts/{artifact_name:path}/raw`` 改成贪婪后缀形态，结果名为
``x/raw`` 的产物其文本 URL 会被按前缀名 ``x`` 吞掉（路由回归），而
「白名单拒读 ``*/raw`` 名」的补法又会把既有产物重新定义成非法
（对象存储清单是权威副本，不能无迁移地隐藏既有行）——结构性解法
是换无歧义的 URL 形态，既有名字与既有单段后缀路由都不受影响。

服务变体与单段后缀路由同为 ``open_raw_current``（manifest-first，
#1178 codex 复审 P2）：远程 Worker 重跑后对象存储已是新字节而宿主
job_dir 缓存还是旧的，本地优先会让「init 重发 → 面板重取」通道静默
播旧媒体；权威副本在对象存储（EXEC-ARTIFACT-STORE-001），本地文件
只在无 manifest 行的 legacy 形态下使用。
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse

from server.app.routes.job_artifact_raw_response import raw_response
from server.app.services.job_artifacts import JobArtifactService

__all__ = ["register_raw_nested_artifact_route"]


def register_raw_nested_artifact_route(
    router: APIRouter,
    service: JobArtifactService,
) -> None:
    # 路径安全在 service 层（is_downloadable_artifact_name 拒绝 .. 段/
    # 绝对名，resolve_within 做包含性校验——见 job_artifact_names.py），
    # 路由形态不新增穿越面。206 声明与单段后缀路由同一契约（#703：
    # Range 时 raw_response 答分段 206 + Content-Range）。
    @router.get(
        "/jobs/{job_id}/raw-artifacts/{artifact_name:path}",
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
    def get_artifact_raw_nested(
        job_id: str,
        artifact_name: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> FileResponse | StreamingResponse:
        # 授权域与单段后缀路由一致（job_group 按 job 行反查，跨域与未知
        # job 同为 404）；Range 解析在 service 内（本地分支忽略）。
        return raw_response(service.open_raw_current(job_id, artifact_name, range_header))
