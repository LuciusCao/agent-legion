"""raw 端点的 media-type 白名单（安全边界）。

仅可内嵌渲染的媒体扩展名映射真实 media type；其余（含 .html/.svg——
同源渲染即脚本执行面）一律 application/octet-stream 强制下载。
"""

from __future__ import annotations

RAW_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
}

FALLBACK_MEDIA_TYPE = "application/octet-stream"


def raw_media_type(artifact_name: str) -> str:
    """Map an artifact filename to a servable media type (whitelist-gated)."""
    for suffix, media_type in RAW_MEDIA_TYPES.items():
        if artifact_name.lower().endswith(suffix):
            return media_type
    return FALLBACK_MEDIA_TYPE
