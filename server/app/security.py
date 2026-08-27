# SSRF 守卫的唯一实现在 workspace_libs/url_guard.py（#200：原先与节点 SDK 侧
# 各持一份 "keep in sync" 副本、无 parity 测试）。冗余别名的显式再导出，
# 保持 `from server.app.security import validate_download_url` 不变。
from workspace_libs.url_guard import validate_download_url as validate_download_url

__all__ = ["validate_download_url", "validate_package_filename"]


def validate_package_filename(filename: str) -> str:
    if not filename:
        raise ValueError("Invalid filename: empty")
    if filename.startswith(".") or filename.startswith("-"):
        raise ValueError("Invalid filename: leading dot or hyphen")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("Invalid filename: path traversal characters")
    return filename
