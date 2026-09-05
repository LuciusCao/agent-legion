"""Static-asset routes for the Worker local control plane (index + /assets).

Split from ``worker/service.py``（#493 review 轮预算腾挪，config_response /
service_tokens 的 router 工厂先例）：静态文件服务是独立关注点——白名单、
media type、no-cache 头与控制 token 的嵌入替换，与配置/重启编排无共享
状态。行为零变化，路由与响应形状照旧。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

# 本地控制面不需要浏览器长缓存：no-cache 强制每次校验，ETag 未变则 304。
# ui_assets 必须覆盖 app.js 的全部静态 import（#493 P1-1：漏一个名字
# asset() 就 404，浏览器中止整个模块图，控制台整页死）——由
# test_ui_assets_cover_app_js_static_imports 钉住求全等。
UI_ASSETS = (
    "app.js",
    "ramp_up.js",
    "styles.css",
    "icons.svg",
    "vendor/uPlot.iife.min.js",
    "vendor/uPlot.min.css",
)
MEDIA_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
NO_CACHE = {"Cache-Control": "no-cache"}


def create_static_router(ui_dir: Path, token: str, *, embed_token: bool) -> APIRouter:
    """Index（控制 token 按需内嵌）+ 白名单 /assets 路由。"""

    router = APIRouter()

    @router.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (ui_dir / "index.html").read_text(encoding="utf-8")
        if embed_token:
            html = html.replace('= "__WORKER_CONTROL_TOKEN__"', f'= "{token}"')
        return HTMLResponse(html, headers=NO_CACHE)

    @router.get("/assets/{name:path}", include_in_schema=False)
    def asset(name: str) -> FileResponse:
        if name not in UI_ASSETS:
            raise HTTPException(status_code=404, detail="asset not found")
        media_type = MEDIA_TYPES[Path(name).suffix]
        return FileResponse(ui_dir / name, media_type=media_type, headers=NO_CACHE)

    return router
